import os
import logging

from src.test_media.queue import _queue
from src.test_media.slice import slice_file
from src.worker_base import Worker

logger = logging.getLogger(__name__)

MEDIA_TEST_WORKERS = int(os.getenv("MEDIA_TEST_WORKERS", "1"))


def _execute(path: str, meta: dict, job_id: int, dry_run: bool):
    slice_file(
        source_path=meta["source_path"],
        output_path=meta["output_path"],
        start_sec=meta["start_sec"],
        duration_sec=meta.get("duration_sec", 30),
        dry_run=dry_run,
        job_id=job_id,
    )


_worker = Worker(
    name="media-test-worker",
    queue=_queue,
    execute_fn=_execute,
    worker_count=MEDIA_TEST_WORKERS,
)


def start():
    _worker.start()
