import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
from typing import Callable

from src.queue import JobQueue

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10  # seconds


class Worker:
    def __init__(
        self,
        name: str,
        queue: JobQueue,
        execute_fn: Callable,
        on_complete: Callable | None = None,
        cleanup_fn: Callable | None = None,
        worker_count: int = 1,
    ):
        """
        name:        Thread name for logging
        queue:       A JobQueue instance
        execute_fn:  callable(path, meta, job_id, dry_run) — raises on failure
        on_complete: optional callable(job_id, meta) — called after non-dry-run success
        cleanup_fn:  optional callable() — called daily for job cleanup; defaults to queue.cleanup_jobs()
        worker_count: Number of concurrent worker threads
        """
        self._name = name
        self._queue = queue
        self._execute_fn = execute_fn
        self._on_complete = on_complete
        self._cleanup_fn = cleanup_fn or queue.cleanup_jobs
        self._worker_count = worker_count
        self._stop_flag = threading.Event()
        self._executor: ThreadPoolExecutor | None = None

    def _run(self, job: dict):
        meta = json.loads(job.get("meta") or "{}")
        path = job["path"]
        job_id = job["id"]
        dry_run = meta.get("dry_run", False)

        logger.info(f"[job {job_id}] Starting: {path} (dry_run={dry_run})")
        try:
            self._execute_fn(path, meta, job_id, dry_run)

            if dry_run:
                self._queue.requeue_job(job_id, dry_run=False)
                logger.info(f"[job {job_id}] Dry run complete, requeued: {path}")
            else:
                self._queue.mark_done(job_id)
                logger.info(f"[job {job_id}] Done: {path}")
                if self._on_complete:
                    self._on_complete(job_id, meta)

        except Exception as e:
            self._queue.mark_failed(job_id)
            logger.error(f"[job {job_id}] Failed: {path} — {e}", exc_info=True)

    def _loop(self):
        last_cleanup = 0.0
        while not self._stop_flag.is_set():
            try:
                jobs = self._queue.claim_pending_jobs(limit=self._worker_count)
                if jobs:
                    futures = [self._executor.submit(self._run, job) for job in jobs]
                    wait(futures, return_when=ALL_COMPLETED)

                if time.time() - last_cleanup > 86400:
                    self._cleanup_fn()
                    last_cleanup = time.time()
            except Exception as e:
                logger.error(f"[{self._name}] Worker loop error: {e}", exc_info=True)

            self._stop_flag.wait(timeout=POLL_INTERVAL)

    def start(self):
        self._executor = ThreadPoolExecutor(max_workers=self._worker_count)

        def _sigterm_handler(signum, frame):
            logger.info(f"[{self._name}] SIGTERM received — draining worker...")
            self._stop_flag.set()
            if self._executor:
                self._executor.shutdown(wait=True, cancel_futures=False)
            logger.info(f"[{self._name}] Worker drained")

        signal.signal(signal.SIGTERM, _sigterm_handler)

        t = threading.Thread(target=self._loop, daemon=True, name=self._name)
        t.start()
        logger.info(f"[{self._name}] Worker started ({self._worker_count} slots)")
