from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "agapi",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_BACKEND_URL,
    include=[
        "app.tasks.flash_sale_tasks",
        "app.tasks.backup_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Prevent task results from piling up indefinitely
    result_expires=3600,
    # ── Periodic task schedule (requires celery beat) ─────────────────
    beat_schedule={
        "daily-database-backup": {
            "task": "backup.backup_database",
            # Runs every day at 02:00 UTC
            "schedule": crontab(hour=2, minute=0),
        },
    },
)
