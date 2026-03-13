import configparser
import os

CONFIG_PATH = os.getenv("CONFIG_PATH", "/config/config.ini")


def _get(section, key, fallback=None):
    env_val = os.getenv(f"{section}_{key}".upper())
    if env_val is not None:
        return env_val
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg.get(section, key, fallback=fallback)


def _get_int(section, key, fallback: int) -> int:
    return int(_get(section, key, fallback=str(fallback)))


def _get_list(section, key, fallback="") -> list:
    raw = _get(section, key, fallback=fallback)
    return [v.strip().lower() for v in raw.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# Named accessors — name matches the env var override (SECTION_KEY uppercased).
# Resolution order: env var → config.ini → default below.
# All keys are documented in config.ini.default and docker-compose.example.yml.
# ---------------------------------------------------------------------------


# [worker]
def WORKER_POLL_INTERVAL() -> int:
    return _get_int("worker", "poll_interval", 120)


# [transcode]
def TRANSCODE_CLEANUP_DONE_DAYS() -> int:
    return _get_int("transcode", "cleanup_done_days", 7)


def TRANSCODE_CLEANUP_FAILED_DAYS() -> int:
    return _get_int("transcode", "cleanup_failed_days", 21)


def TRANSCODE_WORKER_COUNT() -> int:
    return _get_int("transcode", "worker_count", 1)


def TRANSCODE_HEVC_ICQ_QUALITY() -> int:
    return _get_int("transcode", "hevc_icq_quality", 23)


def TRANSCODE_MAX_CONCURRENT_QSV_SESSIONS() -> int:
    return _get_int("transcode", "max_concurrent_qsv_sessions", 2)


def TRANSCODE_TEMP_PRIMARY() -> str:
    return _get("transcode", "temp_primary", "/dev/shm")


def TRANSCODE_TEMP_FALLBACK() -> str:
    return _get("transcode", "temp_fallback", "/transcode-temp")


# [plex]
def PLEX_COLLECTION_DAYS() -> int:
    return _get_int("plex", "collection_days", 30)


def PLEX_MOVIE_BATCH() -> int:
    return _get_int("plex", "movie_batch", 100)


def PLEX_COLLECTION_NAME() -> str:
    return _get("plex", "collection_name", "Leaving Soon")


def PLEX_WORKER_COUNT() -> int:
    return _get_int("plex", "worker_count", 2)


# [test_media]
def TEST_MEDIA_WORKER_COUNT() -> int:
    return _get_int("test_media", "worker_count", 1)


def TEST_MEDIA_CACHE_DIR() -> str:
    return _get("test_media", "cache_dir", "/data/media_cache")


def TEST_MEDIA_OUTPUT_DIR() -> str:
    return _get("test_media", "output_dir", "/data/media_test")


def TEST_MEDIA_MEDIA_DIR() -> str:
    return _get("test_media", "media_dir", "/media")


# [seerr]
def SEERR_BASEURL() -> str:
    return _get("seerr", "baseurl", "").rstrip("/")


def SEERR_API_KEY() -> str:
    return _get("seerr", "api_key", "")


def SEERR_ROOT_FOLDER_ANIME_MOVIES() -> str:
    return _get("seerr", "root_folder_anime_movies", "")


# [radarr]
def RADARR_BASEURL() -> str:
    return _get("radarr", "baseurl", "").rstrip("/")


def RADARR_API_KEY() -> str:
    return _get("radarr", "api_key", "")


# [sonarr]
def SONARR_BASEURL() -> str:
    return _get("sonarr", "baseurl", "").rstrip("/")


def SONARR_API_KEY() -> str:
    return _get("sonarr", "api_key", "")


# [plex] — credentials + infrastructure paths
def PLEX_BASEURL() -> str:
    return _get("plex", "baseurl", "")


def PLEX_TOKEN() -> str:
    return _get("plex", "token", "")


def PLEX_MEDIA_DB() -> str:
    return _get("plex", "media_db", "/config/data/plex_media.db")


def PLEX_CLEANUP_DB() -> str:
    return _get("plex", "cleanup_db", "/config/data/plex_cleanup.db")


def PLEX_SCHEDULE_PATH() -> str:
    return _get("plex", "schedule_path", "/config/data/plex_cleanup_schedule.json")


# [transcode] — infrastructure paths
def TRANSCODE_DB() -> str:
    return _get("transcode", "db", "/config/data/transcode_queue.db")


def TRANSCODE_SCHEDULE_PATH() -> str:
    return _get("transcode", "schedule_path", "/config/data/transcode_schedule.json")


# [test_media] — infrastructure paths
def MEDIA_TEST_DB() -> str:
    return _get("test_media", "db", "/config/data/media_test_queue.db")


# [worker]
def LOG_PATH() -> str:
    return _get("worker", "log_path", "")
