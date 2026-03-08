import os
import shutil
import logging
from flask import Flask, request
from waitress import serve

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Bootstrap /config on first run
config_path = os.getenv("CONFIG_PATH", "/config/config.ini")
os.makedirs(os.path.dirname(config_path), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(config_path), "data"), exist_ok=True)
if not os.path.exists(config_path):
    src_default = os.path.join(os.path.dirname(__file__), "config.ini.default")
    shutil.copy(src_default, config_path)

from src.seerr.controller import bp as seerr_bp
from src.promote.controller import bp as promote_bp
from src.transcode.controller import bp as transcode_bp
from src.import_scan.controller import bp as import_scan_bp
from src.transcode.queue import init_db
from src.transcode import worker

app = Flask(__name__)
app.register_blueprint(seerr_bp)
app.register_blueprint(promote_bp)
app.register_blueprint(transcode_bp)
app.register_blueprint(import_scan_bp)

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

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=5001)
