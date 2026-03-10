import os
import logging
import sqlite3
import threading

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_state (
  plex_key         INTEGER PRIMARY KEY,
  media_type       TEXT NOT NULL,
  title            TEXT NOT NULL,
  location         TEXT,
  tmdb_id          INTEGER,
  tvdb_id          INTEGER,
  state            TEXT NOT NULL DEFAULT 'do_nothing',
  state_changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


class PlexMediaDB:
    def __init__(self, db_path: str):
        self._db_path = db_path
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
                conn.execute(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
        logger.info("media_state DB initialized")

    def get_states(self, plex_keys: list[int]) -> dict[int, dict]:
        """Batch-read states for a list of plex_keys. Returns {plex_key: row_dict}."""
        if not plex_keys:
            return {}
        placeholders = ",".join("?" * len(plex_keys))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT * FROM media_state WHERE plex_key IN ({placeholders})",
                    plex_keys,
                ).fetchall()
                return {row["plex_key"]: dict(row) for row in rows}
            finally:
                conn.close()

    def upsert_state(
        self,
        plex_key: int,
        state: str,
        media_type: str,
        title: str,
        location: str | None,
        tmdb_id: int | None,
        tvdb_id: int | None,
    ) -> None:
        """Insert or update a media item's state. Always updates state_changed_at (caller
        is responsible for only calling this when the state actually changed)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO media_state
                        (plex_key, media_type, title, location, tmdb_id, tvdb_id, state, state_changed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(plex_key) DO UPDATE SET
                        media_type       = excluded.media_type,
                        title            = excluded.title,
                        location         = excluded.location,
                        tmdb_id          = excluded.tmdb_id,
                        tvdb_id          = excluded.tvdb_id,
                        state            = excluded.state,
                        state_changed_at = CURRENT_TIMESTAMP
                    """,
                    (plex_key, media_type, title, location, tmdb_id, tvdb_id, state),
                )
                conn.commit()
            finally:
                conn.close()

    def batch_upsert(self, items: list[dict]) -> None:
        """Upsert multiple items in a single transaction."""
        if not items:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    """
                    INSERT INTO media_state
                        (plex_key, media_type, title, location, tmdb_id, tvdb_id, state, state_changed_at)
                    VALUES (:plex_key, :media_type, :title, :location, :tmdb_id, :tvdb_id, :state, CURRENT_TIMESTAMP)
                    ON CONFLICT(plex_key) DO UPDATE SET
                        media_type       = excluded.media_type,
                        title            = excluded.title,
                        location         = excluded.location,
                        tmdb_id          = excluded.tmdb_id,
                        tvdb_id          = excluded.tvdb_id,
                        state            = excluded.state,
                        state_changed_at = CURRENT_TIMESTAMP
                    """,
                    items,
                )
                conn.commit()
            finally:
                conn.close()

    def get_items_by_state(self, state: str) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM media_state WHERE state=? ORDER BY state_changed_at ASC",
                    (state,),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
