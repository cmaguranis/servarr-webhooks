from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from src import config
from .db import PlexMediaDB
from .service import MovieMetadata, ShowMetadata, get_movies, get_shows


class Action(str, Enum):
    DO_NOTHING = "do_nothing"
    ADD_TO_COLLECTION = "add_to_collection"
    PROMOTE = "promote"
    DELETE = "delete"


@dataclass
class RuleResult:
    action: Action
    media_type: str       # "movie" or "show"
    plex_key: int
    title: str
    location: Optional[str] = None
    tmdb_id: Optional[int] = None        # movies only
    tvdb_id: Optional[int] = None        # shows only
    user_rating: Optional[float] = None
    view_count: int = 0
    date_added: Optional[datetime] = None
    last_viewed: Optional[datetime] = None


def _collection_days() -> int:
    return config.PLEX_COLLECTION_DAYS()


def _movie_batch() -> int:
    return config.PLEX_MOVIE_BATCH()

_ALL = set(Action)

# Defines which states each current state may transition to.
# DO_NOTHING / None (new) are treated identically — any transition is allowed.
VALID_TRANSITIONS: dict[Action | None, set[Action]] = {
    Action.ADD_TO_COLLECTION: {Action.DO_NOTHING, Action.ADD_TO_COLLECTION, Action.PROMOTE, Action.DELETE},
    Action.PROMOTE:           {Action.DO_NOTHING, Action.ADD_TO_COLLECTION, Action.PROMOTE},
    Action.DELETE:            set(),
    # DO_NOTHING / None: _ALL (any transition allowed — handled as default)
}


def _now() -> datetime:
    return datetime.utcnow()


def _result(item: MovieMetadata | ShowMetadata, action: Action) -> RuleResult:
    common = dict(
        action=action,
        plex_key=item.plex_key,
        title=item.title,
        location=item.location,
        user_rating=item.user_rating,
        view_count=item.view_count,
        date_added=item.date_added,
        last_viewed=item.last_viewed,
    )
    if isinstance(item, MovieMetadata):
        return RuleResult(media_type="movie", tmdb_id=item.tmdb_id, **common)
    return RuleResult(media_type="show", tvdb_id=item.tvdb_id, **common)


def process_rules(item: MovieMetadata | ShowMetadata) -> RuleResult:
    now = _now()

    # Rated > 6: keep or promote from cache
    if item.user_rating is not None and item.user_rating > 6:
        if item.location and "media_cache" in item.location:
            return _result(item, Action.PROMOTE)
        return _result(item, Action.DO_NOTHING)

    # Rated ≤ 6: delete
    if item.user_rating is not None and item.user_rating <= 6:
        return _result(item, Action.DELETE)

    # Unrated + unwatched + added > 60 days
    if (
        item.view_count == 0
        and item.date_added is not None
        and (now - item.date_added).days > 60
    ):
        return _result(item, Action.ADD_TO_COLLECTION)

    # Unrated + watched + last viewed > 14 days + added > 30 days
    if (
        item.view_count > 0
        and item.last_viewed is not None
        and (now - item.last_viewed).days > 14
        and item.date_added is not None
        and (now - item.date_added).days > 30
    ):
        return _result(item, Action.ADD_TO_COLLECTION)

    return _result(item, Action.DO_NOTHING)


def _resolve(
    item: MovieMetadata | ShowMetadata,
    record: dict | None,
    now: datetime,
) -> Action:
    """Apply state machine logic and return the resolved new Action."""
    current = Action(record["state"]) if record else None

    # Terminal state — never exits
    if current == Action.DELETE:
        return Action.DELETE

    # Time-based transition: ADD_TO_COLLECTION → DELETE after 30 days
    if (
        current == Action.ADD_TO_COLLECTION
        and record is not None
        and (now - datetime.fromisoformat(record["state_changed_at"])).days >= _collection_days()
    ):
        return Action.DELETE

    new = process_rules(item).action
    allowed = VALID_TRANSITIONS.get(current, _ALL)
    return new if new in allowed else (current or Action.DO_NOTHING)


def run_cleanup(
    baseurl: Optional[str] = None,
    token: Optional[str] = None,
    db: Optional[PlexMediaDB] = None,
) -> tuple[list[RuleResult], list[RuleResult], list[RuleResult], int]:
    """
    Iterate all media, apply process_rules with state machine, and return:
      (add_to_collection, delete, promote, do_nothing_count)

    Only writes to DB when state actually changes.
    Movies are processed in batches of MOVIE_BATCH; shows one at a time.
    """
    # Import here to avoid circular import (queue imports nothing from rules)
    from src.managarr import queue as plex_queue

    now = _now()
    add_to_collection: list[RuleResult] = []
    delete: list[RuleResult] = []
    promote: list[RuleResult] = []
    do_nothing = 0

    def _apply(item: MovieMetadata | ShowMetadata, record: dict | None):
        nonlocal do_nothing
        current = Action(record["state"]) if record else None
        new = _resolve(item, record, now)
        result = _result(item, new)

        _should_enqueue = (
            new in {Action.ADD_TO_COLLECTION, Action.DELETE, Action.PROMOTE}
            or (new == Action.DO_NOTHING and current in {Action.ADD_TO_COLLECTION, Action.PROMOTE})
        )
        if new != current and db is not None:
            db.upsert_state(
                plex_key=item.plex_key,
                state=new.value,
                media_type=result.media_type,
                title=item.title,
                location=item.location,
                tmdb_id=result.tmdb_id,
                tvdb_id=result.tvdb_id,
            )
            if _should_enqueue:
                plex_queue.enqueue_job(item.plex_key, {
                    "action": new.value,
                    "media_type": result.media_type,
                    "title": item.title,
                    "location": item.location,
                    "tmdb_id": result.tmdb_id,
                    "tvdb_id": result.tvdb_id,
                })

        if new == Action.ADD_TO_COLLECTION:
            add_to_collection.append(result)
        elif new == Action.DELETE:
            delete.append(result)
        elif new == Action.PROMOTE:
            promote.append(result)
        else:
            do_nothing += 1

    # Movies: process in batches of MOVIE_BATCH
    batch: list[MovieMetadata] = []
    for item in get_movies(baseurl, token):
        batch.append(item)
        if len(batch) >= _movie_batch():
            states = db.get_states([m.plex_key for m in batch]) if db else {}
            for m in batch:
                _apply(m, states.get(m.plex_key))
            batch = []
    if batch:
        states = db.get_states([m.plex_key for m in batch]) if db else {}
        for m in batch:
            _apply(m, states.get(m.plex_key))

    # Shows: one at a time
    for item in get_shows(baseurl, token):
        record = db.get_states([item.plex_key]).get(item.plex_key) if db else None
        _apply(item, record)

    return add_to_collection, delete, promote, do_nothing
