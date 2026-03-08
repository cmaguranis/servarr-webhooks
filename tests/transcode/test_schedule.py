"""Tests for src/transcode/schedule.py and the /transcode/schedule API."""

import json
import pytest
from unittest.mock import patch
from flask import Flask

from src.transcode.controller import bp
from src.transcode import schedule


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


# ---------------------------------------------------------------------------
# schedule.is_enabled / set_enabled
# ---------------------------------------------------------------------------

class TestScheduleFlag:
    def test_defaults_to_enabled_when_file_missing(self, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            assert schedule.is_enabled() is True

    def test_set_enabled_false_persists(self, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            schedule.set_enabled(False)
            assert schedule.is_enabled() is False

    def test_set_enabled_true_persists(self, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            schedule.set_enabled(False)
            schedule.set_enabled(True)
            assert schedule.is_enabled() is True

    def test_written_file_is_valid_json(self, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            schedule.set_enabled(False)
        with open(path) as f:
            data = json.load(f)
        assert data == {"enabled": False}

    def test_reads_enabled_true_from_file(self, tmp_path):
        path = str(tmp_path / "schedule.json")
        with open(path, "w") as f:
            json.dump({"enabled": True}, f)
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            assert schedule.is_enabled() is True

    def test_missing_key_defaults_to_enabled(self, tmp_path):
        path = str(tmp_path / "schedule.json")
        with open(path, "w") as f:
            json.dump({}, f)
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            assert schedule.is_enabled() is True


# ---------------------------------------------------------------------------
# GET /transcode/schedule
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_returns_enabled_true_by_default(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            resp = client.get("/transcode/schedule")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": True}

    def test_returns_enabled_false_when_disabled(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            schedule.set_enabled(False)
            resp = client.get("/transcode/schedule")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": False}


# ---------------------------------------------------------------------------
# POST /transcode/schedule
# ---------------------------------------------------------------------------

class TestSetSchedule:
    def test_disable_returns_enabled_false(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            resp = client.post("/transcode/schedule?enabled=false")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": False}

    def test_enable_returns_enabled_true(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            schedule.set_enabled(False)
            resp = client.post("/transcode/schedule?enabled=true")
        assert resp.status_code == 200
        assert resp.get_json() == {"enabled": True}

    def test_persists_across_get(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            client.post("/transcode/schedule?enabled=false")
            resp = client.get("/transcode/schedule")
        assert resp.get_json() == {"enabled": False}

    def test_missing_param_returns_400(self, client):
        resp = client.post("/transcode/schedule")
        assert resp.status_code == 400
        assert "enabled" in resp.get_json()["error"]

    def test_invalid_param_returns_400(self, client):
        resp = client.post("/transcode/schedule?enabled=yes")
        assert resp.status_code == 400

    def test_case_insensitive_true(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            resp = client.post("/transcode/schedule?enabled=TRUE")
        # "true" after .lower() → valid
        assert resp.status_code == 200
        assert resp.get_json()["enabled"] is True

    def test_case_insensitive_false(self, client, tmp_path):
        path = str(tmp_path / "schedule.json")
        with patch("src.transcode.schedule._SCHEDULE_PATH", path):
            resp = client.post("/transcode/schedule?enabled=FALSE")
        assert resp.status_code == 200
        assert resp.get_json()["enabled"] is False
