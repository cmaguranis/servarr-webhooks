import os
import re
import shutil
import logging
from flask import Flask, request
from waitress import serve

_RESET = "\033[0m"

_LEVEL_COLORS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}

# 256-color palette for package names (avoid 0-15 which vary per terminal theme)
_PKG_PALETTE = [
    "\033[38;5;33m",   # blue
    "\033[38;5;38m",   # teal
    "\033[38;5;135m",  # purple
    "\033[38;5;166m",  # orange
    "\033[38;5;105m",  # violet
    "\033[38;5;43m",   # seafoam
    "\033[38;5;172m",  # amber
    "\033[38;5;67m",   # steel blue
]

# Distinct colors for job IDs so each job stands out
_JOB_PALETTE = [
    "\033[38;5;208m",  # orange
    "\033[38;5;51m",   # aqua
    "\033[38;5;201m",  # pink
    "\033[38;5;118m",  # lime
    "\033[38;5;226m",  # yellow
    "\033[38;5;165m",  # purple
    "\033[38;5;45m",   # sky blue
    "\033[38;5;214m",  # gold
    "\033[38;5;197m",  # hot pink
    "\033[38;5;82m",   # bright green
]

_JOB_RE = re.compile(r'\[job (\d+)\]')


def _pkg_color(name: str) -> str:
    parts = name.split(".")
    key = parts[1] if len(parts) > 1 and parts[0] == "src" else parts[0]
    return _PKG_PALETTE[hash(key) % len(_PKG_PALETTE)]


class _ColorFormatter(logging.Formatter):
    def format(self, record):
        # Work on a copy so we don't mutate the original LogRecord
        r = logging.makeLogRecord(record.__dict__)

        level_color = _LEVEL_COLORS.get(r.levelname, "")
        r.levelname = f"{level_color}{r.levelname}{_RESET}" if level_color else r.levelname

        pkg_color = _pkg_color(r.name)
        r.name = f"{pkg_color}{r.name}{_RESET}"

        result = super().format(r)

        def _color_job(m):
            job_id = int(m.group(1))
            c = _JOB_PALETTE[job_id % len(_JOB_PALETTE)]
            return f"{c}[job {job_id}]{_RESET}"

        return _JOB_RE.sub(_color_job, result)


_handler = logging.StreamHandler()
_handler.setFormatter(_ColorFormatter(
    fmt="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

# Bootstrap /config on first run
config_path = os.getenv("CONFIG_PATH", "/config/config.ini")
os.makedirs(os.path.dirname(config_path), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(config_path), "data"), exist_ok=True)
if not os.path.exists(config_path):
    src_default = os.path.join(os.path.dirname(__file__), "config.ini.default")
    shutil.copy(src_default, config_path)

from src.seerr.controller import bp as seerr_bp
from src.transcode.controller import bp as transcode_bp
from src.import_scan.controller import bp as import_scan_bp
from src.test_media.controller import bp as test_media_bp
from src.transcode.queue import init_db
from src.transcode import worker
from src.test_media.queue import init_db as init_media_test_db
from src.test_media import worker as media_test_worker

app = Flask(__name__)
app.register_blueprint(seerr_bp)
app.register_blueprint(transcode_bp)
app.register_blueprint(import_scan_bp)
app.register_blueprint(test_media_bp)

_access_log = logging.getLogger("access")

@app.after_request
def _log_request(response):
    _access_log.info(
        "%s %s %s",
        request.method,
        request.full_path.rstrip("?"),
        response.status_code,
    )
    return response

init_db()
worker.start()
init_media_test_db()
media_test_worker.start()

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=5001)
