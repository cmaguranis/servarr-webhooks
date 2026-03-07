import os
import logging
import requests

logger = logging.getLogger(__name__)

_tag_cache: dict[str, int] = {}
_TIMEOUT = 15


def _base():
    return os.getenv("RADARR_BASEURL", "").rstrip("/")


def _headers():
    return {"X-Api-Key": os.getenv("RADARR_API_KEY", "")}


def get_movie(movie_id):
    res = requests.get(f"{_base()}/api/v3/movie/{movie_id}", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def get_all_movies():
    res = requests.get(f"{_base()}/api/v3/movie", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


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
