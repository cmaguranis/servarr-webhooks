import logging
import os
import random
from pathlib import Path

from src import config, radarr_service, sonarr_service
from src.test_media.slice import build_output_path
from src.transcode import schedule
from src.transcode.encode import transcode_file
from src.transcode.probe import extract_probe_summary, get_stream_info
from src.transcode.queue import _queue, cleanup_jobs
from src.worker_base import SkipJobError, Worker

_SLICE_DURATION = 120

logger = logging.getLogger(__name__)

TRANSCODE_WORKERS = config.TRANSCODE_WORKER_COUNT()


def _execute(path: str, meta: dict, job_id: int, dry_run: bool):
    output_path = None
    start_sec = None
    slice_duration = None

    if meta.get("full"):
        p = Path(path)
        output_path = os.path.join(config.TEST_MEDIA_OUTPUT_DIR(), f"{p.parent.name}__{p.name}")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        logger.info(f"[job {job_id}] full movie mode → {output_path}")
    elif meta.get("output_path"):
        output_path = meta["output_path"]
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        logger.info(f"[job {job_id}] output_path override → {output_path}")
    elif meta.get("media_test"):
        slice_duration = meta.get("slice_duration") or _SLICE_DURATION

        info = get_stream_info(path)
        duration = float(info.get("format", {}).get("duration") or 0)
        start_sec = meta.get("start_sec")
        if start_sec is None:
            max_start = int(duration) - slice_duration - 1
            start_sec = random.randint(0, max(0, max_start))
        output_path = build_output_path(path, start_sec, config.TEST_MEDIA_OUTPUT_DIR())
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        logger.info(f"[job {job_id}] media_test mode: slicing {slice_duration}s from {start_sec}s → {output_path}")

    arr_id = meta.get("arr_id")
    arr_type = meta.get("arr_type")
    if arr_id and arr_type and not meta.get("media_test"):
        svc = radarr_service if arr_type == "radarr" else sonarr_service
        try:
            if svc.has_pending_queue_item(arr_id):
                raise SkipJobError("upgrade actively downloading — skipping transcode")
            quality_profile_id = meta.get("quality_profile_id")
            current_quality_id = meta.get("current_quality_id")
            if quality_profile_id and current_quality_id:
                if not svc.is_cutoff_met(arr_id, quality_profile_id, current_quality_id):
                    raise SkipJobError("cutoff not met — skipping transcode until upgraded")
        except SkipJobError:
            raise
        except Exception as e:
            logger.warning(f"[job {job_id}] Could not check upgrade status: {e}")

    cmd_str = transcode_file(
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

    if cmd_str:
        out_probe = None
        if not dry_run:
            dest = output_path or path
            try:
                out_probe = extract_probe_summary(get_stream_info(dest))
            except Exception as e:
                logger.warning(f"[job {job_id}] Could not probe output: {e}")
        _queue.update_result(job_id, ffmpeg_cmd=cmd_str, output_probe=out_probe)


def _post_transcode(job_id: int, meta: dict):
    arr_id = meta.get("arr_id")
    arr_type = meta.get("arr_type")
    if not (arr_type and arr_id):
        return
    try:
        if arr_type == "radarr":
            radarr_service.rescan_movie(arr_id)
        else:
            sonarr_service.rescan_series(arr_id)
    except Exception as e:
        logger.warning(f"[job {job_id}] Post-transcode Arr update failed: {e}")


_worker = Worker(
    name="transcode-worker",
    queue=_queue,
    execute_fn=_execute,
    on_complete=_post_transcode,
    cleanup_fn=cleanup_jobs,
    worker_count=TRANSCODE_WORKERS,
    paused_fn=lambda: not schedule.is_enabled(),
    lock_path_fn=lambda path, meta: (
        f"{meta['arr_type']}:{meta['arr_id']}"
        if meta.get('arr_type') and meta.get('arr_id')
        else path
    ),
)


def start():
    _worker.start()
