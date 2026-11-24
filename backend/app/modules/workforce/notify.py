"""Notification service: in-app records, per-user preferences, duplicate prevention, delivery log, providers.

Notifications are written in the caller's transaction, so nobody is told about a change that rolled back.
Email is sent afterwards by the worker (`deliver_pending`) with retries. Push and WhatsApp have provider
interfaces only: they report "not connected" and never pretend a message was delivered.
"""
from __future__ import annotations

import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.models import DeliveryStatus, Notification, NotificationDelivery, NotificationPreference, User

log = logging.getLogger("above.notify")

WORKFORCE_NOTIFICATION_TYPES: dict[str, str] = {
    "leave_submitted": "Leave request waiting for your decision",
    "leave_decided": "Your leave request was approved or rejected",
    "leave_cancelled": "A leave request was cancelled",
    "overtime_available": "New overtime opportunity",
    "overtime_confirmed": "Your overtime shift is confirmed",
    "overtime_filled": "An overtime opportunity was filled",
    "overtime_expired": "An overtime opportunity expired unfilled",
    "overtime_cancelled": "An overtime opportunity was cancelled",
    "overtime_withdrawn": "An employee withdrew from an overtime shift",
    "shift_changed": "Your shift assignment changed",
    "workforce_conflict": "A scheduling conflict needs a decision",
    "attendance_correction": "Attendance correction requests and decisions",
    "overtime_review": "Overtime records waiting for approval",
}

MAX_EMAIL_ATTEMPTS = 5


# ------------------------------------------------------------------ providers
@dataclass
class ChannelStatus:
    channel: str
    provider: str
    connected: bool
    detail: str


class EmailProvider:
    name = "smtp"

    def status(self) -> ChannelStatus:
        s = get_settings()
        if s.mail_backend == "smtp" and s.smtp_host:
            return ChannelStatus("email", "smtp", True, f"SMTP server {s.smtp_host}:{s.smtp_port}")
        if s.mail_backend == "console":
            return ChannelStatus("email", "console", False, "Console backend: emails are written to the server log, not sent")
        return ChannelStatus("email", "disabled", False, "Email is disabled")

    def send(self, to: str, subject: str, body: str) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.mail_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)


class NotConnectedProvider:
    """Placeholder for push / WhatsApp until a real provider is configured."""

    def __init__(self, channel: str):
        self.channel = channel

    def status(self) -> ChannelStatus:
        return ChannelStatus(self.channel, "none", False, "Not connected — no provider configured")


EMAIL = EmailProvider()
PUSH = NotConnectedProvider("push")
WHATSAPP = NotConnectedProvider("whatsapp")


def channel_statuses() -> list[dict[str, Any]]:
    rows = [ChannelStatus("in_app", "in_app", True, "Notifications appear in the bell and notification history"),
            EMAIL.status(), PUSH.status(), WHATSAPP.status()]
    return [r.__dict__ for r in rows]


def _deliverable_email(user: User) -> bool:
    return bool(user.email) and not user.email.lower().endswith(".invalid")


# ------------------------------------------------------------------ service
def notify(db: Session, user_ids: list[uuid.UUID] | set[uuid.UUID], type_: str, title: str, body: str,
           data: dict[str, Any] | None = None, dedupe_key: str | None = None) -> dict[uuid.UUID, uuid.UUID]:
    """Create one notification per user (respecting preferences) plus a delivery record per channel.

    `dedupe_key` makes the call idempotent per user: the same key never produces a second notification.
    Returns {user_id: notification_id} for notifications actually created.
    """
    settings = get_settings()
    created: dict[uuid.UUID, uuid.UUID] = {}
    for uid in dict.fromkeys(user_ids):  # stable de-duplication
        user = db.get(User, uid)
        if user is None or not user.is_active:
            continue
        pref = db.get(NotificationPreference, (uid, type_))
        in_app = True if pref is None else pref.in_app
        email = bool(pref and pref.email)
        if not in_app and not email:
            continue
        key = f"{dedupe_key}:{uid}" if dedupe_key else None
        values = dict(id=uuid.uuid4(), user_id=uid, type=type_, title=title[:200], body=body, data=data, dedupe_key=key,
                      created_at=utcnow())
        stmt = pg_insert(Notification).values(**values)
        if key:
            stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"], index_where=text("dedupe_key IS NOT NULL"))
        nid = db.execute(stmt.returning(Notification.id)).scalar_one_or_none()
        if nid is None:
            continue  # duplicate suppressed
        created[uid] = nid
        if in_app:
            db.add(NotificationDelivery(notification_id=nid, channel="in_app", provider="in_app", status=DeliveryStatus.DELIVERED,
                                        attempts=1, sent_at=utcnow()))
        if email:
            if not _deliverable_email(user):
                db.add(NotificationDelivery(notification_id=nid, channel="email", provider=None, status=DeliveryStatus.SKIPPED,
                                            last_error="No deliverable email address on the user"))
            elif settings.mail_backend == "smtp" and settings.smtp_host:
                db.add(NotificationDelivery(notification_id=nid, channel="email", provider="smtp", status=DeliveryStatus.PENDING,
                                            next_attempt_at=utcnow()))
            elif settings.mail_backend == "console":
                log.info("EMAIL (console backend, not sent) to=%s subject=%s", user.email, title)
                db.add(NotificationDelivery(notification_id=nid, channel="email", provider="console", status=DeliveryStatus.LOGGED,
                                            attempts=1, sent_at=utcnow()))
            else:
                db.add(NotificationDelivery(notification_id=nid, channel="email", provider=None, status=DeliveryStatus.SKIPPED,
                                            last_error="Email is disabled"))
        for flag, provider in ((pref and pref.push, PUSH), (pref and pref.whatsapp, WHATSAPP)):
            if flag:
                db.add(NotificationDelivery(notification_id=nid, channel=provider.channel, provider=None,
                                            status=DeliveryStatus.SKIPPED, last_error="Provider not connected"))
    db.flush()
    return created


def deliver_pending(db: Session, limit: int = 50) -> dict[str, int]:
    """Send due email deliveries; failed attempts back off exponentially, up to MAX_EMAIL_ATTEMPTS."""
    now = utcnow()
    rows = db.execute(select(NotificationDelivery).where(
        NotificationDelivery.status == DeliveryStatus.PENDING, NotificationDelivery.channel == "email",
        NotificationDelivery.next_attempt_at <= now).order_by(NotificationDelivery.next_attempt_at)
        .limit(limit).with_for_update(skip_locked=True)).scalars().all()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    for d in rows:
        n = db.get(Notification, d.notification_id)
        user = db.get(User, n.user_id) if n else None
        d.attempts += 1
        try:
            if n is None or user is None:
                raise RuntimeError("Notification or user no longer exists")
            EMAIL.send(user.email, n.title, n.body or n.title)
            d.status, d.sent_at, d.last_error = DeliveryStatus.SENT, utcnow(), None
            stats["sent"] += 1
        except Exception as exc:  # network / SMTP errors are expected and retried
            d.last_error = str(exc)[:500]
            if d.attempts >= MAX_EMAIL_ATTEMPTS:
                d.status = DeliveryStatus.FAILED
                stats["failed"] += 1
            else:
                d.next_attempt_at = utcnow() + timedelta(minutes=2 ** d.attempts)
                stats["retry"] += 1
    return stats
