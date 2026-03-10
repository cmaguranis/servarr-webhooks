"""Integration tests for POST /media-test/generate and GET /media-test/jobs."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from flask import Flask

import src.test_media.controller as ctrl
from src.test_media.controller import bp

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
    """Patches all external dependencies used by the test_media controller."""
    with ExitStack() as stack:
        yield {
            "isdir": stack.enter_context(
                patch("src.test_media.controller.os.path.isdir", return_value=True)
            ),
            "walk": stack.enter_context(
                patch("src.test_media.controller.os.walk", return_value=[])
            ),
            "get_signature": stack.enter_context(
                patch("src.test_media.controller.get_media_signature")
            ),
            "get_duration": stack.enter_context(
                patch("src.test_media.controller.get_duration", return_value=120.0)
            ),
            "build_output": stack.enter_context(
                patch("src.test_media.controller.build_output_path",
                      side_effect=lambda src, start, out_dir: f"{out_dir}/{start}s.mkv")
            ),
            "enqueue": stack.enter_context(
                patch("src.test_media.controller.enqueue_job", return_value=1)
            ),
            "list_jobs": stack.enter_context(
                patch("src.test_media.queue.list_jobs", return_value=[])
            ),
            "clear_jobs": stack.enter_context(
                patch("src.test_media.queue.clear_jobs", return_value=0)
            ),
            "radarr_map": stack.enter_context(
                patch("src.test_media.controller.radarr_service.get_path_movie_map", return_value={})
            ),
            "sonarr_map": stack.enter_context(
                patch("src.test_media.controller.sonarr_service.get_path_episode_map", return_value={})
            ),
            "randint": stack.enter_context(
                patch("src.test_media.controller.random.randint", return_value=50)
            ),
        }


def _walk_with_files(*files):
    """Returns an os.walk result listing files in a single directory."""
    return [("/data/media_cache/Movies", [], [f.split("/")[-1] for f in files])]


def _full_paths(*files):
    return [f"/data/media_cache/Movies/{f}" for f in files]


# ---------------------------------------------------------------------------
# POST /media-test/generate — cache dir validation
# ---------------------------------------------------------------------------

class TestCacheDir:
    def test_missing_cache_dir_returns_400(self, client, mocks):
        mocks["isdir"].return_value = False
        rv = client.post("/media-test/generate")
        assert rv.status_code == 400
        assert "error" in rv.get_json()

    def test_error_message_includes_path(self, client, mocks):
        mocks["isdir"].return_value = False
        rv = client.post("/media-test/generate")
        assert "/data/media_cache" in rv.get_json()["error"]


# ---------------------------------------------------------------------------
# POST /media-test/generate — no files found
# ---------------------------------------------------------------------------

class TestNoFiles:
    def test_empty_cache_returns_202(self, client, mocks):
        mocks["walk"].return_value = []
        rv = client.post("/media-test/generate")
        assert rv.status_code == 202

    def test_empty_cache_returns_empty_enqueued(self, client, mocks):
        mocks["walk"].return_value = []
        data = client.post("/media-test/generate").get_json()
        assert data["enqueued"] == []
        assert data["skipped"] == []

    def test_non_media_files_ignored(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache", [], ["cover.jpg", "info.txt"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        data = client.post("/media-test/generate").get_json()
        assert data["enqueued"] == []
        mocks["get_signature"].assert_not_called()


# ---------------------------------------------------------------------------
# POST /media-test/generate — signature deduplication
# ---------------------------------------------------------------------------

class TestSignatureDedup:
    def test_deduplicates_same_signature(self, client, mocks):
        sig = ("hevc", "eac3", 8)
        mocks["walk"].return_value = [
            ("/data/media_cache/Movies", [], ["a.mkv", "b.mkv"])
        ]
        mocks["get_signature"].return_value = sig

        data = client.post("/media-test/generate").get_json()
        # Two files with identical signature → only one enqueued
        assert len(data["enqueued"]) == 1

    def test_different_signatures_both_enqueued(self, client, mocks):
        mocks["walk"].return_value = [
            ("/data/media_cache/Movies", [], ["a.mkv", "b.mkv"])
        ]
        mocks["get_signature"].side_effect = [
            ("hevc", "eac3", 8),
            ("h264", "aac", 2),
        ]

        data = client.post("/media-test/generate").get_json()
        assert len(data["enqueued"]) == 2

    def test_signature_included_in_response(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["a.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)

        data = client.post("/media-test/generate").get_json()
        assert data["enqueued"][0]["signature"] == ["hevc", "eac3", 8]

    def test_probe_error_skips_file(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["a.mkv", "b.mkv"])]
        mocks["get_signature"].side_effect = [RuntimeError("probe failed"), ("h264", "aac", 2)]

        data = client.post("/media-test/generate").get_json()
        assert len(data["enqueued"]) == 1


# ---------------------------------------------------------------------------
# POST /media-test/generate — duration filtering
# ---------------------------------------------------------------------------

class TestDurationFilter:
    def test_skips_files_shorter_than_min(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["short.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "aac", 2)
        mocks["get_duration"].return_value = 20.0  # less than MIN_FILE_DURATION (35s)

        data = client.post("/media-test/generate").get_json()
        assert data["enqueued"] == []

    def test_accepts_files_at_exact_min_duration(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["ok.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "aac", 2)
        mocks["get_duration"].return_value = float(ctrl.MIN_FILE_DURATION)

        data = client.post("/media-test/generate").get_json()
        assert len(data["enqueued"]) == 1

    def test_duration_probe_error_skips_file(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["a.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "aac", 2)
        mocks["get_duration"].side_effect = RuntimeError("probe failed")

        data = client.post("/media-test/generate").get_json()
        assert data["enqueued"] == []


# ---------------------------------------------------------------------------
# POST /media-test/generate — enqueue behavior
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_enqueues_job_and_returns_job_id(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["enqueue"].return_value = 7

        data = client.post("/media-test/generate").get_json()
        assert data["enqueued"][0]["job_id"] == 7

    def test_enqueued_entry_contains_source_output_start(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["randint"].return_value = 42

        data = client.post("/media-test/generate").get_json()
        entry = data["enqueued"][0]
        assert "source" in entry
        assert "output" in entry
        assert entry["start_sec"] == 42

    def test_duplicate_job_counted_in_skipped(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["a.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["enqueue"].return_value = None  # INSERT OR IGNORE no-op

        data = client.post("/media-test/generate").get_json()
        assert len(data["skipped"]) == 1
        assert data["enqueued"] == []

    def test_returns_202(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        rv = client.post("/media-test/generate")
        assert rv.status_code == 202

    def test_meta_passed_to_enqueue_job(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["randint"].return_value = 100

        client.post("/media-test/generate")

        _, meta = mocks["enqueue"].call_args.args
        assert meta["start_sec"] == 100
        assert meta["duration_sec"] == ctrl.SLICE_DURATION
        assert meta["dry_run"] is False

    def test_arr_orig_lang_stored_in_meta_from_radarr(self, client, mocks):
        src_path = "/data/media_cache/Movies/film.mkv"
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["radarr_map"].return_value = {
            src_path: {"id": 42, "originalLanguage": {"name": "Japanese"}}
        }

        client.post("/media-test/generate")
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "jpn"
        assert meta["arr_type"] == "radarr"
        assert meta["arr_id"] == 42

    def test_arr_orig_lang_stored_in_meta_from_sonarr(self, client, mocks):
        src_path = "/data/media_cache/TV/show.mkv"
        mocks["walk"].return_value = [("/data/media_cache/TV", [], ["show.mkv"])]
        mocks["get_signature"].return_value = ("h264", "aac", 2)
        mocks["sonarr_map"].return_value = {
            src_path: {
                "series": {"id": 7, "originalLanguage": {"name": "Korean"}},
                "episode_file": {"path": src_path},
            }
        }

        client.post("/media-test/generate")
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] == "kor"
        assert meta["arr_type"] == "sonarr"
        assert meta["arr_id"] == 7

    def test_arr_data_stored_in_meta(self, client, mocks):
        src_path = "/data/media_cache/Movies/film.mkv"
        movie = {"id": 42, "title": "Test", "originalLanguage": {"name": "English"}}
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["radarr_map"].return_value = {src_path: movie}

        client.post("/media-test/generate")
        _, meta = mocks["enqueue"].call_args.args
        assert meta["arr_data"] == movie

    def test_no_arr_match_stores_null_orig_lang(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        # Both maps return empty — no match

        client.post("/media-test/generate")
        _, meta = mocks["enqueue"].call_args.args
        assert meta["orig_lang"] is None
        assert meta["arr_type"] is None

    def test_arr_lookup_failure_does_not_abort_generate(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)
        mocks["radarr_map"].side_effect = Exception("connection refused")
        mocks["sonarr_map"].side_effect = Exception("connection refused")

        rv = client.post("/media-test/generate")
        assert rv.status_code == 202
        assert len(rv.get_json()["enqueued"]) == 1


# ---------------------------------------------------------------------------
# POST /media-test/generate — dry_run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_call_enqueue(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)

        client.post("/media-test/generate?dry_run=true")
        mocks["enqueue"].assert_not_called()

    def test_dry_run_returns_preview_entries(self, client, mocks):
        mocks["walk"].return_value = [("/data/media_cache/Movies", [], ["film.mkv"])]
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)

        data = client.post("/media-test/generate?dry_run=true").get_json()
        assert data["dry_run"] is True
        assert len(data["enqueued"]) == 1
        assert data["enqueued"][0]["job_id"] is None

    def test_dry_run_false_in_response_when_not_set(self, client, mocks):
        data = client.post("/media-test/generate").get_json()
        assert data["dry_run"] is False


# ---------------------------------------------------------------------------
# GET /media-test/jobs
# ---------------------------------------------------------------------------

class TestGetJobs:
    def test_returns_200_with_jobs_key(self, client, mocks):
        mocks["list_jobs"].return_value = []
        rv = client.get("/media-test/jobs")
        assert rv.status_code == 200
        assert "jobs" in rv.get_json()

    def test_status_filter_passed_to_list_jobs(self, client, mocks):
        mocks["list_jobs"].return_value = []
        client.get("/media-test/jobs?status=done")
        mocks["list_jobs"].assert_called_once_with("done")

    def test_no_filter_passes_none(self, client, mocks):
        mocks["list_jobs"].return_value = []
        client.get("/media-test/jobs")
        mocks["list_jobs"].assert_called_once_with(None)

    def test_meta_deserialized_from_string(self, client, mocks):
        mocks["list_jobs"].return_value = [
            {"id": 1, "path": "/out.mkv", "status": "done",
             "meta": '{"source_path": "/src.mkv", "start_sec": 42}'}
        ]
        data = client.get("/media-test/jobs").get_json()
        meta = data["jobs"][0]["meta"]
        assert isinstance(meta, dict)
        assert meta["start_sec"] == 42

    def test_meta_already_dict_not_broken(self, client, mocks):
        mocks["list_jobs"].return_value = [
            {"id": 1, "path": "/out.mkv", "status": "done",
             "meta": {"source_path": "/src.mkv"}}
        ]
        data = client.get("/media-test/jobs").get_json()
        # dict meta passes through (not double-deserialized)
        assert data["jobs"][0]["meta"] == {"source_path": "/src.mkv"}


# ---------------------------------------------------------------------------
# DELETE /media-test/jobs
# ---------------------------------------------------------------------------

class TestDeleteJobs:
    def test_missing_status_returns_400(self, client, mocks):
        rv = client.delete("/media-test/jobs")
        assert rv.status_code == 400
        assert "status" in rv.get_json()["error"]

    def test_returns_deleted_count(self, client, mocks):
        mocks["clear_jobs"].return_value = 3
        rv = client.delete("/media-test/jobs?status=done")
        assert rv.status_code == 200
        assert rv.get_json()["deleted"] == 3

    def test_passes_status_to_clear_jobs(self, client, mocks):
        client.delete("/media-test/jobs?status=failed")
        mocks["clear_jobs"].assert_called_once_with("failed")

    def test_zero_deleted_still_returns_200(self, client, mocks):
        mocks["clear_jobs"].return_value = 0
        rv = client.delete("/media-test/jobs?status=pending")
        assert rv.status_code == 200
        assert rv.get_json()["deleted"] == 0


# ---------------------------------------------------------------------------
# POST /media-test/generate — include_media flag
# ---------------------------------------------------------------------------

class TestIncludeMedia:
    def test_include_media_missing_dir_returns_400(self, client, mocks):
        # cache dir exists, media dir does not
        mocks["isdir"].side_effect = lambda p: p == ctrl.MEDIA_TEST_CACHE_DIR
        rv = client.post("/media-test/generate?include_media=true")
        assert rv.status_code == 400
        assert "error" in rv.get_json()
        assert ctrl.MEDIA_DIR in rv.get_json()["error"]

    def test_include_media_scans_both_dirs(self, client, mocks):
        # Return one file from each directory
        def _walk(root):
            if "cache" in root:
                return [("/data/media_cache/Movies", [], ["cache_film.mkv"])]
            else:
                return [("/media/Movies", [], ["media_film.mkv"])]

        mocks["walk"].side_effect = _walk
        mocks["get_signature"].side_effect = [
            ("hevc", "eac3", 8),   # cache_film.mkv
            ("h264", "aac", 2),    # media_film.mkv
        ]

        data = client.post("/media-test/generate?include_media=true").get_json()
        assert len(data["enqueued"]) == 2

    def test_without_include_media_only_scans_cache(self, client, mocks):
        call_count = 0

        def _walk(root):
            nonlocal call_count
            call_count += 1
            return [("/data/media_cache/Movies", [], ["film.mkv"])]

        mocks["walk"].side_effect = _walk
        mocks["get_signature"].return_value = ("hevc", "eac3", 8)

        client.post("/media-test/generate")
        assert call_count == 1

    def test_include_media_false_default(self, client, mocks):
        # Identical to not passing the param — media dir not checked
        mocks["isdir"].return_value = True
        mocks["walk"].return_value = []
        rv = client.post("/media-test/generate?include_media=false")
        assert rv.status_code == 202
