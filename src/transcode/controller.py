"""Flask blueprint for the transcode webhook and job management API.

Receives Radarr/Sonarr download webhooks, enqueues HEVC transcode jobs, and
exposes job queue and schedule management endpoints.
"""

import json
import logging
import os

from flask import Blueprint, request

from src import radarr_service, sonarr_service
from src.job_routes import register_job_routes, register_schedule_routes
from src.lang import parse_lang as _parse_lang
from src.media_extensions import MEDIA_EXTENSIONS
from src.test_media.queue import get_job_by_path as get_media_test_job
from src.transcode import queue as transcode_queue
from src.transcode import schedule
from src.transcode.probe import extract_probe_summary, get_stream_info
from src.transcode.queue import enqueue_job

logger = logging.getLogger(__name__)

bp = Blueprint("transcode", __name__)
register_job_routes(bp, transcode_queue, "/transcode")
register_schedule_routes(bp, schedule, "/transcode")


def _quality_id(quality):
    if not isinstance(quality, dict):
        return "<no-quality>"
    val = quality.get("quality", {}).get("id")
    return val if val is not None else "<no-quality>"


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
        (media_obj.get("originalLanguage") or {}).get("name") or media_info.get("audioLanguages", "")
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
        "quality_profile_id": media_obj.get("qualityProfileId"),
        "current_quality_id": _quality_id(file_info.get("quality")),
        "dry_run": request.args.get("dry_run", "").lower() == "true",
        "media_test": media_test,
        "start_sec": int(start_sec_raw) if start_sec_raw else None,
        "slice_duration": int(slice_duration_raw) if slice_duration_raw else None,
    }

    probe = None
    try:
        probe = extract_probe_summary(get_stream_info(file_info["path"]))
    except Exception as e:
        logger.warning(f"Could not probe {file_info.get('path')} at enqueue: {e}")

    enqueue_job(file_info["path"], meta, probe=probe)
    return ("", 202)


@bp.route("/transcode/enqueue-file", methods=["POST"])
def enqueue_file():
    """Enqueue a single file for transcoding.

    Body (JSON): {"path": "/media/movie.mkv"}
    Query params: ?dry_run=true  ?media_test=true
    """
    dry_run = request.args.get("dry_run", "").lower() == "true"
    media_test = request.args.get("media_test", "").lower() == "true"
    full = request.args.get("full", "").lower() == "true"
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()

    if not path:
        return ({"error": "Missing required field: path"}, 400)
    if not os.path.isfile(path):
        return ({"error": f"File does not exist: {path}"}, 400)

    try:
        info = get_stream_info(path)
        probe = extract_probe_summary(info)
        streams = info.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]

        orig_lang = body.get("orig_lang")
        if not orig_lang:
            arr_lang = None
            try:
                movie = radarr_service.get_path_movie_map().get(path)
                if movie:
                    arr_lang = (movie.get("originalLanguage") or {}).get("name")
            except Exception:
                pass
            if not arr_lang:
                try:
                    entry = sonarr_service.get_path_episode_map().get(path)
                    if entry:
                        arr_lang = (entry["series"].get("originalLanguage") or {}).get("name")
                except Exception:
                    pass
            if arr_lang:
                orig_lang = _parse_lang(arr_lang)
            else:
                eng_stream = next((s for s in a_streams if (s.get("tags") or {}).get("language") == "eng"), None)
                audio = eng_stream or (a_streams[0] if a_streams else {})
                orig_lang = (audio.get("tags") or {}).get("language")

        meta = {
            "codec": video.get("codec_name"),
            "bitrate_kbps": int(float(info.get("format", {}).get("bit_rate", 0))) // 1000 or None,
            "orig_lang": orig_lang,
            "has_51": any(
                (s.get("channels") or 0) > 5
                for s in a_streams
                if (s.get("tags") or {}).get("language") == orig_lang
            ),
            "arr_type": None,
            "arr_id": None,
            "dry_run": dry_run,
            "media_test": media_test,
            "full": full,
        }
    except Exception as e:
        logger.warning(f"enqueue-file: ffprobe failed for {path} — {e}")
        return ({"error": f"ffprobe failed: {e}"}, 500)

    job_id = enqueue_job(path, meta, probe=probe)
    if job_id is not None:
        logger.info(f"enqueue-file: enqueued job {job_id} for {path}")
        return ({"job_id": job_id, "path": path}, 202)
    else:
        logger.info(f"enqueue-file: skipped duplicate {path}")
        return ({"skipped": True, "path": path}, 200)


