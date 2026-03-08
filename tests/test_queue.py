"""Unit tests for the generic JobQueue class."""

import json
import sqlite3
import pytest

from src.queue import JobQueue


def _backdate(q: JobQueue):
    """Set updated_at to a distant past date so cleanup thresholds are met."""
    conn = sqlite3.connect(q._db_path)
    conn.execute(f"UPDATE {q._table} SET updated_at = '2000-01-01 00:00:00'")
    conn.commit()
    conn.close()


@pytest.fixture
def q(tmp_path):
    queue = JobQueue(db_path=str(tmp_path / "test.db"), table="test_jobs")
    queue.init_db()
    return queue


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_table(self, q):
        # If table didn't exist, enqueue would raise; prove it works
        job_id = q.enqueue_job("/a.mkv", {})
        assert job_id is not None

    def test_resets_processing_to_pending(self, tmp_path):
        """Jobs stuck in 'processing' at startup should be reset to 'pending'."""
        q = JobQueue(db_path=str(tmp_path / "test.db"), table="test_jobs")
        q.init_db()
        q.enqueue_job("/a.mkv", {})
        q.claim_pending_jobs(limit=1)  # moves to processing

        # Simulate restart
        q2 = JobQueue(db_path=str(tmp_path / "test.db"), table="test_jobs")
        q2.init_db()

        jobs = q2.list_jobs("pending")
        assert len(jobs) == 1
        assert jobs[0]["path"] == "/a.mkv"


# ---------------------------------------------------------------------------
# enqueue_job
# ---------------------------------------------------------------------------

class TestEnqueueJob:
    def test_returns_job_id(self, q):
        job_id = q.enqueue_job("/a.mkv", {"key": "val"})
        assert isinstance(job_id, int)

    def test_stores_pending_status(self, q):
        q.enqueue_job("/a.mkv", {})
        jobs = q.list_jobs("pending")
        assert jobs[0]["path"] == "/a.mkv"
        assert jobs[0]["status"] == "pending"

    def test_duplicate_returns_none(self, q):
        q.enqueue_job("/a.mkv", {})
        job_id = q.enqueue_job("/a.mkv", {})
        assert job_id is None

    def test_duplicate_does_not_add_extra_row(self, q):
        q.enqueue_job("/a.mkv", {})
        q.enqueue_job("/a.mkv", {})
        assert len(q.list_jobs()) == 1

    def test_meta_serialized(self, q):
        q.enqueue_job("/a.mkv", {"codec": "hevc", "channels": 6})
        job = q.list_jobs()[0]
        meta = json.loads(job["meta"])
        assert meta["codec"] == "hevc"
        assert meta["channels"] == 6


# ---------------------------------------------------------------------------
# claim_pending_jobs
# ---------------------------------------------------------------------------

class TestClaimPendingJobs:
    def test_returns_pending_jobs(self, q):
        q.enqueue_job("/a.mkv", {})
        jobs = q.claim_pending_jobs(limit=1)
        assert len(jobs) == 1
        assert jobs[0]["path"] == "/a.mkv"

    def test_marks_claimed_jobs_as_processing(self, q):
        q.enqueue_job("/a.mkv", {})
        q.claim_pending_jobs(limit=1)
        assert len(q.list_jobs("processing")) == 1
        assert len(q.list_jobs("pending")) == 0

    def test_respects_limit(self, q):
        for i in range(5):
            q.enqueue_job(f"/{i}.mkv", {})
        claimed = q.claim_pending_jobs(limit=2)
        assert len(claimed) == 2
        assert len(q.list_jobs("processing")) == 2
        assert len(q.list_jobs("pending")) == 3

    def test_returns_empty_when_none_pending(self, q):
        assert q.claim_pending_jobs(limit=10) == []


# ---------------------------------------------------------------------------
# mark_done / mark_failed
# ---------------------------------------------------------------------------

class TestMarkStatus:
    def test_mark_done(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_done(job["id"])
        assert q.list_jobs("done")[0]["status"] == "done"

    def test_mark_failed(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_failed(job["id"])
        assert q.list_jobs("failed")[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_no_filter_returns_all(self, q):
        q.enqueue_job("/a.mkv", {})
        q.enqueue_job("/b.mkv", {})
        assert len(q.list_jobs()) == 2

    def test_filter_by_status(self, q):
        q.enqueue_job("/a.mkv", {})
        q.enqueue_job("/b.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_done(job["id"])

        assert len(q.list_jobs("done")) == 1
        assert len(q.list_jobs("pending")) == 1

    def test_returns_empty_list_when_no_jobs(self, q):
        assert q.list_jobs() == []


# ---------------------------------------------------------------------------
# requeue_job
# ---------------------------------------------------------------------------

class TestRequeueJob:
    def test_resets_done_to_pending(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_done(job["id"])
        assert q.requeue_job(job["id"]) is True
        assert q.list_jobs("pending")[0]["status"] == "pending"

    def test_resets_failed_to_pending(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_failed(job["id"])
        assert q.requeue_job(job["id"]) is True
        assert q.list_jobs("pending")[0]["status"] == "pending"

    def test_sets_dry_run_in_meta(self, q):
        q.enqueue_job("/a.mkv", {"dry_run": False})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_done(job["id"])
        q.requeue_job(job["id"], dry_run=True)
        meta = json.loads(q.list_jobs("pending")[0]["meta"])
        assert meta["dry_run"] is True

    def test_returns_false_for_missing_job(self, q):
        assert q.requeue_job(999) is False

    def test_returns_false_for_processing_job(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        assert q.requeue_job(job["id"]) is False


# ---------------------------------------------------------------------------
# cleanup_jobs
# ---------------------------------------------------------------------------

class TestCleanupJobs:
    def test_deletes_old_done_jobs(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_done(job["id"])
        _backdate(q)
        q.cleanup_jobs(done_days=7, failed_days=21)
        assert q.list_jobs("done") == []

    def test_deletes_old_failed_jobs(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_failed(job["id"])
        _backdate(q)
        q.cleanup_jobs(done_days=7, failed_days=21)
        assert q.list_jobs("failed") == []

    def test_retains_recent_jobs(self, q):
        q.enqueue_job("/a.mkv", {})
        job = q.claim_pending_jobs(limit=1)[0]
        q.mark_done(job["id"])
        q.cleanup_jobs(done_days=7, failed_days=21)
        assert len(q.list_jobs("done")) == 1

    def test_does_not_delete_pending_or_processing(self, q):
        q.enqueue_job("/a.mkv", {})
        q.enqueue_job("/b.mkv", {})
        q.claim_pending_jobs(limit=1)
        q.cleanup_jobs(done_days=0, failed_days=0)
        assert len(q.list_jobs("pending")) == 1
        assert len(q.list_jobs("processing")) == 1
