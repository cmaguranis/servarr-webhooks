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
                patch("src.transcode.queue.list_jobs", return_value=[])
            ),
            "clear_jobs": stack.enter_context(
                patch("src.transcode.queue.clear_jobs", return_value=0)
            ),
            "requeue": stack.enter_context(
                patch("src.transcode.queue.requeue_job", return_value=True)
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
        "id": 10,
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
        "id": 20,
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
# DELETE /transcode/jobs
# ---------------------------------------------------------------------------

class TestDeleteJobs:
    def test_missing_status_returns_400(self, client, mocks):
        rv = client.delete("/transcode/jobs")
        assert rv.status_code == 400
        assert "status" in rv.get_json()["error"]

    def test_returns_deleted_count(self, client, mocks):
        mocks["clear_jobs"].return_value = 5
        rv = client.delete("/transcode/jobs?status=done")
        assert rv.status_code == 200
        assert rv.get_json()["deleted"] == 5

    def test_passes_status_to_clear_jobs(self, client, mocks):
        client.delete("/transcode/jobs?status=failed")
        mocks["clear_jobs"].assert_called_once_with("failed")

    def test_zero_deleted_still_returns_200(self, client, mocks):
        mocks["clear_jobs"].return_value = 0
        rv = client.delete("/transcode/jobs?status=done")
        assert rv.status_code == 200
        assert rv.get_json()["deleted"] == 0


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


# ---------------------------------------------------------------------------
# POST /transcode/enqueue-folder
# ---------------------------------------------------------------------------

_PROBE_RESULT = {
    "format": {"bit_rate": "8000000"},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "eac3", "channels": 8,
         "tags": {"language": "eng"}},
    ],
}


@pytest.fixture
def folder_mocks():
    with ExitStack() as stack:
        yield {
            "isdir": stack.enter_context(
                patch("src.transcode.controller.os.path.isdir", return_value=True)
            ),
            "walk": stack.enter_context(
                patch("src.transcode.controller.os.walk", return_value=[])
            ),
            "probe": stack.enter_context(
                patch("src.transcode.controller.get_stream_info", return_value=_PROBE_RESULT)
            ),
            "enqueue": stack.enter_context(
                patch("src.transcode.controller.enqueue_job", return_value=1)
            ),
            "radarr_map": stack.enter_context(
                patch("src.transcode.controller.radarr_service.get_path_lang_map", return_value={})
            ),
            "sonarr_map": stack.enter_context(
                patch("src.transcode.controller.sonarr_service.get_path_lang_map", return_value={})
            ),
            "media_test_job": stack.enter_context(
                patch("src.transcode.controller.get_media_test_job", return_value=None)
            ),
        }


