import logging
import os

import requests

from src import config

logger = logging.getLogger(__name__)

_tag_cache: dict[str, int] = {}
_TIMEOUT = 15


def _base():
    return config.RADARR_BASEURL()


def _headers():
    return {"X-Api-Key": config.RADARR_API_KEY()}


def get_movie(movie_id):
    res = requests.get(f"{_base()}/api/v3/movie/{movie_id}", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def get_movie_file(file_id: int) -> dict | None:
    res = requests.get(f"{_base()}/api/v3/movieFile/{file_id}", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def get_all_movies():
    res = requests.get(f"{_base()}/api/v3/movie", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def get_path_lang_map() -> dict[str, str]:
    """Return {file_path: original_language_name} for all movies that have a file."""
    result = {}
    for m in get_all_movies():
        path = (m.get("movieFile") or {}).get("path")
        lang = (m.get("originalLanguage") or {}).get("name")
        if path and lang:
            result[path] = lang
    return result


def get_path_movie_map() -> dict[str, dict]:
    """Return {file_path: movie_dict} for all movies that have a file."""
    result = {}
    for m in get_all_movies():
        path = (m.get("movieFile") or {}).get("path")
        if path:
            result[path] = m
    return result


def get_movie_by_tmdb(tmdb_id):
    res = requests.get(f"{_base()}/api/v3/movie", params={"tmdbId": tmdb_id}, headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    movies = res.json()
    return movies[0] if movies else None


def update_movie(movie):
    res = requests.put(
        f"{_base()}/api/v3/movie/{movie['id']}",
        params={"moveFiles": "true"},
        headers=_headers(),
        json=movie,
        timeout=_TIMEOUT,
    )
    res.raise_for_status()
    return res.json()


def update_movie_path(tmdb_id, new_root_path):
    movie = get_movie_by_tmdb(tmdb_id)
    if not movie:
        logger.warning(f"Radarr: movie tmdbId={tmdb_id} not found")
        return
    folder_name = os.path.basename(movie["path"])
    movie["path"] = os.path.join(new_root_path, folder_name)
    movie["rootFolderPath"] = new_root_path
    update_movie(movie)
    logger.info(f"Radarr: moved '{movie['title']}' to {new_root_path}")


def get_or_create_tag(label: str) -> int:
    if label in _tag_cache:
        return _tag_cache[label]
    res = requests.get(f"{_base()}/api/v3/tag", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    for tag in res.json():
        _tag_cache[tag["label"]] = tag["id"]
    if label in _tag_cache:
        return _tag_cache[label]
    res = requests.post(f"{_base()}/api/v3/tag", headers=_headers(), json={"label": label}, timeout=_TIMEOUT)
    res.raise_for_status()
    tag_id = res.json()["id"]
    _tag_cache[label] = tag_id
    return tag_id


def add_tag(movie_id: int, tag_label: str):
    tag_id = get_or_create_tag(tag_label)
    movie = get_movie(movie_id)
    if tag_id not in movie.get("tags", []):
        movie.setdefault("tags", []).append(tag_id)
        update_movie(movie)
        logger.info(f"Radarr: tagged movie {movie_id} with '{tag_label}'")


def rescan_movie(movie_id: int):
    res = requests.post(
        f"{_base()}/api/v3/command",
        headers=_headers(),
        json={"name": "RescanMovie", "movieId": movie_id},
        timeout=_TIMEOUT,
    )
    res.raise_for_status()
    logger.info(f"Radarr: rescan issued for movie {movie_id}")


def delete_movie(movie_id: int, delete_files: bool = True, add_exclusion: bool = False) -> bool:
    try:
        res = requests.delete(
            f"{_base()}/api/v3/movie/{movie_id}",
            params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": str(add_exclusion).lower()},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        res.raise_for_status()
        logger.info(f"Radarr: deleted movie {movie_id} (deleteFiles={delete_files})")
        return True
    except requests.RequestException as e:
        logger.error(f"Radarr: failed to delete movie {movie_id}: {e}")
        return False


_profile_cache: dict[int, dict] = {}


def get_quality_profile(profile_id: int) -> dict:
    if profile_id not in _profile_cache:
        res = requests.get(f"{_base()}/api/v3/qualityprofile/{profile_id}", headers=_headers(), timeout=_TIMEOUT)
        res.raise_for_status()
        _profile_cache[profile_id] = res.json()
    return _profile_cache[profile_id]


def is_cutoff_met(arr_id: int, quality_profile_id: int, current_quality_id: int) -> bool:
    profile = get_quality_profile(quality_profile_id)
    cutoff_id = profile["cutoff"]
    ordered = [
        item["quality"]["id"]
        for item in profile.get("items", [])
        if item.get("allowed") and "quality" in item
    ]
    if current_quality_id not in ordered or cutoff_id not in ordered:
        return True  # can't determine → assume met, do full transcode
    return ordered.index(current_quality_id) >= ordered.index(cutoff_id)


def has_pending_queue_item(movie_id: int) -> bool:
    res = requests.get(
        f"{_base()}/api/v3/queue",
        params={"movieId": movie_id, "pageSize": 1},
        headers=_headers(), timeout=_TIMEOUT,
    )
    res.raise_for_status()
    return res.json().get("totalRecords", 0) > 0


def unmonitor_movie(movie_id: int) -> bool:
    try:
        movie = get_movie(movie_id)
        movie["monitored"] = False
        update_movie(movie)
        logger.info(f"Radarr: unmonitored movie {movie_id}")
        return True
    except requests.RequestException as e:
        logger.error(f"Radarr: failed to unmonitor movie {movie_id}: {e}")
        return False


_BULK_CHUNK = 50


def bulk_delete_movies(
    movie_ids: list[int],
    delete_files: bool = True,
    add_exclusion: bool = False,
    chunk_size: int = _BULK_CHUNK,
) -> bool:
    try:
        for i in range(0, len(movie_ids), chunk_size):
            chunk = movie_ids[i:i + chunk_size]
            res = requests.delete(
                f"{_base()}/api/v3/movie/editor",
                headers=_headers(),
                json={"movieIds": chunk, "deleteFiles": delete_files, "addImportExclusion": add_exclusion},
                timeout=_TIMEOUT,
            )
            res.raise_for_status()
        logger.info(f"Radarr: bulk deleted {len(movie_ids)} movies (deleteFiles={delete_files})")
        return True
    except requests.RequestException as e:
        logger.error(f"Radarr: failed to bulk delete movies: {e}")
        return False


def bulk_unmonitor_movies(
    movie_ids: list[int],
    chunk_size: int = _BULK_CHUNK,
) -> bool:
    try:
        for i in range(0, len(movie_ids), chunk_size):
            chunk = movie_ids[i:i + chunk_size]
            res = requests.put(
                f"{_base()}/api/v3/movie/editor",
                headers=_headers(),
                json={"movieIds": chunk, "monitored": False},
                timeout=_TIMEOUT,
            )
            res.raise_for_status()
        logger.info(f"Radarr: bulk unmonitored {len(movie_ids)} movies")
        return True
    except requests.RequestException as e:
        logger.error(f"Radarr: failed to bulk unmonitor movies: {e}")
        return False


def trigger_import_scan(path: str) -> dict:
    res = requests.post(
        f"{_base()}/api/v3/command",
        headers=_headers(),
        json={"name": "DownloadedMoviesScan", "path": path},
        timeout=_TIMEOUT,
    )
    res.raise_for_status()
    data = res.json()
    logger.info(f"Radarr: import scan triggered for '{path}' (commandId={data.get('id')})")
    return data
