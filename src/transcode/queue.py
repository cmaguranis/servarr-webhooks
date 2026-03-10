import logging

from src import config
from src.queue import QueueModule
from src.transcode.encode import video_transcode_needed

logger = logging.getLogger(__name__)

_queue = QueueModule(
    db_path=config.TRANSCODE_DB(),
    table="transcode_jobs",
)


def init_db():
    _queue.init_db()


def _compute_priority(meta: dict) -> int:
    return 1 if video_transcode_needed(meta.get("codec"), meta.get("bitrate_kbps")) else 2


def enqueue_job(path: str, meta: dict) -> int | None:
    return _queue.enqueue_job(path, meta, _compute_priority(meta))


def cleanup_jobs():
    _queue.cleanup_jobs(config.TRANSCODE_CLEANUP_DONE_DAYS(), config.TRANSCODE_CLEANUP_FAILED_DAYS())


# Module-level bindings (backward compat — controller imports these by name)
claim_pending_jobs = _queue.claim_pending_jobs
mark_done          = _queue.mark_done
mark_failed        = _queue.mark_failed
list_jobs          = _queue.list_jobs
requeue_job        = _queue.requeue_job
clear_jobs         = _queue.clear_jobs
