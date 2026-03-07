import logging
from flask import Blueprint, request

from src import config, radarr_service, sonarr_service
from src.transcode.queue import enqueue_job

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
