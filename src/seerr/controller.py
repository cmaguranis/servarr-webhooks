import time
import logging
import requests
from flask import Blueprint, request

from src import config, radarr_service

logger = logging.getLogger(__name__)

bp = Blueprint("seerr", __name__)


@bp.route("/seerr_webhook", methods=["POST"])
def handle_seerr_webhook():
    request_data = request.get_json()

    request_id = request_data.get("requestID")
    media_tmdbid = request_data.get("mediaId")
    media_type = request_data.get("mediaType")

    if not all([request_id, media_tmdbid, media_type]):
        logger.warning(f"Seerr: missing fields in payload — {request_data}")
        return ("Bad Request", 400)

    # Wait for initial auto-approve to finish
    time.sleep(3)

    seerr_baseurl = config.SEERR_BASEURL()
    seerr_api_key = config.SEERR_API_KEY()
    root_folder_anime_movies = config.SEERR_ROOT_FOLDER_ANIME_MOVIES()

    headers = {"X-Api-Key": seerr_api_key, "accept": "application/json"}
    try:
        res = requests.get(
            f"{seerr_baseurl}/api/v1/{media_type}/{media_tmdbid}", headers=headers, timeout=15
        )
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        logger.error(f"Seerr: failed to fetch metadata for {media_type} tmdbId={media_tmdbid} — {e}")
        return ("Error", 500)

    title = data.get("title") or data.get("name", f"tmdbId={media_tmdbid}")
    is_anime = (
        "animation" in [g["name"].lower() for g in data.get("genres", [])]
        and data.get("originalLanguage", "").lower() == "ja"
    )

    if media_type == "movie" and is_anime:
        res = requests.put(
            f"{seerr_baseurl}/api/v1/request/{request_id}",
            headers=headers,
            json={"mediaType": "movie", "rootFolder": root_folder_anime_movies},
            timeout=15,
        )
        if res.status_code in (200, 202):
            logger.info(f"Seerr: routed '{title}' to anime folder (requestId={request_id})")
        else:
            logger.error(
                f"Seerr: failed to update request {request_id} for '{title}' — {res.status_code} {res.text}"
            )

        try:
            radarr_service.update_movie_path(media_tmdbid, root_folder_anime_movies)
        except Exception as e:
            logger.error(f"Seerr: Radarr path update failed for tmdbId={media_tmdbid} — {e}")
    else:
        logger.info(f"Seerr: no action for '{title}' (type={media_type}, anime={is_anime})")

    return ("Success", 202)
