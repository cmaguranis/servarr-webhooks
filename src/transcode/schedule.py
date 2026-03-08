import json
import logging
import os

logger = logging.getLogger(__name__)

_SCHEDULE_PATH = os.getenv("TRANSCODE_SCHEDULE_PATH", "/config/data/transcode_schedule.json")


def is_enabled() -> bool:
    try:
        with open(_SCHEDULE_PATH) as f:
            return json.load(f).get("enabled", True)
    except FileNotFoundError:
        return True


def set_enabled(enabled: bool):
    os.makedirs(os.path.dirname(_SCHEDULE_PATH), exist_ok=True)
    with open(_SCHEDULE_PATH, "w") as f:
        json.dump({"enabled": enabled}, f)
    logger.info(f"Transcode scheduler {'enabled' if enabled else 'disabled'}")