@bp.route("/transcode/enqueue-folder", methods=["POST"])
def enqueue_folder():
    """Scan a folder for media files and enqueue transcode jobs for each.

    Body (JSON): {"path": "/data/media_test"}
    Query params: ?dry_run=true  ?media_test=true
    """
    dry_run = request.args.get("dry_run", "").lower() == "true"
    media_test = request.args.get("media_test", "").lower() == "true"
    body = request.get_json(silent=True) or {}
    folder = (body.get("path") or "").strip()

    if not folder:
        return ({"error": "Missing required field: path"}, 400)
    if not os.path.isdir(folder):
        return ({"error": f"Directory does not exist: {folder}"}, 400)

    # Build path → orig_lang map from Arr APIs
    arr_lang_map: dict[str, str] = {}
    try:
        for path, movie in radarr_service.get_path_movie_map().items():
            lang = (movie.get("originalLanguage") or {}).get("name")
            if lang:
                arr_lang_map[path] = lang
        logger.info(f"enqueue-folder: loaded {len(arr_lang_map)} Radarr path→lang entries")
    except Exception as e:
        logger.warning(f"enqueue-folder: Radarr lookup unavailable — {e}")
    try:
        sonarr_count = 0
        for path, entry in sonarr_service.get_path_episode_map().items():
            series = entry["series"]
            lang = (series.get("originalLanguage") or {}).get("name")
            if lang:
                arr_lang_map[path] = lang
                sonarr_count += 1
        logger.info(f"enqueue-folder: loaded {sonarr_count} Sonarr path→lang entries")
    except Exception as e:
        logger.warning(f"enqueue-folder: Sonarr lookup unavailable — {e}")

    enqueued = []
    skipped = []
    errors = []

    for dirpath, _dirnames, filenames in os.walk(folder):
        for fname in filenames:
            if os.path.splitext(fname)[1].lower() not in MEDIA_EXTENSIONS:
                continue
            path = os.path.join(dirpath, fname)
            try:
                info = get_stream_info(path)
                probe = extract_probe_summary(info)
                streams = info.get("streams") or []
                video = next((s for s in streams if s.get("codec_type") == "video"), {})
                a_streams = [s for s in streams if s.get("codec_type") == "audio"]

                # Priority 1: metadata stored at slice time in the media_test queue
                # Priority 2: Arr API path→lang map (for actual library files)
                # Priority 3: eng preference heuristic, then first audio track
                media_test_job = get_media_test_job(path)
                if media_test_job:
                    clip_meta = json.loads(media_test_job.get("meta") or "{}")
                    audio_lang = clip_meta.get("orig_lang")
                    logger.info(f"enqueue-folder: using stored orig_lang={audio_lang!r} for {path}")
                elif arr_lang_map.get(path):
                    audio_lang = _parse_lang(arr_lang_map[path])
                else:
                    eng_stream = next((s for s in a_streams if (s.get("tags") or {}).get("language") == "eng"), None)
                    audio = eng_stream or (a_streams[0] if a_streams else {})
                    audio_lang = (audio.get("tags") or {}).get("language")

                meta = {
                    "codec": video.get("codec_name"),
                    "bitrate_kbps": int(float(info.get("format", {}).get("bit_rate", 0))) // 1000 or None,
                    "orig_lang": audio_lang,
                    "has_51": any(
                        (s.get("channels") or 0) > 5
                        for s in a_streams
                        if (s.get("tags") or {}).get("language") == audio_lang
                    ),
                    "arr_type": None,
                    "arr_id": None,
                    "dry_run": dry_run,
                    "media_test": media_test,
                }
            except Exception as e:
                logger.warning(f"enqueue-folder: ffprobe failed for {path} — {e}")
                errors.append({"path": path, "error": str(e)})
                continue

            job_id = enqueue_job(path, meta, probe=probe)
            if job_id is not None:
                logger.info(f"enqueue-folder: enqueued job {job_id} for {path}")
                enqueued.append({"job_id": job_id, "path": path})
            else:
                logger.info(f"enqueue-folder: skipped duplicate {path}")
                skipped.append(path)

    logger.info(
        f"enqueue-folder {folder}: {len(enqueued)} enqueued, "
        f"{len(skipped)} skipped, {len(errors)} errors" + (" (dry_run)" if dry_run else "")
    )
    return ({"enqueued": enqueued, "skipped": skipped, "errors": errors}, 202)
