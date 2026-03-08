import os

from src.queue import JobQueue

_q = JobQueue(
    db_path=os.getenv("MEDIA_TEST_DB", "/config/data/media_test_queue.db"),
    table="media_test_jobs",
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


def get_job_by_path(path: str) -> dict | None:
    return _q.get_job_by_path(path)


def clear_jobs(status: str) -> int:
    return _q.clear_jobs(status)


def cleanup_jobs():
    _q.cleanup_jobs()
