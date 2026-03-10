"""Tests for src/managarr/worker.py — action dispatch and concurrency guards."""

from unittest.mock import MagicMock, patch

from src.managarr.worker import (
    _add_to_collection,
    _plex_collection_lock,
    _radarr_lock,
    _remove_from_collection,
    _sonarr_lock,
)


def _plex(plex_key=1, title="Test", collections=None, location="/media_cache/foo.mkv"):
    item = MagicMock()
    item.title = title
    item.location = location
    item.collections = [MagicMock(tag=c) for c in (collections or [])]
    plex = MagicMock()
    plex.fetchItem.return_value = item
    return plex, item


# ---------------------------------------------------------------------------
# Plex collection lock
# ---------------------------------------------------------------------------

class TestPlexCollectionLock:
    def test_add_to_collection_holds_lock_during_addItems(self):
        """_plex_collection_lock must be held when addItems is called."""
        lock_held_during_call = []

        def fake_addItems(items):
            lock_held_during_call.append(_plex_collection_lock.locked())

        plex, item = _plex()
        item.collections = []
        section = MagicMock()
        collection = MagicMock()
        collection.addItems.side_effect = fake_addItems
        section.collection.return_value = collection
        item.section.return_value = section

        _add_to_collection(plex, 1, {"title": "Test"}, job_id=1, dry_run=False)

        assert lock_held_during_call == [True]

    def test_remove_from_collection_holds_lock_during_removeItems(self):
        lock_held_during_call = []

        def fake_removeItems(items):
            lock_held_during_call.append(_plex_collection_lock.locked())

        name = "Cleanup Queue"
        plex, item = _plex(collections=[name])
        collection = MagicMock()
        collection.removeItems.side_effect = fake_removeItems
        item.section.return_value.collection.return_value = collection

        with patch("src.managarr.worker._collection_name", return_value=name):
            _remove_from_collection(item, dry_run=False, job_id=1)

        assert lock_held_during_call == [True]

    def test_collection_lock_not_held_on_dry_run(self):
        """No Plex API calls (and thus no lock needed) in dry_run mode."""
        plex, item = _plex()
        item.collections = []

        _add_to_collection(plex, 1, {"title": "Test"}, job_id=1, dry_run=True)

        assert not _plex_collection_lock.locked()


# ---------------------------------------------------------------------------
# Arr service locks
# ---------------------------------------------------------------------------

class TestArrLocks:
    def test_radarr_lock_held_during_update_movie_path(self):
        lock_held = []

        with patch("src.managarr.worker.radarr_service") as mock_radarr, \
             patch("src.managarr.worker.sonarr_service"):
            def fake_update(tmdb_id, new_root):
                lock_held.append(_radarr_lock.locked())
            mock_radarr.get_movie_by_tmdb.return_value = {
                "rootFolderPath": "/media_cache/movies"
            }
            mock_radarr.update_movie_path.side_effect = fake_update

            plex, item = _plex()
            item.collections = []
            from src.managarr.worker import _promote
            _promote(plex, 1, {
                "title": "Foo",
                "location": "/media_cache/movies/Foo.mkv",
                "media_type": "movie",
                "tmdb_id": 123,
            }, job_id=1, dry_run=False)

        assert lock_held == [True]

    def test_sonarr_lock_held_during_update_series(self):
        lock_held = []

        with patch("src.managarr.worker.sonarr_service") as mock_sonarr, \
             patch("src.managarr.worker.radarr_service"):
            def fake_update(series):
                lock_held.append(_sonarr_lock.locked())
            mock_sonarr.get_series_by_tvdb.return_value = {
                "rootFolderPath": "/media_cache/tv",
                "path": "/media_cache/tv/Show",
            }
            mock_sonarr.update_series.side_effect = fake_update

            plex, item = _plex()
            item.collections = []
            from src.managarr.worker import _promote
            _promote(plex, 1, {
                "title": "Show",
                "location": "/media_cache/tv/Show/s01e01.mkv",
                "media_type": "show",
                "tvdb_id": 456,
            }, job_id=1, dry_run=False)

        assert lock_held == [True]

    def test_radarr_lock_held_during_delete_movie(self):
        lock_held = []

        with patch("src.managarr.worker.radarr_service") as mock_radarr:
            def fake_delete(movie_id, delete_files):
                lock_held.append(_radarr_lock.locked())
            mock_radarr.get_movie_by_tmdb.return_value = {"id": 7}
            mock_radarr.delete_movie.side_effect = fake_delete

            plex, item = _plex()
            from src.managarr.worker import _delete
            _delete(plex, 1, {
                "title": "Foo",
                "media_type": "movie",
                "tmdb_id": 123,
            }, job_id=1, dry_run=False)

        assert lock_held == [True]

    def test_delete_continuing_show_deletes_files_only(self):
        """sonarr_continuing=True → delete_episode_files called, delete_series not called."""
        with patch("src.managarr.worker.sonarr_service") as mock_sonarr:
            mock_sonarr.get_series_by_tvdb.return_value = {"id": 42}

            plex, _ = _plex()
            from src.managarr.worker import _delete
            _delete(plex, 1, {
                "title": "Show",
                "media_type": "show",
                "tvdb_id": 100,
                "sonarr_continuing": True,
            }, job_id=1, dry_run=False)

        mock_sonarr.delete_episode_files.assert_called_once_with(42)
        mock_sonarr.delete_series.assert_not_called()

    def test_delete_ended_show_deletes_series(self):
        """sonarr_continuing=False → delete_series called, delete_episode_files not called."""
        with patch("src.managarr.worker.sonarr_service") as mock_sonarr:
            mock_sonarr.get_series_by_tvdb.return_value = {"id": 42}

            plex, _ = _plex()
            from src.managarr.worker import _delete
            _delete(plex, 1, {
                "title": "Show",
                "media_type": "show",
                "tvdb_id": 100,
                "sonarr_continuing": False,
            }, job_id=1, dry_run=False)

        mock_sonarr.delete_series.assert_called_once_with(42, delete_files=True)
        mock_sonarr.delete_episode_files.assert_not_called()

    def test_delete_continuing_show_dry_run(self):
        """dry_run=True → no Sonarr calls at all."""
        with patch("src.managarr.worker.sonarr_service") as mock_sonarr:
            mock_sonarr.get_series_by_tvdb.return_value = {"id": 42}

            plex, _ = _plex()
            from src.managarr.worker import _delete
            _delete(plex, 1, {
                "title": "Show",
                "media_type": "show",
                "tvdb_id": 100,
                "sonarr_continuing": True,
            }, job_id=1, dry_run=True)

        mock_sonarr.delete_episode_files.assert_not_called()
        mock_sonarr.delete_series.assert_not_called()
