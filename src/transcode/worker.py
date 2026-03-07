import os
import time
import json
import signal
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

from src import radarr_service, sonarr_service
from src.transcode.queue import claim_pending_jobs, mark_done, mark_failed, cleanup_jobs
from src.transcode.ffmpeg import transcode_file

logger = logging.getLogger(__name__)

TRANSCODE_WORKERS = int(os.getenv("TRANSCODE_WORKERS", "1"))
POLL_INTERVAL = 10  # seconds

_stop_flag = threading.Event()
_executor: ThreadPoolExecutor | None = None


def _run(job: dict):
    meta = json.loads(job.get("meta") or "{}")
    path = job["path"]
    job_id = job["id"]
    dry_run = meta.get("dry_run", False)

    try:
        transcode_file(
            path,
            codec=meta.get("codec"),
            bitrate_kbps=meta.get("bitrate_kbps"),
            orig_lang=meta.get("orig_lang"),
            has_51=meta.get("has_51"),
            dry_run=dry_run,
        )

        arr_id = meta.get("arr_id")
        arr_type = meta.get("arr_type")
        if arr_type and arr_id and not dry_run:
            svc = radarr_service if arr_type == "radarr" else sonarr_service
            svc.add_tag(arr_id, "transcoded")
            if arr_type == "radarr":
                radarr_service.rescan_movie(arr_id)
            else:
                sonarr_service.rescan_series(arr_id)

        mark_done(job_id)
        logger.info(f"Job {job_id} done: {path}")

    except Exception as e:
        mark_failed(job_id)
        logger.error(f"Job {job_id} failed: {path} — {e}")


def _loop():
    last_cleanup = 0.0
    while not _stop_flag.is_set():
        jobs = claim_pending_jobs(limit=TRANSCODE_WORKERS)
        if jobs:
            futures = [_executor.submit(_run, job) for job in jobs]
            wait(futures, return_when=ALL_COMPLETED)

        if time.time() - last_cleanup > 86400:
            cleanup_jobs()
            last_cleanup = time.time()

        _stop_flag.wait(timeout=POLL_INTERVAL)


def start():
    global _executor
    _executor = ThreadPoolExecutor(max_workers=TRANSCODE_WORKERS)

    def _sigterm_handler(signum, frame):
        logger.info("SIGTERM received — draining worker...")
        _stop_flag.set()
        if _executor:
            _executor.shutdown(wait=True, cancel_futures=False)
        logger.info("Worker drained")

    signal.signal(signal.SIGTERM, _sigterm_handler)

    t = threading.Thread(target=_loop, daemon=True, name="transcode-worker")
    t.start()
    logger.info(f"Transcode worker started ({TRANSCODE_WORKERS} slots)")
