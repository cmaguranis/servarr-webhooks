import json
import logging
import os

logger = logging.getLogger(__name__)


class Schedule:
    """JSON file-backed enable/disable flag. Defaults to enabled if file is missing."""

    def __init__(self, path: str, default: bool = True):
        self._path = path
        self._default = default

    def is_enabled(self) -> bool:
        try:
            with open(self._path) as f:
                return json.load(f).get("enabled", self._default)
        except FileNotFoundError:
            return self._default

    def set_enabled(self, enabled: bool):
        dir_ = os.path.dirname(self._path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump({"enabled": enabled}, f)
        logger.info(f"Schedule ({self._path}): {'enabled' if enabled else 'disabled'}")
