from src import config
from src.schedule import Schedule


def is_enabled() -> bool:
    return Schedule(config.PLEX_SCHEDULE_PATH()).is_enabled()


def set_enabled(enabled: bool):
    Schedule(config.PLEX_SCHEDULE_PATH()).set_enabled(enabled)
