"""Integration tests for the /transcode-webhook and /transcode/jobs endpoints."""

import json
import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock
from flask import Flask

from src.transcode.controller import bp


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
def mocks():
    """Patches all external dependencies used by the transcode controller."""
    with ExitStack() as stack:
        yield {
            "get_list": stack.enter_context(
                patch("src.transcode.controller.config.get_list", return_value=[])
            ),
            "radarr_tag": stack.enter_context(
                patch("src.transcode.controller.radarr_service.get_or_create_tag", return_value=99)
            ),
            "sonarr_tag": stack.enter_context(
                patch("src.transcode.controller.sonarr_service.get_or_create_tag", return_value=99)
            ),
            "enqueue": stack.enter_context(
                patch("src.transcode.controller.enqueue_job")
            ),
            "list_jobs": stack.enter_context(
                patch("src.transcode.controller.list_jobs", return_value=[])
            ),
            "requeue": stack.enter_context(
                patch("src.transcode.controller.requeue_job", return_value=True)
            ),
        }


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

RADARR_PAYLOAD = {
    "eventType": "Download",
    "movie": {
        "id": 42,
        "title": "Test Movie",
        "originalLanguage": {"name": "English"},
        "tags": [],
    },
    "movieFile": {
        "path": "/media/movies/Test Movie (2020)/Test Movie.mkv",
        "releaseGroup": "SomeGroup",
        "mediaInfo": {
            "videoCodec": "AVC",
            "videoBitrate": 12000,
            "audioChannels": 6,
            "audioLanguages": "English",
        },
    },
}

SONARR_PAYLOAD = {
    "eventType": "Download",
    "series": {
        "id": 7,
        "title": "Test Show",
        "originalLanguage": {"name": "Japanese"},
        "tags": [],
    },
    "episodeFile": {
        "path": "/media/tv/Test Show/Season 01/S01E01.mkv",
        "releaseGroup": "SomeGroup",
        "mediaInfo": {
            "videoCodec": "x265",
            "videoBitrate": 3000,
            "audioChannels": 2,
            "audioLanguages": "Japanese",
        },
    },
}


