"""Unit tests for src/managarr/rules.py — process_rules and _resolve state machine."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.managarr.rules import Action, RuleResult, _resolve, process_rules, _collection_days
from src.managarr.service import MovieMetadata, ShowMetadata, SeasonMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _movie(
    *,
    user_rating=None,
    view_count=0,
    last_viewed=None,
    date_added=None,
    location=None,
) -> MovieMetadata:
    return MovieMetadata(
        title="Test Movie",
        plex_key=1,
        user_rating=user_rating,
        view_count=view_count,
        last_viewed=last_viewed,
        date_added=date_added,
        location=location,
    )


def _show(
    *,
    user_rating=None,
    view_count=0,
    last_viewed=None,
    date_added=None,
    location=None,
) -> ShowMetadata:
    return ShowMetadata(
        title="Test Show",
        plex_key=2,
        user_rating=user_rating,
        view_count=view_count,
        last_viewed=last_viewed,
        date_added=date_added,
        location=location,
        seasons=[],
        total_episodes=0,
    )


def _record(state: str, days_ago: int = 0) -> dict:
    """Fake DB record with state_changed_at set to `days_ago` days ago."""
    changed = (datetime.utcnow() - timedelta(days=days_ago)).isoformat(sep=" ", timespec="seconds")
    return {"state": state, "state_changed_at": changed}


# ---------------------------------------------------------------------------
# process_rules — rating rules
# ---------------------------------------------------------------------------

def test_rated_above_6_returns_do_nothing():
    item = _movie(user_rating=8.0)
    assert process_rules(item).action == Action.DO_NOTHING


def test_rated_above_6_in_media_cache_returns_promote():
    item = _movie(user_rating=7.5, location="/media_cache/movies/Film.mkv")
    assert process_rules(item).action == Action.PROMOTE


def test_rated_exactly_6_returns_delete():
    item = _movie(user_rating=6.0)
    assert process_rules(item).action == Action.DELETE


def test_rated_below_6_returns_delete():
    item = _movie(user_rating=4.0)
    assert process_rules(item).action == Action.DELETE


# ---------------------------------------------------------------------------
# process_rules — unrated rules
# ---------------------------------------------------------------------------

def test_unrated_unwatched_recent_returns_do_nothing():
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=30))
    assert process_rules(item).action == Action.DO_NOTHING


def test_unrated_unwatched_old_returns_add_to_collection():
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=61))
    assert process_rules(item).action == Action.ADD_TO_COLLECTION


def test_unrated_watched_recently_returns_do_nothing():
    item = _movie(
        user_rating=None,
        view_count=1,
        last_viewed=datetime.utcnow() - timedelta(days=5),
        date_added=datetime.utcnow() - timedelta(days=60),
    )
    assert process_rules(item).action == Action.DO_NOTHING


def test_unrated_watched_old_and_old_added_returns_add_to_collection():
    item = _movie(
        user_rating=None,
        view_count=1,
        last_viewed=datetime.utcnow() - timedelta(days=15),
        date_added=datetime.utcnow() - timedelta(days=31),
    )
    assert process_rules(item).action == Action.ADD_TO_COLLECTION


def test_unrated_watched_old_but_recently_added_returns_do_nothing():
    """date_added guard: recently added items should not be flagged even if last_viewed is old."""
    item = _movie(
        user_rating=None,
        view_count=1,
        last_viewed=datetime.utcnow() - timedelta(days=15),
        date_added=datetime.utcnow() - timedelta(days=4),
    )
    assert process_rules(item).action == Action.DO_NOTHING


def test_works_for_shows_too():
    item = _show(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=61))
    assert process_rules(item).action == Action.ADD_TO_COLLECTION


# ---------------------------------------------------------------------------
# _resolve — state machine transitions
# ---------------------------------------------------------------------------

def test_terminal_delete_stays_delete():
    item = _movie(user_rating=9.0)  # would normally be DO_NOTHING
    record = _record("delete")
    assert _resolve(item, record, datetime.utcnow()) == Action.DELETE


def test_add_to_collection_transitions_to_delete_after_30_days():
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=100))
    record = _record("add_to_collection", days_ago=_collection_days())
    assert _resolve(item, record, datetime.utcnow()) == Action.DELETE


def test_add_to_collection_stays_if_under_30_days():
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=100))
    record = _record("add_to_collection", days_ago=_collection_days() - 1)
    assert _resolve(item, record, datetime.utcnow()) == Action.ADD_TO_COLLECTION


def test_do_nothing_to_add_to_collection():
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=61))
    record = _record("do_nothing")
    assert _resolve(item, record, datetime.utcnow()) == Action.ADD_TO_COLLECTION


def test_do_nothing_to_delete_when_rated_poorly():
    item = _movie(user_rating=3.0)
    record = _record("do_nothing")
    assert _resolve(item, record, datetime.utcnow()) == Action.DELETE


def test_promote_cannot_transition_to_delete_via_rules():
    """PROMOTE → DELETE is not a valid transition; should stay PROMOTE when rule fires DELETE."""
    item = _movie(user_rating=3.0)   # rule says DELETE
    record = _record("promote")
    # DELETE is not in VALID_TRANSITIONS[PROMOTE], so should stay PROMOTE
    assert _resolve(item, record, datetime.utcnow()) == Action.PROMOTE


def test_new_item_no_record():
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=61))
    assert _resolve(item, None, datetime.utcnow()) == Action.ADD_TO_COLLECTION


# ---------------------------------------------------------------------------
# run_cleanup — DB write only on state change
# ---------------------------------------------------------------------------

def test_no_db_write_when_state_unchanged():
    """When resolved state equals current DB state, upsert_state must not be called."""
    item = _movie(user_rating=None, view_count=0, date_added=datetime.utcnow() - timedelta(days=61))
    # Current state is already add_to_collection (and timer not expired)
    record = _record("add_to_collection", days_ago=1)

    db = MagicMock()
    db.get_states.return_value = {item.plex_key: record}

    with patch("src.managarr.rules.get_movies", return_value=[item]), \
         patch("src.managarr.rules.get_shows", return_value=[]):
        from src.managarr.rules import run_cleanup
        run_cleanup(db=db)

    db.upsert_state.assert_not_called()
