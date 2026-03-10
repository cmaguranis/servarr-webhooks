from src import config
from src.queue import QueueModule

_queue: QueueModule | None = None


def _q() -> QueueModule:
    if _queue is None:
        raise RuntimeError("plex_queue.init_db() must be called before use")
    return _queue


def init_db(db_path: str | None = None):
    global _queue
    path = db_path or config.PLEX_CLEANUP_DB()
    _queue = QueueModule(db_path=path, table="plex_cleanup_jobs")
    _queue.init_db()


def enqueue_job(plex_key: int, meta: dict) -> int | None:
    return _q().enqueue_job(str(plex_key), meta)


def claim_pending_jobs(limit: int = 10) -> list:
    return _q().claim_pending_jobs(limit)


def mark_done(job_id: int, result: str | None = None):
    _q().mark_done(job_id, result)


def mark_failed(job_id: int, error: str | None = None):
    _q().mark_failed(job_id, error)


def list_jobs(status: str | None = None) -> list:
    return _q().list_jobs(status)


def requeue_job(job_id: int, dry_run: bool = False) -> bool:
    return _q().requeue_job(job_id, dry_run)


def clear_jobs(status: str) -> int:
    return _q().clear_jobs(status)


def cleanup_jobs(done_days: int = 7, failed_days: int = 21):
    _q().cleanup_jobs(done_days, failed_days)
