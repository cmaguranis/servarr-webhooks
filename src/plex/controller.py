"""Flask blueprint for the Plex cleanup API.

Provides endpoints to trigger the cleanup rules pass (categorise all media and
enqueue jobs), inspect and manage the cleanup job queue, and toggle the
enabled/disabled schedule flag that controls whether the background worker
claims new jobs.
"""

import logging
import os

from flask import Blueprint

from src.plex import queue as plex_queue
from src.plex import schedule
from src.plex.db import PlexMediaDB
from src.plex.rules import run_cleanup
from src.job_routes import register_job_routes, register_schedule_routes

logger = logging.getLogger(__name__)

_MEDIA_DB_PATH = os.getenv("PLEX_MEDIA_DB", "/config/data/plex_media.db")

bp = Blueprint("plex_cleanup", __name__)
register_job_routes(bp, plex_queue, "/plex/cleanup")     # GET/DELETE /jobs, POST /jobs/<id>/retry
register_schedule_routes(bp, schedule, "/plex/cleanup")  # GET/POST /schedule


@bp.route("/plex/cleanup/rules", methods=["POST"])
def run():
    db = PlexMediaDB(_MEDIA_DB_PATH)
    db.init_db()
    add_to_collection, delete, promote, do_nothing = run_cleanup(db=db)
    return {
        "add_to_collection": len(add_to_collection),
        "delete": len(delete),
        "promote": len(promote),
        "do_nothing": do_nothing,
    }, 202
