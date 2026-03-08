import os
import random
import logging

from src import radarr_service, sonarr_service
from src.test_media.slice import build_output_path
from src.transcode.queue import _q, cleanup_jobs
from src.transcode.encode import transcode_file
from src.transcode.probe import get_stream_info
from src.worker_base import Worker

MEDIA_TEST_OUTPUT_DIR = os.getenv("MEDIA_TEST_OUTPUT_DIR", "/data/media_test")
_SLICE_DURATION = 30

logger = logging.getLogger(__name__)

TRANSCODE_WORKERS = int(os.getenv("TRANSCODE_WORKERS", "1"))


def _execute(path: str, meta: dict, job_id: int, dry_run: bool):
    output_path = None
    start_sec = None
    slice_duration = None

    if meta.get("media_test"):
        slice_duration = meta.get("slice_duration") or _SLICE_DURATION

        info = get_stream_info(path)
        duration = float(info.get("format", {}).get("duration") or 0)
        start_sec = meta.get("start_sec")
        if start_sec is None:
            max_start = int(duration) - slice_duration - 1
            start_sec = random.randint(0, max(0, max_start))
        output_path = build_output_path(path, start_sec, MEDIA_TEST_OUTPUT_DIR)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        logger.info(f"[job {job_id}] media_test mode: slicing {slice_duration}s from {start_sec}s → {output_path}")

    transcode_file(
        path,
        codec=meta.get("codec"),
        bitrate_kbps=meta.get("bitrate_kbps"),
        orig_lang=meta.get("orig_lang"),
        has_51=meta.get("has_51"),
        dry_run=dry_run,
        job_id=job_id,
        output_path=output_path,
        start_sec=start_sec,
        slice_duration=slice_duration,
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
