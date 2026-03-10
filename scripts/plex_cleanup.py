# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "plexapi",
#   "pydantic",
# ]
# ///

import argparse
import sys
import os
from datetime import datetime

# Allow importing from src/ when run as a standalone script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.managarr.db import PlexMediaDB
from src.managarr.rules import Action, RuleResult, run_cleanup
from src.managarr import queue as plex_queue


RESET   = '\033[0m'
BOLD    = '\033[1m'
DIM     = '\033[2m'
CYAN    = '\033[36m'
MAGENTA = '\033[35m'
YELLOW  = '\033[33m'
GREEN   = '\033[32m'
RED     = '\033[31m'


def _label(result: RuleResult) -> str:
    if result.media_type == 'movie':
        return f'{MAGENTA}[Movie]{RESET}'
    return f'{CYAN}[Show]{RESET}'


def _meta(r: RuleResult) -> str:
    now = datetime.utcnow()
    parts = []
    if r.user_rating is not None:
        parts.append(f'rating={r.user_rating:.1f}')
    parts.append(f'views={r.view_count}')
    if r.date_added:
        parts.append(f'added={( now - r.date_added).days}d ago')
    if r.last_viewed:
        parts.append(f'seen={( now - r.last_viewed).days}d ago')
    if r.location:
        parts.append(r.location)
    return f'{DIM}  {" · ".join(parts)}{RESET}'


def _item(r: RuleResult) -> str:
    return f'  {_label(r)} {r.title}\n{_meta(r)}'


def print_buckets(
    add_to_collection: list[RuleResult],
    delete: list[RuleResult],
    promote: list[RuleResult],
    do_nothing: int,
):
    print(f'\n{BOLD}{GREEN}=== ADD TO COLLECTION ({len(add_to_collection)}) ==={RESET}')
    for r in add_to_collection:
        print(_item(r))

    print(f'\n{BOLD}{RED}=== DELETE ({len(delete)}) ==={RESET}')
    for r in delete:
        print(_item(r))

    print(f'\n{BOLD}{YELLOW}=== PROMOTE ({len(promote)}) ==={RESET}')
    for r in promote:
        print(_item(r))

    print(f'\n{DIM}DO_NOTHING: {do_nothing} items{RESET}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Plex cleanup: shows state machine transitions per media item')
    parser.add_argument('--server', required=True, help='Plex server URL (e.g. http://192.168.1.1:32400)')
    parser.add_argument('--token', required=True, help='Plex authentication token')
    parser.add_argument('--db', default=os.getenv('PLEX_CLEANUP_DB', 'plex_cleanup.db'),
                        help='Path to state DB (default: PLEX_CLEANUP_DB env or plex_cleanup.db in cwd)')
    args = parser.parse_args()

    db = PlexMediaDB(args.db)
    db.init_db()
    plex_queue.init_db(args.db)

    add_to_collection, delete, promote, do_nothing = run_cleanup(
        baseurl=args.server,
        token=args.token,
        db=db,
    )
    print_buckets(add_to_collection, delete, promote, do_nothing)
