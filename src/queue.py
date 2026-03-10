import json
import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)

_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  path       TEXT NOT NULL UNIQUE,
  meta       TEXT,
  status     TEXT NOT NULL DEFAULT 'pending',
  priority   INTEGER NOT NULL DEFAULT 1,
  error      TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


class JobQueue:
    def __init__(self, db_path: str, table: str):
        self._db_path = db_path
        self._table = table
        self._lock = threading.Lock()

    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        dir_ = os.path.dirname(self._db_path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(_SCHEMA_TEMPLATE.format(table=self._table))
                for migration in [
                    f"ALTER TABLE {self._table} ADD COLUMN priority INTEGER NOT NULL DEFAULT 1",
                    f"ALTER TABLE {self._table} ADD COLUMN error TEXT",
                ]:
                    try:
                        conn.execute(migration)
                    except Exception:
                        pass  # column already exists
                conn.execute(
                    f"UPDATE {self._table} SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE status='processing'"
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"{self._table} DB initialized")

    def enqueue_job(self, path: str, meta: dict, priority: int = 1) -> int | None:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO {self._table} (path, meta, status, priority) VALUES (?, ?, 'pending', ?)",
                    (path, json.dumps(meta), priority),
                )
                conn.commit()
                job_id = cur.lastrowid if cur.rowcount else None
            finally:
                conn.close()
        if job_id:
            logger.info(f"[job {job_id}] Enqueued: {path}")
        else:
            logger.info(f"Skipped duplicate enqueue: {path}")
        return job_id

    def claim_pending_jobs(self, limit: int = 10) -> list:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT * FROM {self._table} WHERE status='pending' ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    conn.execute(
                        f"UPDATE {self._table} SET status='processing', updated_at=CURRENT_TIMESTAMP WHERE id IN ({','.join('?' * len(ids))})",
                        ids,
                    )
                    conn.commit()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def mark_done(self, job_id: int, result: str | None = None):
        self._set_status(job_id, "done", {"result": result} if result else None)

    def mark_failed(self, job_id: int, error: str | None = None):
        self._set_status(job_id, "failed", error=error)

    def _set_status(self, job_id: int, status: str, meta_update: dict | None = None, error: str | None = None):
        with self._lock:
            conn = self._connect()
            try:
                if meta_update:
                    row = conn.execute(
                        f"SELECT meta FROM {self._table} WHERE id=?", (job_id,)
                    ).fetchone()
                    meta = json.loads((row["meta"] if row else None) or "{}")
                    meta.update(meta_update)
                    conn.execute(
                        f"UPDATE {self._table} SET status=?, meta=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (status, json.dumps(meta), error, job_id),
                    )
                else:
                    conn.execute(
                        f"UPDATE {self._table} SET status=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (status, error, job_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_job_by_path(self, path: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT * FROM {self._table} WHERE path=?", (path,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def list_jobs(self, status: str | None = None) -> list:
        with self._lock:
            conn = self._connect()
            try:
                if status:
                    rows = conn.execute(
                        f"SELECT * FROM {self._table} WHERE status=? ORDER BY updated_at DESC",
                        (status,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT * FROM {self._table} ORDER BY updated_at DESC"
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def requeue_job(self, job_id: int, dry_run: bool = False) -> bool:
        """Reset a job to pending. Returns False if job not found or currently processing."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT meta, status FROM {self._table} WHERE id=?", (job_id,)
                ).fetchone()
                if not row:
                    return False
                if row["status"] == "processing":
                    return False
                meta = json.loads(row["meta"] or "{}")
                meta["dry_run"] = dry_run
                conn.execute(
                    f"UPDATE {self._table} SET status='pending', meta=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(meta), job_id),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def defer_job(self, job_id: int):
        """Reset a processing job back to pending for automatic retry."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"UPDATE {self._table} SET status='pending', updated_at=CURRENT_TIMESTAMP "
                    f"WHERE id=? AND status='processing'",
                    (job_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def clear_jobs(self, status: str) -> int:
        """Delete all jobs with the given status. Returns the number of rows deleted."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"DELETE FROM {self._table} WHERE status=?", (status,)
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def cleanup_jobs(self, done_days: int = 7, failed_days: int = 21):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"""
                    DELETE FROM {self._table}
                    WHERE (status='done'   AND updated_at < datetime('now', '-{done_days} days'))
                       OR (status='failed' AND updated_at < datetime('now', '-{failed_days} days'))
                    """
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"{self._table} job cleanup complete")


class QueueModule:
    """Facade over JobQueue. Instantiate with db_path + table; override only domain methods."""

    def __init__(self, db_path: str, table: str):
        self._q = JobQueue(db_path, table)

    def init_db(self): self._q.init_db()

    def enqueue_job(self, path: str, meta: dict, priority: int = 1) -> int | None:
        return self._q.enqueue_job(path, meta, priority)

    def claim_pending_jobs(self, limit: int = 10) -> list: return self._q.claim_pending_jobs(limit)

    def mark_done(self, job_id: int, result: str | None = None): self._q.mark_done(job_id, result)

    def mark_failed(self, job_id: int, error: str | None = None): self._q.mark_failed(job_id, error)

    def get_job_by_path(self, path: str) -> dict | None: return self._q.get_job_by_path(path)

    def list_jobs(self, status: str | None = None) -> list: return self._q.list_jobs(status)

    def requeue_job(self, job_id: int, dry_run: bool = False) -> bool: return self._q.requeue_job(job_id, dry_run)

    def defer_job(self, job_id: int): self._q.defer_job(job_id)

    def clear_jobs(self, status: str) -> int: return self._q.clear_jobs(status)

    def cleanup_jobs(self, done_days: int = 7, failed_days: int = 21): self._q.cleanup_jobs(done_days, failed_days)
