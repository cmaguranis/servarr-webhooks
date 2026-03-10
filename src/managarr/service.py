import logging
from typing import Iterator, Optional
from datetime import datetime

from src import config

from plexapi.server import PlexServer
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class MediaStats(BaseModel):
    last_viewed: Optional[datetime] = None
    date_added: Optional[datetime] = None
    user_rating: Optional[float] = None
    view_count: int = 0


class SeasonMetadata(MediaStats):
    season_num: int
    total_episodes: int


class ShowMetadata(MediaStats):
    title: str
    plex_key: int
    tvdb_id: Optional[int] = None
    location: Optional[str] = None
    seasons: list[SeasonMetadata]
    total_episodes: int
    plex_last_viewed: Optional[datetime] = None  # raw Plex show-level value
    plex_view_count: Optional[int] = None        # raw Plex show-level value


class MovieMetadata(MediaStats):
    title: str
    plex_key: int
    tmdb_id: Optional[int] = None
    location: Optional[str] = None


def _server(baseurl: Optional[str] = None, token: Optional[str] = None) -> PlexServer:
    return PlexServer(
        baseurl or config.PLEX_BASEURL(),
        token or config.PLEX_TOKEN(),
    )


def _extract_external_ids(item) -> tuple[Optional[int], Optional[int]]:
    """Parse item.guids into (tmdb_id, tvdb_id)."""
    tmdb_id = None
    tvdb_id = None
    try:
        for guid in item.guids:
            gid = guid.id
            if gid.startswith("tmdb://"):
                try:
                    tmdb_id = int(gid.removeprefix("tmdb://"))
                except ValueError:
                    pass
            elif gid.startswith("tvdb://"):
                try:
                    tvdb_id = int(gid.removeprefix("tvdb://"))
                except ValueError:
                    pass
    except Exception as e:
        logger.warning(f"Plex: could not extract external IDs for '{item.title}': {e}")
    return tmdb_id, tvdb_id


def _fetch_batched(section, libtype, batch_size=50):
    offset = 0
    while True:
        batch = section.search(libtype=libtype, container_start=offset, container_size=batch_size)
        if not batch:
            break
        yield from batch
        offset += batch_size


def _collect_movies_from_section(section) -> Iterator[MovieMetadata]:
    for movie in _fetch_batched(section, 'movie'):
        tmdb_id, _ = _extract_external_ids(movie)
        yield MovieMetadata(
            title=movie.title,
            plex_key=int(movie.ratingKey),
            last_viewed=movie.lastViewedAt,
            date_added=movie.addedAt,
            user_rating=movie.userRating,
            view_count=getattr(movie, 'viewCount', 0),
            tmdb_id=tmdb_id,
            location=movie.locations[0] if movie.locations else None,
        )


def _collect_shows_from_section(section) -> Iterator[ShowMetadata]:
    for show in _fetch_batched(section, 'show'):
        _, tvdb_id = _extract_external_ids(show)

        # One API call for all episodes; group by season number locally
        by_season: dict[int, list] = {}
        for ep in show.episodes():
            season_num = ep.parentIndex or 0
            by_season.setdefault(season_num, []).append(ep)

        seasons = []
        for season_num in sorted(by_season):
            eps = by_season[season_num]
            last_viewed = None
            viewed_count = 0
            date_added = None
            for ep in eps:
                if ep.lastViewedAt:
                    if last_viewed is None or ep.lastViewedAt > last_viewed:
                        last_viewed = ep.lastViewedAt
                if getattr(ep, 'viewCount', 0):
                    viewed_count += 1
                if ep.addedAt:
                    if date_added is None or ep.addedAt < date_added:
                        date_added = ep.addedAt
            seasons.append(SeasonMetadata(
                season_num=season_num,
                last_viewed=last_viewed,
                date_added=date_added,
                view_count=viewed_count,
                total_episodes=len(eps),
            ))

        show_last_viewed = None
        for s in seasons:
            if s.last_viewed:
                if show_last_viewed is None or s.last_viewed > show_last_viewed:
                    show_last_viewed = s.last_viewed

        yield ShowMetadata(
            title=show.title,
            plex_key=int(show.ratingKey),
            last_viewed=show_last_viewed,
            date_added=show.addedAt,
            user_rating=show.userRating,
            view_count=sum(s.view_count for s in seasons),
            total_episodes=sum(s.total_episodes for s in seasons),
            tvdb_id=tvdb_id,
            location=show.locations[0] if show.locations else None,
            seasons=seasons,
            plex_last_viewed=show.lastViewedAt,
            plex_view_count=getattr(show, 'viewedLeafCount', None),
        )


def get_movies(baseurl: Optional[str] = None, token: Optional[str] = None) -> Iterator[MovieMetadata]:
    """Yield movie watch metadata from Plex one item at a time."""
    try:
        plex = _server(baseurl, token)
        for section in plex.library.sections():
            if section.type == 'movie':
                yield from _collect_movies_from_section(section)
    except Exception as e:
        logger.error(f"Plex: failed to collect movies: {e}")
        raise


def get_shows(baseurl: Optional[str] = None, token: Optional[str] = None) -> Iterator[ShowMetadata]:
    """Yield show watch metadata from Plex one show at a time."""
    try:
        plex = _server(baseurl, token)
        for section in plex.library.sections():
            if section.type == 'show':
                yield from _collect_shows_from_section(section)
    except Exception as e:
        logger.error(f"Plex: failed to collect shows: {e}")
        raise


def get_all(baseurl: Optional[str] = None, token: Optional[str] = None) -> Iterator[MovieMetadata | ShowMetadata]:
    """Yield all movie and show watch metadata from Plex."""
    yield from get_movies(baseurl, token)
    yield from get_shows(baseurl, token)
