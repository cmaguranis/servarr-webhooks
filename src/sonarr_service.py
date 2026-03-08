import os
import logging
import requests

logger = logging.getLogger(__name__)

_tag_cache: dict[str, int] = {}
_TIMEOUT = 15


def _base():
    return os.getenv("SONARR_BASEURL", "").rstrip("/")


def _headers():
    return {"X-Api-Key": os.getenv("SONARR_API_KEY", "")}


def get_series(series_id):
    res = requests.get(f"{_base()}/api/v3/series/{series_id}", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def get_episode_file(file_id: int) -> dict | None:
    res = requests.get(f"{_base()}/api/v3/episodeFile/{file_id}", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def get_all_series():
    res = requests.get(f"{_base()}/api/v3/series", headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def update_series(series):
    res = requests.put(
        f"{_base()}/api/v3/series/{series['id']}",
        params={"moveFiles": "true"},
        headers=_headers(),
        json=series,
        timeout=_TIMEOUT,
    )
    res.raise_for_status()
    return res.json()


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


def add_tag(series_id: int, tag_label: str):
    tag_id = get_or_create_tag(tag_label)
    series = get_series(series_id)
    if tag_id not in series.get("tags", []):
        series.setdefault("tags", []).append(tag_id)
        update_series(series)
        logger.info(f"Sonarr: tagged series {series_id} with '{tag_label}'")


def rescan_series(series_id: int):
    res = requests.post(
        f"{_base()}/api/v3/command",
        headers=_headers(),
        json={"name": "RescanSeries", "seriesId": series_id},
        timeout=_TIMEOUT,
    )
    res.raise_for_status()
    logger.info(f"Sonarr: rescan issued for series {series_id}")


def trigger_import_scan(path: str) -> dict:
    res = requests.post(
        f"{_base()}/api/v3/command",
        headers=_headers(),
        json={"name": "DownloadedEpisodesScan", "path": path},
        timeout=_TIMEOUT,
    )
    res.raise_for_status()
    data = res.json()
    logger.info(f"Sonarr: import scan triggered for '{path}' (commandId={data.get('id')})")
    return data
