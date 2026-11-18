"""Scheduled tasks. Each task opens its own session and commits once."""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.deps import build_actor
from app.models import Notification, NotificationPreference, User
from app.modules.inventory.alerts import compute_alerts
from app.orchestrator import approval_manager
from app.workers.celery_app import celery_app

log = logging.getLogger("above.workers")


def run_alert_digest(db) -> int:
    """Create one in-app digest per user per day, honouring notification preferences and warehouse scope."""
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, timezone.utc)
    created = 0
    for user in db.execute(select(User).where(User.is_active.is_(True))).scalars():
        actor = build_actor(db, user)
        if not actor.has("inventory.view"):
            continue
        prefs = {p.alert_type: p for p in db.execute(select(NotificationPreference).where(NotificationPreference.user_id == user.id)).scalars()}
        wanted = {t for t in ("low_stock", "critical_stock", "overstock", "expiring_stock", "pending_receipt", "unconfirmed_transfer",
                              "stock_discrepancy", "inactive_stock") if prefs.get(t) is None or prefs[t].in_app}
        if not wanted:
            continue
        exists = db.execute(select(Notification.id).where(Notification.user_id == user.id, Notification.type == "alert_digest",
                                                          Notification.created_at >= today_start)).first()
        if exists:
            continue
        alerts = compute_alerts(db, actor.warehouse_ids, wanted)
        if not alerts:
            continue
        counts: dict[str, int] = {}
        for a in alerts:
            counts[a["type"]] = counts.get(a["type"], 0) + 1
        db.add(Notification(user_id=user.id, type="alert_digest", title=f"{len(alerts)} inventory alert(s) need attention",
                            body="; ".join(f"{n} {t.replace('_', ' ')}" for t, n in counts.items()),
                            data={"counts": counts, "top": [a["title"] for a in alerts[:5]]}))
        emails = [t for t, p in prefs.items() if p.email]
        if emails:
            log.info("Email digest for %s queued for types %s (SMTP integration pending; console backend)", user.email, emails)
        created += 1
    return created


@celery_app.task(name="app.workers.tasks.generate_alert_notifications")
def generate_alert_notifications() -> int:
    with SessionLocal() as db:
        n = run_alert_digest(db)
        db.commit()
    log.info("Alert digests created: %s", n)
    return n


@celery_app.task(name="app.workers.tasks.expire_approvals")
def expire_approvals() -> int:
    with SessionLocal() as db:
        n = approval_manager.expire_stale(db)
        db.commit()
    return n
