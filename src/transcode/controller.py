import json
import logging
import os
from flask import Blueprint, request

from src import config, radarr_service, sonarr_service
from src.media_extensions import MEDIA_EXTENSIONS
from src.transcode.queue import clear_jobs, enqueue_job, list_jobs, requeue_job
from src.transcode.probe import get_stream_info

logger = logging.getLogger(__name__)

bp = Blueprint("transcode", __name__)

_LANG_MAP = {
    "english": "eng",
    "japanese": "jpn",
    "french": "fra",
    "spanish": "spa",
    "german": "deu",
    "korean": "kor",
    "chinese": "zho",
    "portuguese": "por",
    "italian": "ita",
}


def _parse_lang(name: str) -> str | None:
    return _LANG_MAP.get((name or "").lower().strip()) if name else None


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
    skip_groups = set(config.get_list("transcode", "skip_groups"))
    release_group = (file_info.get("releaseGroup") or "").strip()
    if release_group.lower() in skip_groups:
        logger.info(f"Skipping trusted group '{release_group}': {file_info.get('path')}")
        return ("", 200)

    # Skip if already tagged as transcoded
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

    meta = {
        "codec": media_info.get("videoCodec"),
        "bitrate_kbps": media_info.get("videoBitrate"),
        "orig_lang": orig_lang,
        "has_51": (media_info.get("audioChannels") or 0) > 5,
        "arr_type": arr_type,
        "arr_id": media_obj.get("id"),
        "dry_run": request.args.get("dry_run", "").lower() == "true",
    }

    enqueue_job(file_info["path"], meta)
    return ("", 202)


@bp.route("/transcode/jobs", methods=["GET"])
def get_jobs():
    status = request.args.get("status")  # optional filter: pending, processing, done, failed
    jobs = list_jobs(status)
    for job in jobs:
        if isinstance(job.get("meta"), str):
            try:
                job["meta"] = json.loads(job["meta"])
            except (json.JSONDecodeError, TypeError):
                pass
    return ({"jobs": jobs}, 200)


@bp.route("/transcode/jobs", methods=["DELETE"])
def delete_jobs():
    status = request.args.get("status")
    if not status:
        return ({"error": "Missing required query param: status"}, 400)
    deleted = clear_jobs(status)
    logger.info(f"Cleared {deleted} transcode jobs with status={status}")
    return ({"deleted": deleted}, 200)


@bp.route("/transcode/jobs/<int:job_id>/retry", methods=["POST"])
def retry_job(job_id):
    dry_run = request.args.get("dry_run", "").lower() == "true"
    found = requeue_job(job_id, dry_run=dry_run)
    if not found:
        return ({"error": f"Job {job_id} not found"}, 404)
    logger.info(f"Requeued job {job_id} (dry_run={dry_run})")
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
                audio   = next((s for s in streams if s.get("codec_type") == "audio"), {})

                # Prefer audio language tag if present
                audio_lang = (audio.get("tags") or {}).get("language")

                meta = {
                    "codec":        video.get("codec_name"),
                    "bitrate_kbps": int(float(info.get("format", {}).get("bit_rate", 0))) // 1000 or None,
                    "orig_lang":    audio_lang,
                    "has_51":       (audio.get("channels") or 0) > 5,
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
