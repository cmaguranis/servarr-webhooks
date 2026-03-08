import os
import time
import json
import signal
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

from src import radarr_service, sonarr_service
from src.transcode.queue import claim_pending_jobs, mark_done, mark_failed, cleanup_jobs, requeue_job
from src.transcode.ffmpeg import transcode_file

logger = logging.getLogger(__name__)

TRANSCODE_WORKERS = int(os.getenv("TRANSCODE_WORKERS", "1"))
POLL_INTERVAL = 10  # seconds

_stop_flag = threading.Event()
_executor: ThreadPoolExecutor | None = None


def _enrich_meta(meta: dict, job_id: int) -> dict:
    """Fetch accurate bitrate from the Arr API if the webhook payload had none.
    Radarr/Sonarr report videoBitrate in bps; divide by 1000 for kbps."""
    if meta.get("bitrate_kbps"):
        return meta
    arr_type = meta.get("arr_type")
    arr_file_id = meta.get("arr_file_id")
    if not arr_type or not arr_file_id:
        return meta
    try:
        if arr_type == "radarr":
            file_data = radarr_service.get_movie_file(arr_file_id)
        else:
            file_data = sonarr_service.get_episode_file(arr_file_id)
        bitrate_bps = (file_data or {}).get("mediaInfo", {}).get("videoBitrate") or 0
        if bitrate_bps:
            meta = {**meta, "bitrate_kbps": bitrate_bps // 1000}
            logger.info(f"[job {job_id}] Enriched bitrate from API: {meta['bitrate_kbps']} kbps")
    except Exception as e:
        logger.warning(f"[job {job_id}] Could not fetch media info from API: {e}")
    return meta


def _run(job: dict):
    meta = json.loads(job.get("meta") or "{}")
    path = job["path"]
    job_id = job["id"]
    dry_run = meta.get("dry_run", False)

    meta = _enrich_meta(meta, job_id)
    logger.info(f"[job {job_id}] Starting: {path} (dry_run={dry_run})")
    try:
        transcode_file(
            path,
            codec=meta.get("codec"),
            bitrate_kbps=meta.get("bitrate_kbps"),
            orig_lang=meta.get("orig_lang"),
            has_51=meta.get("has_51"),
            dry_run=dry_run,
            job_id=job_id,
        )

        if dry_run:
            requeue_job(job_id, dry_run=False)
            logger.info(f"[job {job_id}] Dry run complete, requeued for real transcode: {path}")
        else:
            mark_done(job_id)
            logger.info(f"[job {job_id}] Done: {path}")

        arr_id = meta.get("arr_id")
        arr_type = meta.get("arr_type")
        if arr_type and arr_id and not dry_run:
            try:
                svc = radarr_service if arr_type == "radarr" else sonarr_service
                svc.add_tag(arr_id, "transcoded")
                if arr_type == "radarr":
                    radarr_service.rescan_movie(arr_id)
                else:
                    sonarr_service.rescan_series(arr_id)
            except Exception as e:
                logger.warning(f"Job {job_id} done but post-transcode Arr update failed: {e}")

    except Exception as e:
        mark_failed(job_id)
        logger.error(f"Job {job_id} failed: {path} — {e}")


def _loop():
    last_cleanup = 0.0
    while not _stop_flag.is_set():
        try:
            jobs = claim_pending_jobs(limit=TRANSCODE_WORKERS)
            if jobs:
                futures = [_executor.submit(_run, job) for job in jobs]
                wait(futures, return_when=ALL_COMPLETED)

            if time.time() - last_cleanup > 86400:
                cleanup_jobs()
                last_cleanup = time.time()
        except Exception as e:
            logger.error(f"Worker loop error: {e}", exc_info=True)

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