def _post(client, payload, query=""):
    return client.post(
        f"/transcode-webhook{query}",
        data=json.dumps(payload),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /transcode-webhook — event filtering
# ---------------------------------------------------------------------------

class TestEventFiltering:
    def test_non_download_event_ignored(self, client, mocks):
        rv = _post(client, {"eventType": "Test"})
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()

    def test_grab_event_ignored(self, client, mocks):
        rv = _post(client, {"eventType": "Grab", "movieFile": {"path": "/x.mkv"}})
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()

    def test_missing_file_info_ignored(self, client, mocks):
        rv = _post(client, {"eventType": "Download"})
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()

    def test_empty_body_ignored(self, client, mocks):
        rv = client.post("/transcode-webhook", data=b"", content_type="application/json")
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()


# ---------------------------------------------------------------------------
# POST /transcode-webhook — Radarr happy path
# ---------------------------------------------------------------------------

class TestRadarrEnqueue:
    def test_returns_202(self, client, mocks):
        rv = _post(client, RADARR_PAYLOAD)
        assert rv.status_code == 202

    def test_enqueues_correct_path(self, client, mocks):
        _post(client, RADARR_PAYLOAD)
        path, meta = mocks["enqueue"].call_args.args
        assert path == RADARR_PAYLOAD["movieFile"]["path"]

    def test_meta_arr_type_and_id(self, client, mocks):
        _post(client, RADARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["arr_type"] == "radarr"
        assert meta["arr_id"] == 42

    def test_meta_codec_and_bitrate(self, client, mocks):
        _post(client, RADARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["codec"] == "AVC"
        assert meta["bitrate_kbps"] == 12000

    def test_meta_has_51_true_for_6_channels(self, client, mocks):
        _post(client, RADARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["has_51"] is True

    def test_meta_orig_lang_from_original_language(self, client, mocks):
        _post(client, RADARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "eng"

    def test_meta_dry_run_false_by_default(self, client, mocks):
        _post(client, RADARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["dry_run"] is False


# ---------------------------------------------------------------------------
# POST /transcode-webhook — Sonarr happy path
# ---------------------------------------------------------------------------

class TestSonarrEnqueue:
    def test_returns_202(self, client, mocks):
        rv = _post(client, SONARR_PAYLOAD)
        assert rv.status_code == 202

    def test_enqueues_correct_path(self, client, mocks):
        _post(client, SONARR_PAYLOAD)
        path, meta = mocks["enqueue"].call_args.args
        assert path == SONARR_PAYLOAD["episodeFile"]["path"]

    def test_meta_arr_type_sonarr(self, client, mocks):
        _post(client, SONARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["arr_type"] == "sonarr"
        assert meta["arr_id"] == 7

    def test_meta_has_51_false_for_2_channels(self, client, mocks):
        _post(client, SONARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["has_51"] is False

    def test_meta_orig_lang_japanese(self, client, mocks):
        _post(client, SONARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "jpn"


# ---------------------------------------------------------------------------
# POST /transcode-webhook — skip logic
# ---------------------------------------------------------------------------

class TestSkipLogic:
    def test_trusted_group_skipped(self, client, mocks):
        mocks["get_list"].return_value = ["yify", "yts", "judas"]
        payload = {**RADARR_PAYLOAD, "movieFile": {**RADARR_PAYLOAD["movieFile"], "releaseGroup": "YIFY"}}
        rv = _post(client, payload)
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()

    def test_skip_group_case_insensitive(self, client, mocks):
        mocks["get_list"].return_value = ["yify"]
        payload = {**RADARR_PAYLOAD, "movieFile": {**RADARR_PAYLOAD["movieFile"], "releaseGroup": "YiFy"}}
        rv = _post(client, payload)
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()

    def test_unknown_group_not_skipped(self, client, mocks):
        mocks["get_list"].return_value = ["yify", "yts"]
        rv = _post(client, RADARR_PAYLOAD)
        assert rv.status_code == 202
        mocks["enqueue"].assert_called_once()

    def test_already_transcoded_tag_skipped(self, client, mocks):
        mocks["radarr_tag"].return_value = 5
        payload = {
            **RADARR_PAYLOAD,
            "movie": {**RADARR_PAYLOAD["movie"], "tags": [5]},
        }
        rv = _post(client, payload)
        assert rv.status_code == 200
        mocks["enqueue"].assert_not_called()

    def test_different_tag_not_skipped(self, client, mocks):
        mocks["radarr_tag"].return_value = 5
        payload = {
            **RADARR_PAYLOAD,
            "movie": {**RADARR_PAYLOAD["movie"], "tags": [3, 4]},
        }
        rv = _post(client, payload)
        assert rv.status_code == 202
        mocks["enqueue"].assert_called_once()

    def test_tag_check_exception_still_enqueues(self, client, mocks):
        """If the Arr tag API is unreachable, we log a warning and proceed."""
        mocks["radarr_tag"].side_effect = Exception("connection refused")
        rv = _post(client, RADARR_PAYLOAD)
        assert rv.status_code == 202
        mocks["enqueue"].assert_called_once()


# ---------------------------------------------------------------------------
# POST /transcode-webhook — dry_run query param
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_true(self, client, mocks):
        rv = _post(client, RADARR_PAYLOAD, "?dry_run=true")
        assert rv.status_code == 202
        _, meta = mocks["enqueue"].call_args.args
        assert meta["dry_run"] is True

    def test_dry_run_false_explicit(self, client, mocks):
        rv = _post(client, RADARR_PAYLOAD, "?dry_run=false")
        assert rv.status_code == 202
        _, meta = mocks["enqueue"].call_args.args
        assert meta["dry_run"] is False

    def test_dry_run_absent_defaults_false(self, client, mocks):
        rv = _post(client, RADARR_PAYLOAD)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["dry_run"] is False


# ---------------------------------------------------------------------------
# POST /transcode-webhook — language mapping
# ---------------------------------------------------------------------------

class TestLanguageMapping:
    @pytest.mark.parametrize("lang_name,expected", [
        ("English",    "eng"),
        ("Japanese",   "jpn"),
        ("French",     "fra"),
        ("Spanish",    "spa"),
        ("German",     "deu"),
        ("Korean",     "kor"),
        ("Chinese",    "zho"),
        ("Portuguese", "por"),
        ("Italian",    "ita"),
    ])
    def test_known_language_mapped(self, client, mocks, lang_name, expected):
        payload = {**RADARR_PAYLOAD, "movie": {**RADARR_PAYLOAD["movie"], "originalLanguage": {"name": lang_name}}}
        _post(client, payload)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == expected

    def test_unknown_language_returns_none(self, client, mocks):
        payload = {**RADARR_PAYLOAD, "movie": {**RADARR_PAYLOAD["movie"], "originalLanguage": {"name": "Klingon"}}}
        _post(client, payload)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] is None

    def test_missing_original_language_falls_back_to_audio_languages(self, client, mocks):
        payload = {
            **RADARR_PAYLOAD,
            "movie": {"id": 1, "title": "X", "tags": []},  # no originalLanguage
            "movieFile": {
                **RADARR_PAYLOAD["movieFile"],
                "mediaInfo": {**RADARR_PAYLOAD["movieFile"]["mediaInfo"], "audioLanguages": "French"},
            },
        }
        _post(client, payload)
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "fra"


# ---------------------------------------------------------------------------
# GET /transcode/jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_returns_all_jobs(self, client, mocks):
        mocks["list_jobs"].return_value = [
            {"id": 1, "path": "/a.mkv", "status": "pending", "meta": "{}"},
            {"id": 2, "path": "/b.mkv", "status": "done",    "meta": "{}"},
        ]
        rv = client.get("/transcode/jobs")
        assert rv.status_code == 200
        data = rv.get_json()
        assert len(data["jobs"]) == 2
        mocks["list_jobs"].assert_called_once_with(None)

    def test_status_filter_passed_to_list_jobs(self, client, mocks):
        mocks["list_jobs"].return_value = []
        rv = client.get("/transcode/jobs?status=pending")
        assert rv.status_code == 200
        mocks["list_jobs"].assert_called_once_with("pending")

    def test_empty_result(self, client, mocks):
        mocks["list_jobs"].return_value = []
        rv = client.get("/transcode/jobs")
        assert rv.status_code == 200
        assert rv.get_json() == {"jobs": []}


# ---------------------------------------------------------------------------
# POST /transcode/jobs/<id>/retry
# ---------------------------------------------------------------------------

class TestRetryJob:
    def test_found_job_returns_202(self, client, mocks):
        mocks["requeue"].return_value = True
        rv = client.post("/transcode/jobs/1/retry")
        assert rv.status_code == 202

    def test_not_found_returns_404(self, client, mocks):
        mocks["requeue"].return_value = False
        rv = client.post("/transcode/jobs/99/retry")
        assert rv.status_code == 404
        assert "error" in rv.get_json()

    def test_dry_run_passed_to_requeue(self, client, mocks):
        mocks["requeue"].return_value = True
        client.post("/transcode/jobs/1/retry?dry_run=true")
        mocks["requeue"].assert_called_once_with(1, dry_run=True)

    def test_dry_run_false_by_default(self, client, mocks):
        mocks["requeue"].return_value = True
        client.post("/transcode/jobs/1/retry")
        mocks["requeue"].assert_called_once_with(1, dry_run=False)
