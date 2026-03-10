"""Flask blueprint for the test media generation API.

Scans the media cache for representative files, slices 30-second test clips,
and exposes job queue management endpoints.
"""

import logging
import os
import random

from flask import Blueprint, request

from src import config, radarr_service, sonarr_service
from src.lang import parse_lang
from src.media_extensions import MEDIA_EXTENSIONS
from src.test_media.queue import enqueue_job
from src.test_media.slice import build_output_path, get_duration, get_media_signature
from src.job_routes import register_job_routes
from src.test_media import queue as test_media_queue

logger = logging.getLogger(__name__)

bp = Blueprint("test_media", __name__)
register_job_routes(bp, test_media_queue, "/media-test")

MEDIA_TEST_CACHE_DIR  = config.TEST_MEDIA_CACHE_DIR()
MEDIA_TEST_OUTPUT_DIR = config.TEST_MEDIA_OUTPUT_DIR()
MEDIA_DIR             = config.TEST_MEDIA_MEDIA_DIR()
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


def _build_arr_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (radarr_path_movie_map, sonarr_path_episode_map), each best-effort."""
    radarr_map: dict[str, dict] = {}
    sonarr_map: dict[str, dict] = {}
    try:
        radarr_map = radarr_service.get_path_movie_map()
        logger.info(f"generate: loaded {len(radarr_map)} Radarr path→movie entries")
    except Exception as e:
        logger.warning(f"generate: Radarr lookup unavailable — {e}")
    try:
        sonarr_map = sonarr_service.get_path_episode_map()
        logger.info(f"generate: loaded {len(sonarr_map)} Sonarr path→episode entries")
    except Exception as e:
        logger.warning(f"generate: Sonarr lookup unavailable — {e}")
    return radarr_map, sonarr_map


def _arr_meta_for_path(path: str, radarr_map: dict, sonarr_map: dict) -> dict:
    """Return arr_type, arr_id, orig_lang, arr_data for a source file path."""
    movie = radarr_map.get(path)
    if movie:
        return {
            "arr_type": "radarr",
            "arr_id":   movie["id"],
            "orig_lang": parse_lang((movie.get("originalLanguage") or {}).get("name")),
            "arr_data":  movie,
        }
    episode = sonarr_map.get(path)
    if episode:
        series = episode["series"]
        return {
            "arr_type": "sonarr",
            "arr_id":   series["id"],
            "orig_lang": parse_lang((series.get("originalLanguage") or {}).get("name")),
            "arr_data":  episode,
        }
    return {"arr_type": None, "arr_id": None, "orig_lang": None, "arr_data": None}


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
        return ({"dry_run": dry_run, "enqueued": [], "skipped": []}, 202)

    logger.info(f"Found {len(all_files)} media files across {scan_dirs}")

    # Build Arr path→metadata maps once (best-effort; graceful if Arr is unreachable)
    radarr_map, sonarr_map = _build_arr_maps()

    # Select one file per unique codec signature — maps signature tuple → path
    seen: dict[tuple, str] = {}
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

    selected = list(seen.items())
    logger.info(
        f"Found {len(selected)} distinct codec signatures from {len(all_files)} files"
        + (f" ({probe_errors} probe errors skipped)" if probe_errors else "")
    )

    enqueued = []
    skipped: list[str] = []

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
        arr         = _arr_meta_for_path(path, radarr_map, sonarr_map)

        meta = {
            "source_path": path,
            "output_path": output_path,
            "start_sec":   start_sec,
            "duration_sec": SLICE_DURATION,
            "dry_run":     dry_run,
            "arr_type":    arr["arr_type"],
            "arr_id":      arr["arr_id"],
            "orig_lang":   arr["orig_lang"],
            "arr_data":    arr["arr_data"],
        }

        if dry_run:
            enqueued.append({
                "job_id":    None,
                "source":    path,
                "output":    output_path,
                "start_sec": start_sec,
                "signature": list(sig),
                "arr_type":  arr["arr_type"],
                "orig_lang": arr["orig_lang"],
            })
        else:
            job_id = enqueue_job(output_path, meta)
            if job_id is None:
                skipped.append(output_path)
            else:
                enqueued.append({
                    "job_id":    job_id,
                    "source":    path,
                    "output":    output_path,
                    "start_sec": start_sec,
                    "signature": list(sig),
                    "arr_type":  arr["arr_type"],
                    "orig_lang": arr["orig_lang"],
                })

    logger.info(
        f"generate complete: {len(enqueued)} enqueued, {len(skipped)} skipped"
        + (" (dry_run)" if dry_run else "")
    )
    return ({"dry_run": dry_run, "enqueued": enqueued, "skipped": skipped}, 202)


