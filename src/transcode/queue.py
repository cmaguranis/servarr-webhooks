import os
import logging

from src import config
from src.queue import JobQueue

logger = logging.getLogger(__name__)

_q = JobQueue(
    db_path=os.getenv("TRANSCODE_DB", "/config/data/transcode_queue.db"),
    table="transcode_jobs",
)


def init_db():
    _q.init_db()


def enqueue_job(path: str, meta: dict) -> int | None:
    return _q.enqueue_job(path, meta)


def claim_pending_jobs(limit: int = 10) -> list:
    return _q.claim_pending_jobs(limit)


def mark_done(job_id: int):
    _q.mark_done(job_id)


def mark_failed(job_id: int):
    _q.mark_failed(job_id)


def list_jobs(status: str | None = None) -> list:
    return _q.list_jobs(status)


def requeue_job(job_id: int, dry_run: bool = False) -> bool:
    return _q.requeue_job(job_id, dry_run)


def clear_jobs(status: str) -> int:
    return _q.clear_jobs(status)


def cleanup_jobs():
    done_days = int(config.get("transcode", "cleanup_done_days", fallback="7"))
    failed_days = int(config.get("transcode", "cleanup_failed_days", fallback="21"))
    _q.cleanup_jobs(done_days, failed_days)
