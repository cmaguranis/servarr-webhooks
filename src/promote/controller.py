import copy
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify

from src import radarr_service, sonarr_service

logger = logging.getLogger(__name__)

bp = Blueprint("promote", __name__)


def _update_media_path(media_item, threshold_date):
    added_str = media_item.get("added")
    if not added_str:
        return None

    added_date = datetime.fromisoformat(added_str.replace("Z", "+00:00"))
    if added_date > threshold_date:
        return None

    updated_item = copy.copy(media_item)
    logger.info(
        f"Promoting '{updated_item.get('title')}' (id={updated_item.get('id')}, added={added_str})"
    )

    new_root = updated_item.get("rootFolderPath", "").replace("/media_cache", "/media")
    new_path = updated_item.get("path", "").replace("/media_cache", "/media")

    updated_item["path"] = new_path
    updated_item["rootFolderPath"] = new_root
    return updated_item


@bp.route("/promote-cache", methods=["POST"])
def promote_cache():
    threshold_date = datetime.now(timezone.utc) - timedelta(days=8)

    try:
        movies = radarr_service.get_all_movies()
        for movie in movies:
            updated = _update_media_path(movie, threshold_date)
            if updated:
                try:
                    radarr_service.update_movie(updated)
                except Exception as e:
                    logger.error(f"Radarr: promotion failed for '{movie.get('title')}' — {e}")
    except Exception as e:
        logger.error(f"Radarr: failed to fetch movies — {e}")

    try:
        series_list = sonarr_service.get_all_series()
        for series in series_list:
            updated = _update_media_path(series, threshold_date)
            if updated:
                try:
                    sonarr_service.update_series(updated)
                except Exception as e:
                    logger.error(f"Sonarr: promotion failed for '{series.get('title')}' — {e}")
    except Exception as e:
        logger.error(f"Sonarr: failed to fetch series — {e}")

    return jsonify({"status": "promotion check complete"}), 200


