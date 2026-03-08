import os
import logging

from src import radarr_service, sonarr_service
from src.transcode.queue import _q, cleanup_jobs
from src.transcode.encode import transcode_file
from src.worker_base import Worker

logger = logging.getLogger(__name__)

TRANSCODE_WORKERS = int(os.getenv("TRANSCODE_WORKERS", "1"))


def _execute(path: str, meta: dict, job_id: int, dry_run: bool):
    transcode_file(
        path,
        codec=meta.get("codec"),
        bitrate_kbps=meta.get("bitrate_kbps"),
        orig_lang=meta.get("orig_lang"),
        has_51=meta.get("has_51"),
        dry_run=dry_run,
        job_id=job_id,
    )


def _post_transcode(job_id: int, meta: dict):
    arr_id = meta.get("arr_id")
    arr_type = meta.get("arr_type")
    if not (arr_type and arr_id):
        return
    try:
        svc = radarr_service if arr_type == "radarr" else sonarr_service
        svc.add_tag(arr_id, "transcoded")
        if arr_type == "radarr":
            radarr_service.rescan_movie(arr_id)
        else:
            sonarr_service.rescan_series(arr_id)
    except Exception as e:
        logger.warning(f"[job {job_id}] Post-transcode Arr update failed: {e}")


_worker = Worker(
    name="transcode-worker",
    queue=_q,
    execute_fn=_execute,
    on_complete=_post_transcode,
    cleanup_fn=cleanup_jobs,
    worker_count=TRANSCODE_WORKERS,
)


def start():
    _worker.start()
