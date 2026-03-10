import os

from src.schedule import Schedule

_SCHEDULE_PATH = os.getenv("PLEX_SCHEDULE_PATH", "/config/data/plex_cleanup_schedule.json")


def is_enabled() -> bool:
    return Schedule(_SCHEDULE_PATH).is_enabled()


def set_enabled(enabled: bool):
    Schedule(_SCHEDULE_PATH).set_enabled(enabled)
