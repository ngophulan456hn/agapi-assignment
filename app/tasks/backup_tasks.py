"""
backup_tasks.py
---------------
Celery task that creates a daily PostgreSQL dump using pg_dump.

Schedule: every day at 02:00 UTC (configured via Celery Beat in celery_app.py).

Output:
  <BACKUP_DIR>/backup_<YYYY-MM-DD_HH-MM-SS>.dump
  (custom-format dump, restorable with pg_restore)

Retention:
  Files older than BACKUP_RETENTION_DAYS (default 7) are deleted automatically
  at the end of each backup run.

Requirements:
  • pg_dump must be available on the PATH (install postgresql-client in Docker).
"""

import glob
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

BACKUP_RETENTION_DAYS: int = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))


@celery_app.task(
    name="backup.backup_database",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def backup_database(self) -> dict:
    """
    Dump the PostgreSQL database to a file in the configured backup directory.
    Old dumps beyond the retention window are pruned afterwards.
    """
    backup_dir = settings.BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"backup_{timestamp}.dump"
    filepath = os.path.join(backup_dir, filename)

    env = os.environ.copy()
    env["PGPASSWORD"] = settings.POSTGRES_PASSWORD

    cmd = [
        "pg_dump",
        "--host",
        settings.POSTGRES_HOST,
        "--port",
        str(settings.POSTGRES_PORT),
        "--username",
        settings.POSTGRES_USER,
        "--dbname",
        settings.POSTGRES_DB,
        "--format",
        "custom",  # compressed, supports selective restore
        "--no-password",
        "--file",
        filepath,
    ]

    logger.info("Starting database backup → %s", filepath)
    try:
        result = subprocess.run(
            cmd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,  # 10-minute hard limit
        )
    except subprocess.CalledProcessError as exc:
        logger.error("pg_dump failed (exit %d): %s", exc.returncode, exc.stderr.strip())
        raise self.retry(exc=exc)
    except subprocess.TimeoutExpired as exc:
        logger.error("pg_dump timed out after 600 s")
        raise self.retry(exc=exc)

    file_size = os.path.getsize(filepath)
    logger.info("Backup complete: %s (%.1f KB)", filepath, file_size / 1024)

    # ── Prune old backups ──────────────────────────────────────────────
    pruned = _prune_old_backups(backup_dir, BACKUP_RETENTION_DAYS)
    if pruned:
        logger.info("Pruned %d old backup(s): %s", len(pruned), pruned)

    if result.stderr:
        logger.warning("pg_dump stderr: %s", result.stderr.strip())

    return {
        "status": "ok",
        "file": filepath,
        "size_bytes": file_size,
        "pruned": pruned,
    }


def _prune_old_backups(backup_dir: str, retention_days: int) -> list[str]:
    """Delete .dump files older than *retention_days* and return their paths."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed: list[str] = []

    for path in glob.glob(os.path.join(backup_dir, "backup_*.dump")):
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        if mtime < cutoff:
            try:
                os.remove(path)
                removed.append(path)
            except OSError as exc:
                logger.warning("Could not remove old backup %s: %s", path, exc)

    return removed
