"""Unit tests for the generic Worker class (_run logic)."""

import json
import pytest
from unittest.mock import MagicMock, call

from src.worker_base import Worker


def _make_worker(execute_fn=None, on_complete=None, cleanup_fn=None):
    """Build a Worker with a mock queue, not started."""
    queue = MagicMock()
    worker = Worker(
        name="test-worker",
        queue=queue,
        execute_fn=execute_fn or MagicMock(),
        on_complete=on_complete,
        cleanup_fn=cleanup_fn,
        worker_count=1,
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
        queue.mark_done.assert_called_once_with(1)

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
        queue.mark_failed.assert_called_once_with(1)

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
