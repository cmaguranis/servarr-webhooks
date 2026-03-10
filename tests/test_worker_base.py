"""Unit tests for the generic Worker class (_run logic)."""

import json
import pytest
from unittest.mock import MagicMock, call

from src import file_op_lock
from src.worker_base import Worker, DeferJobError


def _make_worker(execute_fn=None, on_complete=None, cleanup_fn=None, paused_fn=None, lock_path_fn=None):
    """Build a Worker with a mock queue, not started."""
    queue = MagicMock()
    worker = Worker(
        name="test-worker",
        queue=queue,
        execute_fn=execute_fn or MagicMock(),
        on_complete=on_complete,
        cleanup_fn=cleanup_fn,
        worker_count=1,
        paused_fn=paused_fn,
        lock_path_fn=lock_path_fn,
    )
    return worker, queue


def _make_job(job_id=1, path="/a.mkv", meta=None, status="pending"):
    return {
        "id": job_id,
        "path": path,
        "meta": json.dumps(meta or {}),
        "status": status,
    }


# ---------------------------------------------------------------------------
# _run — success path
# ---------------------------------------------------------------------------

class TestRunSuccess:
    def test_calls_execute_fn_with_correct_args(self):
        execute_fn = MagicMock()
        worker, queue = _make_worker(execute_fn=execute_fn)
        job = _make_job(meta={"codec": "hevc"})

        worker._run(job)

        execute_fn.assert_called_once_with(
            "/a.mkv", {"codec": "hevc"}, 1, False
        )

    def test_marks_done_on_success(self):
        worker, queue = _make_worker()
        worker._run(_make_job())
        queue.mark_done.assert_called_once_with(1, result="ok")

    def test_calls_on_complete_after_success(self):
        on_complete = MagicMock()
        worker, queue = _make_worker(on_complete=on_complete)
        worker._run(_make_job(meta={"arr_id": 42}))
        on_complete.assert_called_once_with(1, {"arr_id": 42})

    def test_no_on_complete_does_not_raise(self):
        worker, queue = _make_worker(on_complete=None)
        worker._run(_make_job())  # should not raise
        queue.mark_done.assert_called_once()

    def test_mark_failed_not_called_on_success(self):
        worker, queue = _make_worker()
        worker._run(_make_job())
        queue.mark_failed.assert_not_called()


# ---------------------------------------------------------------------------
# _run — dry_run path
# ---------------------------------------------------------------------------

class TestRunDryRun:
    def test_requeues_with_dry_run_false(self):
        worker, queue = _make_worker()
        worker._run(_make_job(meta={"dry_run": True}))
        queue.requeue_job.assert_called_once_with(1, dry_run=False)

    def test_does_not_mark_done_on_dry_run(self):
        worker, queue = _make_worker()
        worker._run(_make_job(meta={"dry_run": True}))
        queue.mark_done.assert_not_called()

    def test_does_not_call_on_complete_on_dry_run(self):
        on_complete = MagicMock()
        worker, queue = _make_worker(on_complete=on_complete)
        worker._run(_make_job(meta={"dry_run": True}))
        on_complete.assert_not_called()

    def test_passes_dry_run_true_to_execute_fn(self):
        execute_fn = MagicMock()
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job(meta={"dry_run": True}))
        _, _, _, dry_run = execute_fn.call_args.args
        assert dry_run is True


# ---------------------------------------------------------------------------
# _run — failure path
# ---------------------------------------------------------------------------

class TestRunFailure:
    def test_marks_failed_on_exception(self):
        execute_fn = MagicMock(side_effect=RuntimeError("boom"))
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job())
        queue.mark_failed.assert_called_once_with(1, error="boom")

    def test_error_message_stored_in_mark_failed(self):
        execute_fn = MagicMock(side_effect=RuntimeError("ffmpeg exited 1: error details"))
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job())
        assert "ffmpeg exited 1" in queue.mark_failed.call_args.kwargs["error"]

    def test_mark_done_not_called_on_exception(self):
        execute_fn = MagicMock(side_effect=RuntimeError("boom"))
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job())
        queue.mark_done.assert_not_called()

    def test_on_complete_not_called_on_exception(self):
        execute_fn = MagicMock(side_effect=RuntimeError("boom"))
        on_complete = MagicMock()
        worker, queue = _make_worker(execute_fn=execute_fn, on_complete=on_complete)
        worker._run(_make_job())
        on_complete.assert_not_called()

    def test_does_not_propagate_exception(self):
        execute_fn = MagicMock(side_effect=ValueError("unexpected"))
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job())  # should not raise


# ---------------------------------------------------------------------------
# cleanup_fn
# ---------------------------------------------------------------------------

