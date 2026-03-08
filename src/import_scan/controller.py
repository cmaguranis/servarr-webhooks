import logging
from flask import Blueprint, request, jsonify
from src import radarr_service, sonarr_service

logger = logging.getLogger(__name__)
bp = Blueprint("import_scan", __name__)


@bp.route("/import-scan", methods=["POST"])
def import_scan():
    """
    Trigger Radarr/Sonarr to scan a folder and import any new media files,
    firing their On Import webhooks (including the transcode webhook).

    Body:
      {
        "path": "/media/import/The Dark Knight (2008)",
        "arr":  "radarr" | "sonarr" | "both"   // default: "both"
      }

    Radarr fires DownloadedMoviesScan; Sonarr fires DownloadedEpisodesScan.
    Both move/rename the file into the library and trigger all configured webhooks.
    """
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    arr = (body.get("arr") or "both").lower()

    if not path:
        return jsonify({"error": "path is required"}), 400
    if arr not in ("radarr", "sonarr", "both"):
        return jsonify({"error": "arr must be 'radarr', 'sonarr', or 'both'"}), 400

    results = {}

    if arr in ("radarr", "both"):
        try:
            data = radarr_service.trigger_import_scan(path)
            results["radarr"] = {"status": "queued", "commandId": data.get("id")}
        except Exception as e:
            logger.error(f"Radarr import scan failed for '{path}': {e}")
            results["radarr"] = {"status": "error", "error": str(e)}

    if arr in ("sonarr", "both"):
        try:
            data = sonarr_service.trigger_import_scan(path)
            results["sonarr"] = {"status": "queued", "commandId": data.get("id")}
        except Exception as e:
            logger.error(f"Sonarr import scan failed for '{path}': {e}")
            results["sonarr"] = {"status": "error", "error": str(e)}

    status = 200 if any(r["status"] == "queued" for r in results.values()) else 502
    return jsonify(results), status
