from src import config
from src.queue import QueueModule

_queue = QueueModule(
    db_path=config.MEDIA_TEST_DB(),
    table="media_test_jobs",
)


def init_db():
    _queue.init_db()


def enqueue_job(path: str, meta: dict) -> int | None:
    return _queue.enqueue_job(path, meta)


# Module-level bindings (backward compat — controller and worker import these by name)
claim_pending_jobs = _queue.claim_pending_jobs
mark_done          = _queue.mark_done
mark_failed        = _queue.mark_failed
list_jobs          = _queue.list_jobs
requeue_job        = _queue.requeue_job
get_job_by_path    = _queue.get_job_by_path
clear_jobs         = _queue.clear_jobs
cleanup_jobs       = _queue.cleanup_jobs
