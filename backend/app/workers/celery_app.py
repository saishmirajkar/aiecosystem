"""Celery application: background and scheduled work (Redis broker)."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("above", broker=settings.redis_url, backend=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"], timezone="UTC", enable_utc=True,
    task_acks_late=True, worker_prefetch_multiplier=1, result_expires=3600,
    beat_schedule={
        "inventory-alert-digest": {"task": "app.workers.tasks.generate_alert_notifications", "schedule": crontab(hour=1, minute=30)},
        "expire-stale-approvals": {"task": "app.workers.tasks.expire_approvals", "schedule": crontab(minute=5)},
        "workforce-tick": {"task": "app.workers.tasks.workforce_tick", "schedule": 60.0},
        "workforce-attendance-close": {"task": "app.workers.tasks.workforce_attendance_close", "schedule": crontab(minute="*/15")},
        "deliver-notifications": {"task": "app.workers.tasks.deliver_notifications", "schedule": 60.0},
    },
)
