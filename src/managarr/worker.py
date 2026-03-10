import logging
import threading

from plexapi.exceptions import NotFound

from src import config, radarr_service, sonarr_service
from src.managarr import queue as plex_queue
from src.managarr import schedule
from src.managarr.service import _server
from src.worker_base import Worker

logger = logging.getLogger(__name__)

PLEX_CLEANUP_WORKERS = config.PLEX_WORKER_COUNT()

# Serialise all Plex collection mutations — concurrent addItems/removeItems
# calls to the same collection silently drop writes.
_plex_collection_lock = threading.Lock()

# Serialise write calls to each Arr service — both use SQLite internally
# and concurrent write-heavy requests cause SQLITE_BUSY contention.
_radarr_lock = threading.Lock()
_sonarr_lock = threading.Lock()


def _collection_name() -> str:
    return config.PLEX_COLLECTION_NAME()


def _remove_from_collection(item, dry_run: bool, job_id: int) -> bool:
    """Remove item from the cleanup collection if present. Returns True if removed."""
    name = _collection_name()
    for coll in item.collections:
        if coll.tag == name:
            if dry_run:
                logger.info(f"[job {job_id}] [dry_run] Would remove '{item.title}' from '{name}'")
            else:
                with _plex_collection_lock:
                    item.section().collection(name).removeItems([item])
                logger.info(f"[job {job_id}] Removed '{item.title}' from '{name}'")
            return True
    return False


def _do_nothing(plex, plex_key: int, meta: dict, job_id: int, dry_run: bool):
    item = plex.fetchItem(plex_key)
    removed = _remove_from_collection(item, dry_run, job_id)
    if not removed:
        logger.info(f"[job {job_id}] do_nothing: '{meta.get('title')}' not in collection")


def _add_to_collection(plex, plex_key: int, meta: dict, job_id: int, dry_run: bool):
    name = _collection_name()
    item = plex.fetchItem(plex_key)
    if dry_run:
        logger.info(f"[job {job_id}] [dry_run] Would add '{item.title}' to '{name}'")
        return
    for c in item.collections:
        if c.tag == name:
            logger.info(f"[job {job_id}] '{item.title}' already in '{name}'")
            return
    section = item.section()
    with _plex_collection_lock:
        try:
            section.collection(name).addItems([item])
        except NotFound:
            section.createCollection(name, items=[item])
    logger.info(f"[job {job_id}] Added '{item.title}' to '{name}'")


def _promote(plex, plex_key: int, meta: dict, job_id: int, dry_run: bool):
    title = meta.get("title", "")
    location = meta.get("location") or ""

    if "media_cache" not in location:
        logger.info(f"[job {job_id}] promote: '{title}' not in media_cache, skipping arr update")
    elif meta.get("media_type") == "movie":
        tmdb_id = meta.get("tmdb_id")
        if not tmdb_id:
            raise ValueError(f"promote: missing tmdb_id for '{title}'")
        movie = radarr_service.get_movie_by_tmdb(tmdb_id)
        if not movie:
            raise ValueError(f"promote: tmdbId={tmdb_id} not found in Radarr")
        new_root = movie["rootFolderPath"].replace("/media_cache", "/media")
        if dry_run:
            logger.info(f"[job {job_id}] [dry_run] Would promote movie '{title}' → {new_root}")
        else:
            with _radarr_lock:
                radarr_service.update_movie_path(tmdb_id, new_root)
            logger.info(f"[job {job_id}] Promoted movie '{title}' → {new_root}")
    elif meta.get("media_type") == "show":
        tvdb_id = meta.get("tvdb_id")
        if not tvdb_id:
            raise ValueError(f"promote: missing tvdb_id for '{title}'")
        series = sonarr_service.get_series_by_tvdb(tvdb_id)
        if not series:
            raise ValueError(f"promote: tvdbId={tvdb_id} not found in Sonarr")
        new_root = series["rootFolderPath"].replace("/media_cache", "/media")
        new_path = series["path"].replace("/media_cache", "/media")
        if dry_run:
            logger.info(f"[job {job_id}] [dry_run] Would promote show '{title}' → {new_root}")
        else:
            with _sonarr_lock:
                sonarr_service.update_series({**series, "rootFolderPath": new_root, "path": new_path})
            logger.info(f"[job {job_id}] Promoted show '{title}' → {new_root}")
    else:
        raise ValueError(f"promote: unknown media_type {meta.get('media_type')!r}")

    item = plex.fetchItem(plex_key)
    _remove_from_collection(item, dry_run, job_id)


def _delete(plex, plex_key: int, meta: dict, job_id: int, dry_run: bool):
    title = meta.get("title", "")
    if meta.get("media_type") == "movie":
        tmdb_id = meta.get("tmdb_id")
        if not tmdb_id:
            raise ValueError(f"delete: missing tmdb_id for '{title}'")
        movie = radarr_service.get_movie_by_tmdb(tmdb_id)
        if not movie:
            logger.warning(f"[job {job_id}] delete: '{title}' not found in Radarr (already deleted?)")
            return
        if dry_run:
            logger.info(f"[job {job_id}] [dry_run] Would delete movie '{title}' (radarr_id={movie['id']})")
        else:
            with _radarr_lock:
                radarr_service.delete_movie(movie["id"], delete_files=True)
            logger.info(f"[job {job_id}] Deleted movie '{title}' (radarr_id={movie['id']})")
    elif meta.get("media_type") == "show":
        tvdb_id = meta.get("tvdb_id")
        if not tvdb_id:
            raise ValueError(f"delete: missing tvdb_id for '{title}'")
        series = sonarr_service.get_series_by_tvdb(tvdb_id)
        if not series:
            logger.warning(f"[job {job_id}] delete: '{title}' not found in Sonarr (already deleted?)")
            return
        if meta.get("sonarr_continuing"):
            if dry_run:
                logger.info(f"[job {job_id}] [dry_run] Would delete episode files for '{title}' (sonarr_id={series['id']}, monitoring kept)")
            else:
                with _sonarr_lock:
                    sonarr_service.delete_episode_files(series["id"])
                logger.info(f"[job {job_id}] Deleted episode files for '{title}' (sonarr_id={series['id']}, monitoring kept)")
        else:
            if dry_run:
                logger.info(f"[job {job_id}] [dry_run] Would delete series '{title}' (sonarr_id={series['id']})")
            else:
                with _sonarr_lock:
                    sonarr_service.delete_series(series["id"], delete_files=True)
                logger.info(f"[job {job_id}] Deleted series '{title}' (sonarr_id={series['id']})")
    else:
        raise ValueError(f"delete: unknown media_type {meta.get('media_type')!r}")


_DISPATCH = {
    "do_nothing":        _do_nothing,
    "add_to_collection": _add_to_collection,
    "promote":           _promote,
    "delete":            _delete,
}


def _execute(path: str, meta: dict, job_id: int, dry_run: bool):
    plex_key = int(path)
    action = meta["action"]
    fn = _DISPATCH.get(action)
    if fn is None:
        raise ValueError(f"Unknown plex cleanup action: {action!r}")
    plex = _server()
    fn(plex, plex_key, meta, job_id, dry_run)


def init_db(db_path: str | None = None):
    plex_queue.init_db(db_path)


def start():
    worker = Worker(
        name="plex-cleanup-worker",
        queue=plex_queue._q(),
        execute_fn=_execute,
        worker_count=PLEX_CLEANUP_WORKERS,
        paused_fn=lambda: not schedule.is_enabled(),
        lock_path_fn=lambda path, meta: meta.get("location"),
    )
    worker.start()
