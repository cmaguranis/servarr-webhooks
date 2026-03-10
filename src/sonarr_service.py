import logging

import requests

from src import config

logger = logging.getLogger(__name__)

_tag_cache: dict[str, int] = {}
_TIMEOUT = 15


def _base():
    return config.SONARR_BASEURL()


def _headers():
    return {"X-Api-Key": config.SONARR_API_KEY()}


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


def get_series_by_tvdb(tvdb_id: int) -> dict | None:
    res = requests.get(f"{_base()}/api/v3/series", params={"tvdbId": tvdb_id}, headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    series = res.json()
    return series[0] if series else None


def get_episode_files(series_id: int) -> list:
    res = requests.get(f"{_base()}/api/v3/episodefile", params={"seriesId": series_id}, headers=_headers(), timeout=_TIMEOUT)
    res.raise_for_status()
    return res.json()


def delete_episode_files(series_id: int) -> bool:
    """Delete all episode files for a series but keep it monitored in Sonarr."""
    try:
        files = get_episode_files(series_id)
        file_ids = [f["id"] for f in files if "id" in f]
        if not file_ids:
            logger.info(f"Sonarr: no episode files to delete for series {series_id}")
            return True
        res = requests.delete(
            f"{_base()}/api/v3/episodeFile/bulk",
            headers=_headers(),
            json={"episodeFileIds": file_ids},
            timeout=_TIMEOUT,
        )
        res.raise_for_status()
        logger.info(f"Sonarr: deleted {len(file_ids)} episode files for series {series_id} (monitoring kept)")
        return True
    except requests.RequestException as e:
        logger.error(f"Sonarr: failed to delete episode files for series {series_id}: {e}")
        return False


def get_path_lang_map() -> dict[str, str]:
    """Return {file_path: original_language_name} for all episode files across all series."""
    result = {}
    for series in get_all_series():
        lang = (series.get("originalLanguage") or {}).get("name")
        if not lang:
            continue
        try:
            for f in get_episode_files(series["id"]):
                path = f.get("path")
                if path:
                    result[path] = lang
        except Exception as e:
            logger.warning(f"Sonarr: could not get episode files for series {series['id']}: {e}")
    return result


def get_path_episode_map() -> dict[str, dict]:
    """Return {file_path: {"series": series_dict, "episode_file": file_dict}} for all episode files."""
    result = {}
    for series in get_all_series():
        try:
            for f in get_episode_files(series["id"]):
                path = f.get("path")
                if path:
                    result[path] = {"series": series, "episode_file": f}
        except Exception as e:
            logger.warning(f"Sonarr: could not get episode files for series {series['id']}: {e}")
    return result


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


def delete_series(series_id: int, delete_files: bool = True, add_exclusion: bool = False) -> bool:
    try:
        res = requests.delete(
            f"{_base()}/api/v3/series/{series_id}",
            params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": str(add_exclusion).lower()},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        res.raise_for_status()
        logger.info(f"Sonarr: deleted series {series_id} (deleteFiles={delete_files})")
        return True
    except requests.RequestException as e:
        logger.error(f"Sonarr: failed to delete series {series_id}: {e}")
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


def has_pending_queue_item(series_id: int) -> bool:
    res = requests.get(
        f"{_base()}/api/v3/queue",
        params={"seriesId": series_id, "pageSize": 1},
        headers=_headers(), timeout=_TIMEOUT,
    )
    res.raise_for_status()
    return res.json().get("totalRecords", 0) > 0


def unmonitor_series(series_id: int) -> bool:
    try:
        series = get_series(series_id)
        series["monitored"] = False
        update_series(series)
        logger.info(f"Sonarr: unmonitored series {series_id}")
        return True
    except requests.RequestException as e:
        logger.error(f"Sonarr: failed to unmonitor series {series_id}: {e}")
        return False


_BULK_CHUNK = 50


def bulk_delete_series(
    series_ids: list[int],
    delete_files: bool = True,
    add_exclusion: bool = False,
    chunk_size: int = _BULK_CHUNK,
) -> bool:
    try:
        for i in range(0, len(series_ids), chunk_size):
            chunk = series_ids[i:i + chunk_size]
            res = requests.delete(
                f"{_base()}/api/v3/series/editor",
                headers=_headers(),
                json={"seriesIds": chunk, "deleteFiles": delete_files, "addImportExclusion": add_exclusion},
                timeout=_TIMEOUT,
            )
            res.raise_for_status()
        logger.info(f"Sonarr: bulk deleted {len(series_ids)} series (deleteFiles={delete_files})")
        return True
    except requests.RequestException as e:
        logger.error(f"Sonarr: failed to bulk delete series: {e}")
        return False


def bulk_unmonitor_series(
    series_ids: list[int],
    chunk_size: int = _BULK_CHUNK,
) -> bool:
    try:
        for i in range(0, len(series_ids), chunk_size):
            chunk = series_ids[i:i + chunk_size]
            res = requests.put(
                f"{_base()}/api/v3/series/editor",
                headers=_headers(),
                json={"seriesIds": chunk, "monitored": False},
                timeout=_TIMEOUT,
            )
            res.raise_for_status()
        logger.info(f"Sonarr: bulk unmonitored {len(series_ids)} series")
        return True
    except requests.RequestException as e:
        logger.error(f"Sonarr: failed to bulk unmonitor series: {e}")
        return False


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