class TestEnqueueFolder:
    def _post(self, client, path="/data/media_test", query=""):
        return client.post(
            f"/transcode/enqueue-folder{query}",
            data=json.dumps({"path": path}),
            content_type="application/json",
        )

    def test_missing_path_returns_400(self, client, folder_mocks):
        rv = client.post("/transcode/enqueue-folder",
                         data=json.dumps({}), content_type="application/json")
        assert rv.status_code == 400
        assert "path" in rv.get_json()["error"]

    def test_nonexistent_dir_returns_400(self, client, folder_mocks):
        folder_mocks["isdir"].return_value = False
        rv = self._post(client)
        assert rv.status_code == 400
        assert "does not exist" in rv.get_json()["error"]

    def test_empty_folder_returns_202_with_empty_lists(self, client, folder_mocks):
        folder_mocks["walk"].return_value = []
        rv = self._post(client)
        assert rv.status_code == 202
        data = rv.get_json()
        assert data["enqueued"] == []
        assert data["skipped"] == []
        assert data["errors"] == []

    def test_non_media_files_ignored(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["cover.jpg", "info.txt"])]
        rv = self._post(client)
        assert rv.get_json()["enqueued"] == []
        folder_mocks["probe"].assert_not_called()

    def test_enqueues_media_file_and_returns_job(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [
            ("/data/media_test", [], ["clip.mkv"])
        ]
        rv = self._post(client)
        data = rv.get_json()
        assert len(data["enqueued"]) == 1
        assert data["enqueued"][0]["job_id"] == 1
        assert data["enqueued"][0]["path"] == "/data/media_test/clip.mkv"

    def test_meta_derived_from_probe(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        self._post(client)
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["codec"] == "hevc"
        assert meta["has_51"] is True   # 8 channels > 5
        assert meta["orig_lang"] == "eng"
        assert meta["arr_type"] is None
        assert meta["arr_id"] is None

    def test_bitrate_converted_to_kbps(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        self._post(client)
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["bitrate_kbps"] == 8000  # 8_000_000 bps → 8000 kbps

    def test_duplicate_counted_in_skipped(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        folder_mocks["enqueue"].return_value = None
        data = self._post(client).get_json()
        assert data["skipped"] == ["/data/media_test/clip.mkv"]
        assert data["enqueued"] == []

    def test_probe_error_counted_in_errors(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        folder_mocks["probe"].side_effect = Exception("ffprobe failed")
        data = self._post(client).get_json()
        assert len(data["errors"]) == 1
        assert data["errors"][0]["path"] == "/data/media_test/clip.mkv"
        assert data["enqueued"] == []

    def test_dry_run_set_in_meta(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        self._post(client, query="?dry_run=true")
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["dry_run"] is True

    def test_multiple_files_all_enqueued(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [
            ("/data/media_test", [], ["a.mkv", "b.mkv", "c.mp4"])
        ]
        folder_mocks["enqueue"].side_effect = [1, 2, 3]
        data = self._post(client).get_json()
        assert len(data["enqueued"]) == 3

    def test_eng_preferred_over_first_audio_stream(self, client, folder_mocks):
        # ITA first, ENG second — should pick eng as orig_lang
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["dubbed.mkv"])]
        folder_mocks["probe"].return_value = {
            "format": {"bit_rate": "4000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "ita"}},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "eng"}},
            ],
        }
        self._post(client)
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "eng"

    def test_has_51_based_on_selected_lang_stream(self, client, folder_mocks):
        # ENG is stereo, ITA is 5.1 — has_51 should be False (ENG selected)
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["dubbed.mkv"])]
        folder_mocks["probe"].return_value = {
            "format": {"bit_rate": "4000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "channels": 6, "tags": {"language": "ita"}},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "eng"}},
            ],
        }
        self._post(client)
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "eng"
        assert meta["has_51"] is False

    def test_media_test_stored_lang_takes_priority(self, client, folder_mocks):
        # Clip has eng+ita audio; media_test queue says orig_lang=jpn (anime dub)
        clip_path = "/data/media_test/clip.mkv"
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        folder_mocks["media_test_job"].return_value = {
            "meta": json.dumps({"orig_lang": "jpn", "source_path": "/media/anime/ep.mkv"})
        }
        folder_mocks["probe"].return_value = {
            "format": {"bit_rate": "4000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "hevc"},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "jpn"}},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "eng"}},
            ],
        }
        self._post(client)
        folder_mocks["media_test_job"].assert_called_once_with(clip_path)
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "jpn"

    def test_arr_lang_map_used_when_no_media_test_job(self, client, folder_mocks):
        # File has eng+ita tracks; Arr map says original is Japanese
        folder_mocks["walk"].return_value = [("/media/anime", [], ["ep.mkv"])]
        folder_mocks["sonarr_map"].return_value = {"/media/anime/ep.mkv": "Japanese"}
        folder_mocks["probe"].return_value = {
            "format": {"bit_rate": "4000000"},
            "streams": [
                {"codec_type": "video", "codec_name": "hevc"},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "jpn"}},
                {"codec_type": "audio", "channels": 2, "tags": {"language": "eng"}},
            ],
        }
        self._post(client, path="/media/anime")
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "jpn"

    def test_arr_lookup_failure_falls_back_to_heuristic(self, client, folder_mocks):
        folder_mocks["walk"].return_value = [("/data/media_test", [], ["clip.mkv"])]
        folder_mocks["radarr_map"].side_effect = Exception("connection refused")
        folder_mocks["sonarr_map"].side_effect = Exception("connection refused")
        self._post(client)
        # Should still succeed via eng heuristic
        _, meta = folder_mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "eng"