class TestCleanupFn:
    def test_uses_provided_cleanup_fn(self):
        cleanup_fn = MagicMock()
        worker, queue = _make_worker(cleanup_fn=cleanup_fn)
        assert worker._cleanup_fn is cleanup_fn

    def test_defaults_to_queue_cleanup_jobs(self):
        worker, queue = _make_worker(cleanup_fn=None)
        assert worker._cleanup_fn is queue.cleanup_jobs


# ---------------------------------------------------------------------------
# paused_fn
# ---------------------------------------------------------------------------

class TestPausedFn:
    def test_paused_fn_none_by_default(self):
        worker, _ = _make_worker()
        assert worker._paused_fn is None

    def test_paused_fn_stored(self):
        paused_fn = MagicMock(return_value=True)
        worker, _ = _make_worker(paused_fn=paused_fn)
        assert worker._paused_fn is paused_fn

    def test_loop_skips_claim_when_paused(self):
        """When paused_fn returns True, claim_pending_jobs must not be called."""
        import threading

        paused_fn = MagicMock(return_value=True)
        worker, queue = _make_worker(paused_fn=paused_fn)

        stop = threading.Event()
        original_wait = worker._stop_flag.wait

        call_count = 0

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                worker._stop_flag.set()
            return original_wait(0)  # return immediately

        worker._stop_flag.wait = fake_wait
        from concurrent.futures import ThreadPoolExecutor
        worker._executor = ThreadPoolExecutor(max_workers=1)
        worker._loop()

        queue.claim_pending_jobs.assert_not_called()

    def test_loop_claims_when_not_paused(self):
        """When paused_fn returns False, claim_pending_jobs is called."""
        import threading

        paused_fn = MagicMock(return_value=False)
        worker, queue = _make_worker(paused_fn=paused_fn)
        queue.claim_pending_jobs.return_value = []

        call_count = 0
        original_wait = worker._stop_flag.wait

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                worker._stop_flag.set()
            return original_wait(0)

        worker._stop_flag.wait = fake_wait
        from concurrent.futures import ThreadPoolExecutor
        worker._executor = ThreadPoolExecutor(max_workers=1)
        worker._loop()

        queue.claim_pending_jobs.assert_called()


# ---------------------------------------------------------------------------
# lock_path_fn / file_op_lock integration
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_file_op_lock():
    file_op_lock._active.clear()
    yield
    file_op_lock._active.clear()


class TestFileLock:
    def test_defers_when_file_lock_held(self):
        """Job is deferred (not executed) when its file path is already locked."""
        file_op_lock._active.add("/media/foo.mkv")
        execute_fn = MagicMock()
        worker, queue = _make_worker(
            execute_fn=execute_fn,
            lock_path_fn=lambda path, meta: path,
        )
        worker._run(_make_job(path="/media/foo.mkv"))

        execute_fn.assert_not_called()
        queue.defer_job.assert_called_once_with(1)
        queue.mark_done.assert_not_called()
        queue.mark_failed.assert_not_called()

    def test_proceeds_when_file_lock_free(self):
        execute_fn = MagicMock()
        worker, queue = _make_worker(
            execute_fn=execute_fn,
            lock_path_fn=lambda path, meta: path,
        )
        worker._run(_make_job(path="/media/foo.mkv"))

        execute_fn.assert_called_once()
        queue.mark_done.assert_called_once()

    def test_lock_released_on_success(self):
        worker, queue = _make_worker(lock_path_fn=lambda path, meta: path)
        worker._run(_make_job(path="/media/foo.mkv"))
        assert "/media/foo.mkv" not in file_op_lock._active

    def test_lock_released_on_failure(self):
        execute_fn = MagicMock(side_effect=RuntimeError("boom"))
        worker, queue = _make_worker(
            execute_fn=execute_fn,
            lock_path_fn=lambda path, meta: path,
        )
        worker._run(_make_job(path="/media/foo.mkv"))
        assert "/media/foo.mkv" not in file_op_lock._active

    def test_no_lock_when_lock_path_fn_returns_none(self):
        execute_fn = MagicMock()
        worker, queue = _make_worker(
            execute_fn=execute_fn,
            lock_path_fn=lambda path, meta: None,
        )
        worker._run(_make_job(path="/media/foo.mkv"))

        execute_fn.assert_called_once()
        assert len(file_op_lock._active) == 0

    def test_no_lock_when_no_lock_path_fn(self):
        execute_fn = MagicMock()
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job(path="/media/foo.mkv"))

        execute_fn.assert_called_once()
        assert len(file_op_lock._active) == 0

    def test_defer_job_error_resets_to_pending(self):
        execute_fn = MagicMock(side_effect=DeferJobError("not ready"))
        worker, queue = _make_worker(execute_fn=execute_fn)
        worker._run(_make_job())

        queue.defer_job.assert_called_once_with(1)
        queue.mark_failed.assert_not_called()
        queue.mark_done.assert_not_called()
