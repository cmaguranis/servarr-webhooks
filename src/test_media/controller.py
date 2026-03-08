import json
import logging
import os
import random

from flask import Blueprint, request

from src.test_media.queue import enqueue_job, list_jobs
from src.test_media.slice import build_output_path, get_duration, get_media_signature

logger = logging.getLogger(__name__)

bp = Blueprint("test_media", __name__)

MEDIA_TEST_CACHE_DIR  = os.getenv("MEDIA_TEST_CACHE_DIR",  "/data/media_cache")
MEDIA_TEST_OUTPUT_DIR = os.getenv("MEDIA_TEST_OUTPUT_DIR", "/data/media_test")
MEDIA_DIR             = os.getenv("MEDIA_DIR",             "/media")
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts"}
SLICE_DURATION    = 30  # seconds
MIN_FILE_DURATION = SLICE_DURATION + 5  # 35s safety buffer


def _collect_media_files(scan_dirs: list[str]) -> list[str]:
    files = []
    for root_dir in scan_dirs:
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in MEDIA_EXTENSIONS:
                    files.append(os.path.join(dirpath, fname))
    return files


@bp.route("/media-test/generate", methods=["POST"])
def generate():
    dry_run       = request.args.get("dry_run",        "").lower() == "true"
    include_media = request.args.get("include_media",  "").lower() == "true"

    if not os.path.isdir(MEDIA_TEST_CACHE_DIR):
        return ({"error": f"Cache directory does not exist: {MEDIA_TEST_CACHE_DIR}"}, 400)

    scan_dirs = [MEDIA_TEST_CACHE_DIR]
    if include_media:
        if not os.path.isdir(MEDIA_DIR):
            return ({"error": f"Media directory does not exist: {MEDIA_DIR}"}, 400)
        scan_dirs.append(MEDIA_DIR)
        logger.info(f"include_media=true: scanning {scan_dirs}")

    # Collect all media files recursively from all scan dirs
    all_files = _collect_media_files(scan_dirs)

    if not all_files:
        for d in scan_dirs:
            logger.info(f"No media files found in {d}")
        return ({"dry_run": dry_run, "enqueued": [], "skipped": 0}, 202)

    logger.info(f"Found {len(all_files)} media files across {scan_dirs}")

    # Select one file per unique codec signature — maps signature tuple → (path, sig)
    seen: dict[tuple, str] = {}  # sig_tuple → path
    probe_errors = 0
    for path in sorted(all_files):
        try:
            sig = get_media_signature(path)
        except Exception as e:
            logger.warning(f"Skipping {path}: ffprobe failed — {e}")
            probe_errors += 1
            continue
        if sig not in seen:
            seen[sig] = path

    # Build list of (sig_tuple, path) for processing
    selected = list(seen.items())
    logger.info(
        f"Found {len(selected)} distinct codec signatures from {len(all_files)} files"
        + (f" ({probe_errors} probe errors skipped)" if probe_errors else "")
    )

    enqueued = []
    skipped  = 0

    for sig, path in selected:
        try:
            duration = get_duration(path)
        except Exception as e:
            logger.warning(f"Skipping {path}: ffprobe failed — {e}")
            continue

        if duration < MIN_FILE_DURATION:
            logger.warning(
                f"Skipping {path}: duration {duration:.1f}s < {MIN_FILE_DURATION}s"
            )
            continue

        start_sec   = random.randint(0, int(duration) - SLICE_DURATION - 1)
        output_path = build_output_path(path, start_sec, MEDIA_TEST_OUTPUT_DIR)

        meta = {
            "source_path": path,
            "output_path":  output_path,
            "start_sec":    start_sec,
            "duration_sec": SLICE_DURATION,
            "dry_run":      dry_run,
        }

        if dry_run:
            enqueued.append({
                "job_id":    None,
                "source":    path,
                "output":    output_path,
                "start_sec": start_sec,
                "signature": list(sig),
            })
        else:
            job_id = enqueue_job(output_path, meta)
            if job_id is None:
                skipped += 1
            else:
                enqueued.append({
                    "job_id":    job_id,
                    "source":    path,
                    "output":    output_path,
                    "start_sec": start_sec,
                    "signature": list(sig),
                })

    logger.info(
        f"generate complete: {len(enqueued)} enqueued, {skipped} skipped"
        + (" (dry_run)" if dry_run else "")
    )
    return ({"dry_run": dry_run, "enqueued": enqueued, "skipped": skipped}, 202)


@bp.route("/media-test/jobs", methods=["GET"])
def get_jobs():
    status = request.args.get("status")
    jobs = list_jobs(status)
    for job in jobs:
        if isinstance(job.get("meta"), str):
            try:
                job["meta"] = json.loads(job["meta"])
            except (json.JSONDecodeError, TypeError):
                pass
    return ({"jobs": jobs}, 200)
