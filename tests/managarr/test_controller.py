"""Tests for src/managarr/controller.py — /managarr/cleanup/* API endpoints."""

import json
import pytest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch
from flask import Flask

from src.managarr.controller import bp
from src.managarr import controller as managarr_controller


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def job_mocks():
    with ExitStack() as stack:
        yield {
            "list_jobs": stack.enter_context(
                patch("src.managarr.queue.list_jobs", return_value=[])
            ),
            "clear_jobs": stack.enter_context(
                patch("src.managarr.queue.clear_jobs", return_value=0)
            ),
            "requeue_job": stack.enter_context(
                patch("src.managarr.queue.requeue_job", return_value=True)
            ),
        }


# ---------------------------------------------------------------------------
# POST /managarr/cleanup/rules
# ---------------------------------------------------------------------------

class TestRunCleanup:
    def _mock_run_cleanup(self, add=0, delete=0, promote=0, do_nothing=0):
        from src.managarr.rules import RuleResult, Action
        def _result(n, action):
            return [
                RuleResult(action=action, media_type="movie", plex_key=i, title=f"T{i}")
                for i in range(n)
            ]
        return (
            _result(add, Action.ADD_TO_COLLECTION),
            _result(delete, Action.DELETE),
            _result(promote, Action.PROMOTE),
            do_nothing,
        )

    def test_returns_bucket_counts(self, client):
        buckets = self._mock_run_cleanup(add=3, delete=1, promote=2, do_nothing=5)
        mock_db = MagicMock()
        with patch("src.managarr.controller.run_cleanup", return_value=buckets), \
             patch("src.managarr.controller.PlexMediaDB", return_value=mock_db):
            resp = client.post("/managarr/cleanup/rules")
        assert resp.status_code == 202
        assert resp.get_json() == {
            "add_to_collection": 3,
            "delete": 1,
            "promote": 2,
            "do_nothing": 5,
        }

    def test_calls_db_init(self, client):
        buckets = self._mock_run_cleanup()
        mock_db = MagicMock()
        with patch("src.managarr.controller.run_cleanup", return_value=buckets), \
             patch("src.managarr.controller.PlexMediaDB", return_value=mock_db):
            client.post("/managarr/cleanup/rules")
        mock_db.init_db.assert_called_once()

    def test_db_receives_correct_path(self, client):
        buckets = self._mock_run_cleanup()
        with patch("src.managarr.controller.run_cleanup", return_value=buckets), \
             patch("src.managarr.controller.PlexMediaDB") as mock_cls, \
             patch("src.managarr.controller._MEDIA_DB_PATH", "/tmp/test_plex_media.db"):
            mock_cls.return_value = MagicMock()
            client.post("/managarr/cleanup/rules")
        mock_cls.assert_called_once_with("/tmp/test_plex_media.db")

    def test_run_cleanup_receives_db(self, client):
        mock_db = MagicMock()
        with patch("src.managarr.controller.PlexMediaDB", return_value=mock_db), \
             patch("src.managarr.controller.run_cleanup", return_value=([], [], [], 0)) as mock_rc:
            client.post("/managarr/cleanup/rules")
        mock_rc.assert_called_once_with(db=mock_db)


# ---------------------------------------------------------------------------
# GET|POST /managarr/cleanup/schedule
# ---------------------------------------------------------------------------

class TestScheduleRoutes:
    def test_get_schedule_returns_enabled_by_default(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.managarr.schedule._SCHEDULE_PATH", path):
            resp = client.get("/managarr/cleanup/schedule")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": True}

    def test_post_schedule_disables(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.managarr.schedule._SCHEDULE_PATH", path):
            resp = client.post("/managarr/cleanup/schedule?enabled=false")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": False}

    def test_post_schedule_enables(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.managarr.schedule._SCHEDULE_PATH", path):
            client.post("/managarr/cleanup/schedule?enabled=false")
            resp = client.post("/managarr/cleanup/schedule?enabled=true")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": True}

    def test_post_schedule_missing_param_returns_400(self, client):
        resp = client.post("/managarr/cleanup/schedule")
        assert resp.status_code == 400
        assert "enabled" in resp.get_json()["error"]

    def test_post_schedule_invalid_param_returns_400(self, client):
        resp = client.post("/managarr/cleanup/schedule?enabled=yes")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET|DELETE /managarr/cleanup/jobs + retry
# ---------------------------------------------------------------------------

class TestJobRoutes:
    def test_get_jobs_returns_list(self, client, job_mocks):
        job_mocks["list_jobs"].return_value = [{"id": 1, "status": "pending", "meta": "{}"}]
        resp = client.get("/managarr/cleanup/jobs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["meta"] == {}

    def test_get_jobs_passes_status_filter(self, client, job_mocks):
        client.get("/managarr/cleanup/jobs?status=done")
        job_mocks["list_jobs"].assert_called_once_with("done")

    def test_delete_jobs_missing_status_returns_400(self, client, job_mocks):
        resp = client.delete("/managarr/cleanup/jobs")
        assert resp.status_code == 400
        assert "status" in resp.get_json()["error"]

    def test_delete_jobs_calls_clear(self, client, job_mocks):
        job_mocks["clear_jobs"].return_value = 3
        resp = client.delete("/managarr/cleanup/jobs?status=done")
        assert resp.status_code == 200
        assert resp.get_json() == {"deleted": 3}
        job_mocks["clear_jobs"].assert_called_once_with("done")

    def test_retry_job_not_found_returns_404(self, client, job_mocks):
        job_mocks["requeue_job"].return_value = False
        resp = client.post("/managarr/cleanup/jobs/99/retry")
        assert resp.status_code == 404

    def test_retry_job_found_returns_202(self, client, job_mocks):
        resp = client.post("/managarr/cleanup/jobs/1/retry")
        assert resp.status_code == 202
