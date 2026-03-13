import json
import logging

from flask import Blueprint, request

logger = logging.getLogger(__name__)


def register_job_routes(bp: Blueprint, queue, url_prefix: str):
    """Attach GET /jobs, DELETE /jobs, and POST /jobs/<id>/retry to a blueprint."""

    @bp.route(f"{url_prefix}/jobs", methods=["GET"])
    def list_jobs():
        status = request.args.get("status")
        jobs = queue.list_jobs(status)
        for job in jobs:
            if isinstance(job.get("meta"), str):
                try:
                    job["meta"] = json.loads(job["meta"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return {"jobs": jobs}, 200

    @bp.route(f"{url_prefix}/jobs", methods=["DELETE"])
    def delete_jobs():
        status = request.args.get("status")
        if not status:
            return {"error": "Missing required query param: status"}, 400
        deleted = queue.clear_jobs(status)
        logger.info(f"Cleared {deleted} {url_prefix} jobs with status={status}")
        return {"deleted": deleted}, 200

    @bp.route(f"{url_prefix}/jobs/<int:job_id>", methods=["DELETE"])
    def delete_job(job_id):
        found = queue.delete_job(job_id)
        if not found:
            return {"error": f"Job {job_id} not found"}, 404
        logger.info(f"Deleted {url_prefix} job {job_id}")
        return "", 204

    @bp.route(f"{url_prefix}/jobs/<int:job_id>/retry", methods=["POST"])
    def retry_job(job_id):
        dry_run_param = request.args.get("dry_run")
        dry_run = dry_run_param.lower() == "true" if dry_run_param is not None else None
        found = queue.requeue_job(job_id, dry_run=dry_run)
        if not found:
            return {"error": f"Job {job_id} not found"}, 404
        logger.info(f"Requeued {url_prefix} job {job_id} (dry_run={dry_run})")
        return "", 202


def register_schedule_routes(bp: Blueprint, schedule_module, url_prefix: str):
    """Attach GET and POST /schedule to a blueprint."""

    @bp.route(f"{url_prefix}/schedule", methods=["GET"])
    def get_schedule():
        return {"enabled": schedule_module.is_enabled()}, 200

    @bp.route(f"{url_prefix}/schedule", methods=["POST"])
    def set_schedule():
        enabled = request.args.get("enabled", "").lower()
        if enabled not in ("true", "false"):
            return {"error": "Missing or invalid query param: enabled (true|false)"}, 400
        schedule_module.set_enabled(enabled == "true")
        return {"enabled": schedule_module.is_enabled()}, 200
