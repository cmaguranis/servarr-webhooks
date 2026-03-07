import os
import json
import sqlite3
import logging
import threading

from src import config

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("TRANSCODE_DB", "/config/data/transcode_queue.db")
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcode_jobs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  path       TEXT NOT NULL UNIQUE,
  meta       TEXT,
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _lock:
        conn = _connect()
        try:
            conn.execute(_SCHEMA)
            # Reset any jobs that were interrupted mid-transcode
            conn.execute(
                "UPDATE transcode_jobs SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE status='processing'"
            )
            conn.commit()
        finally:
            conn.close()
    logger.info("Transcode DB initialized")


def enqueue_job(path: str, meta: dict):
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO transcode_jobs (path, meta, status) VALUES (?, ?, 'pending')",
                (path, json.dumps(meta)),
            )
            conn.commit()
            job_id = cur.lastrowid if cur.rowcount else None
        finally:
            conn.close()
    if job_id:
        logger.info(f"[job {job_id}] Enqueued: {path}")
    else:
        logger.info(f"Skipped duplicate enqueue: {path}")


def claim_pending_jobs(limit: int = 10) -> list:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM transcode_jobs WHERE status='pending' ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                conn.execute(
                    f"UPDATE transcode_jobs SET status='processing', updated_at=CURRENT_TIMESTAMP WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                conn.commit()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def mark_done(job_id: int):
    _set_status(job_id, "done")


def mark_failed(job_id: int):
    _set_status(job_id, "failed")


def _set_status(job_id: int, status: str):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE transcode_jobs SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, job_id),
            )
            conn.commit()
        finally:
            conn.close()


def list_jobs(status: str | None = None) -> list:
    with _lock:
        conn = _connect()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM transcode_jobs WHERE status=? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM transcode_jobs ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def requeue_job(job_id: int, dry_run: bool = False) -> bool:
    """Reset a job to pending. Returns False if job not found or currently processing."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT meta, status FROM transcode_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                return False
            if row["status"] == "processing":
                return False
            meta = json.loads(row["meta"] or "{}")
            meta["dry_run"] = dry_run
            conn.execute(
                "UPDATE transcode_jobs SET status='pending', meta=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(meta), job_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def cleanup_jobs():
    done_days = int(config.get("transcode", "cleanup_done_days", fallback="7"))
    failed_days = int(config.get("transcode", "cleanup_failed_days", fallback="21"))
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                f"""
                DELETE FROM transcode_jobs
                WHERE (status='done'   AND updated_at < datetime('now', '-{done_days} days'))
                   OR (status='failed' AND updated_at < datetime('now', '-{failed_days} days'))
                """
            )
            conn.commit()
        finally:
            conn.close()
    logger.info("Transcode job cleanup complete")
