"""Flask blueprint for the transcode webhook and job management API.

Receives Radarr/Sonarr download webhooks, enqueues HEVC transcode jobs, and
exposes job queue and schedule management endpoints.
"""

import json
import logging
import os
from flask import Blueprint, request

from src import config, radarr_service, sonarr_service
from src.lang import parse_lang as _parse_lang
from src.media_extensions import MEDIA_EXTENSIONS
from src.test_media.queue import get_job_by_path as get_media_test_job
from src.transcode.queue import enqueue_job
from src.transcode.probe import get_stream_info
from src.transcode import schedule
from src.job_routes import register_job_routes, register_schedule_routes
from src.transcode import queue as transcode_queue

logger = logging.getLogger(__name__)

bp = Blueprint("transcode", __name__)
register_job_routes(bp, transcode_queue, "/transcode")
register_schedule_routes(bp, schedule, "/transcode")


@bp.route("/transcode-webhook", methods=["POST"])
def transcode_webhook():
    payload = request.get_json(silent=True) or {}

    if payload.get("eventType") != "Download":
        return ("", 200)

    is_radarr = "movieFile" in payload
    file_info = payload.get("movieFile") or payload.get("episodeFile")
    if not file_info:
        return ("", 200)

    media_obj = payload.get("movie") or payload.get("series") or {}
    media_info = file_info.get("mediaInfo") or {}
    arr_type = "radarr" if is_radarr else "sonarr"

    # Skip trusted release groups (hot-reloaded from config.ini)
    skip_groups = set(config.TRANSCODE_SKIP_GROUPS())
    release_group = (file_info.get("releaseGroup") or "").strip()
    if release_group.lower() in skip_groups:
        logger.info(f"Skipping trusted group '{release_group}': {file_info.get('path')}")
        return ("", 200)

    media_test = request.args.get("media_test", "").lower() == "true"

    # Skip if already tagged as transcoded (bypass in media_test mode)
    if not media_test:
        svc = radarr_service if is_radarr else sonarr_service
        try:
            transcoded_tag_id = svc.get_or_create_tag("transcoded")
            if transcoded_tag_id in (media_obj.get("tags") or []):
                logger.info(f"Skipping already-transcoded: {file_info.get('path')}")
                return ("", 200)
        except Exception as e:
            logger.warning(f"Could not check 'transcoded' tag — proceeding anyway: {e}")

    orig_lang = _parse_lang(
        (media_obj.get("originalLanguage") or {}).get("name")
        or media_info.get("audioLanguages", "")
    )

    start_sec_raw = request.args.get("start_sec")
    slice_duration_raw = request.args.get("slice_duration")
    meta = {
        "codec": media_info.get("videoCodec"),
        "bitrate_kbps": media_info.get("videoBitrate"),
        "orig_lang": orig_lang,
        "has_51": (media_info.get("audioChannels") or 0) > 5,
        "arr_type": arr_type,
        "arr_id": media_obj.get("id"),
        "dry_run": request.args.get("dry_run", "").lower() == "true",
        "media_test": media_test,
        "start_sec": int(start_sec_raw) if start_sec_raw else None,
        "slice_duration": int(slice_duration_raw) if slice_duration_raw else None,
    }

    enqueue_job(file_info["path"], meta)
    return ("", 202)



@bp.route("/transcode/enqueue-folder", methods=["POST"])
def enqueue_folder():
    """Scan a folder for media files and enqueue transcode jobs for each.

    Body (JSON): {"path": "/data/media_test"}
    Query params: ?dry_run=true
    """
    dry_run = request.args.get("dry_run", "").lower() == "true"
    body = request.get_json(silent=True) or {}
    folder = (body.get("path") or "").strip()

    if not folder:
        return ({"error": "Missing required field: path"}, 400)
    if not os.path.isdir(folder):
        return ({"error": f"Directory does not exist: {folder}"}, 400)

    # Build path → orig_lang map from Arr APIs as fallback for non-clip files
    arr_lang_map: dict[str, str] = {}
    try:
        arr_lang_map.update(radarr_service.get_path_lang_map())
        logger.info(f"enqueue-folder: loaded {len(arr_lang_map)} Radarr path→lang entries")
    except Exception as e:
        logger.warning(f"enqueue-folder: Radarr lang lookup unavailable — {e}")
    try:
        sonarr_map = sonarr_service.get_path_lang_map()
        arr_lang_map.update(sonarr_map)
        logger.info(f"enqueue-folder: loaded {len(sonarr_map)} Sonarr path→lang entries")
    except Exception as e:
        logger.warning(f"enqueue-folder: Sonarr lang lookup unavailable — {e}")

    enqueued = []
    skipped  = []
    errors   = []

    for dirpath, _dirnames, filenames in os.walk(folder):
        for fname in filenames:
            if os.path.splitext(fname)[1].lower() not in MEDIA_EXTENSIONS:
                continue
            path = os.path.join(dirpath, fname)
            try:
                info    = get_stream_info(path)
                streams = info.get("streams") or []
                video   = next((s for s in streams if s.get("codec_type") == "video"), {})
                a_streams = [s for s in streams if s.get("codec_type") == "audio"]

                # Priority 1: metadata stored at slice time in the media_test queue
                # Priority 2: Arr API path→lang map (for actual library files)
                # Priority 3: eng preference heuristic, then first audio track
                media_test_job = get_media_test_job(path)
                if media_test_job:
                    clip_meta  = json.loads(media_test_job.get("meta") or "{}")
                    audio_lang = clip_meta.get("orig_lang")
                    logger.info(f"enqueue-folder: using stored orig_lang={audio_lang!r} for {path}")
                elif arr_lang_map.get(path):
                    audio_lang = _parse_lang(arr_lang_map[path])
                else:
                    eng_stream = next((s for s in a_streams if (s.get("tags") or {}).get("language") == "eng"), None)
                    audio      = eng_stream or (a_streams[0] if a_streams else {})
                    audio_lang = (audio.get("tags") or {}).get("language")

                meta = {
                    "codec":        video.get("codec_name"),
                    "bitrate_kbps": int(float(info.get("format", {}).get("bit_rate", 0))) // 1000 or None,
                    "orig_lang":    audio_lang,
                    "has_51":       any((s.get("channels") or 0) > 5 for s in a_streams if (s.get("tags") or {}).get("language") == audio_lang),
                    "arr_type":     None,
                    "arr_id":       None,
                    "dry_run":      dry_run,
                }
            except Exception as e:
                logger.warning(f"enqueue-folder: ffprobe failed for {path} — {e}")
                errors.append({"path": path, "error": str(e)})
                continue

            job_id = enqueue_job(path, meta)
            if job_id is not None:
                logger.info(f"enqueue-folder: enqueued job {job_id} for {path}")
                enqueued.append({"job_id": job_id, "path": path})
            else:
                logger.info(f"enqueue-folder: skipped duplicate {path}")
                skipped.append(path)

    logger.info(
        f"enqueue-folder {folder}: {len(enqueued)} enqueued, "
        f"{len(skipped)} skipped, {len(errors)} errors"
        + (" (dry_run)" if dry_run else "")
    )
    return ({"enqueued": enqueued, "skipped": skipped, "errors": errors}, 202)
