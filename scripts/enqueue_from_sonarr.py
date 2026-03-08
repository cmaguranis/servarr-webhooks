#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Enqueue a transcode job from a real Sonarr episode file.

Fetches series + episode file metadata from Sonarr and fires the /transcode-webhook
endpoint, just as Sonarr itself would on an import event.

Usage:
  uv run scripts/enqueue_from_sonarr.py --sonarr-url http://sonarr:8989 --sonarr-key KEY --series-id 7 --episode-file-id 42
  uv run scripts/enqueue_from_sonarr.py --sonarr-url http://sonarr:8989 --sonarr-key KEY --search "Breaking Bad" --episode-file-id 42
  uv run scripts/enqueue_from_sonarr.py --sonarr-url http://sonarr:8989 --sonarr-key KEY --series-id 7  # lists episode files
  uv run scripts/enqueue_from_sonarr.py --sonarr-url http://sonarr:8989 --sonarr-key KEY --series-id 7 --episode-file-id 42 --retry
  uv run scripts/enqueue_from_sonarr.py --sonarr-url http://sonarr:8989 --sonarr-key KEY --series-id 7 --episode-file-id 42 --retry --dry-run
"""

import argparse
import json
import sys

import requests


def get_sonarr(path, params=None, *, base, key):
    res = requests.get(
        f"{base}/api/v3/{path}",
        params=params,
        headers={"X-Api-Key": key},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


def find_series(*, base, key, series_id=None, search=None):
    if series_id:
        return get_sonarr(f"series/{series_id}", base=base, key=key)

    all_series = get_sonarr("series", base=base, key=key)
    query = search.lower()
    matches = [s for s in all_series if query in s.get("title", "").lower()]
    if not matches:
        print(f"No series found matching '{search}'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple matches — pick a --series-id:")
        for s in matches:
            print(f"  {s['id']:5d}  {s['title']} ({s.get('year', '?')})")
        sys.exit(1)
    return matches[0]


def list_episode_files(episode_files):
    print(f"  {'ID':>6}  {'Season':>6}  {'Episode(s)':<20}  Codec       Bitrate    Path")
    print(f"  {'':->6}  {'':->6}  {'':->20}  {'':->10}  {'':->9}  ----")
    for ef in sorted(episode_files, key=lambda f: f.get("id", 0)):
        episodes = ef.get("episodes", [])
        ep_label = ", ".join(
            f"S{e.get('seasonNumber', 0):02d}E{e.get('episodeNumber', 0):02d}"
            for e in episodes
        ) or "?"
        mi = ef.get("mediaInfo", {})
        codec = mi.get("videoCodec", "?")
        bitrate = mi.get("videoBitrate", "?")
        path = ef.get("relativePath", ef.get("path", "?"))
        print(f"  {ef['id']:>6}  {ef.get('seasonNumber', '?'):>6}  {ep_label:<20}  {codec:<10}  {bitrate:>6} kbps  {path}")


def build_payload(series, episode_file):
    return {
        "eventType": "Download",
        "isUpgrade": False,
        "series": {
            "id": series["id"],
            "title": series.get("title", ""),
            "originalLanguage": series.get("originalLanguage", {"name": "English"}),
            "tags": series.get("tags", []),
        },
        "episodeFile": {
            "path": episode_file["path"],
            "releaseGroup": episode_file.get("releaseGroup", ""),
            "mediaInfo": episode_file.get("mediaInfo", {}),
        },
    }


def find_job_for_path(webhook_base, path):
    res = requests.get(f"{webhook_base}/transcode/jobs", timeout=15)
    res.raise_for_status()
    jobs = res.json().get("jobs", [])
    return next((j for j in jobs if j["path"] == path), None)


def main():
    parser = argparse.ArgumentParser(description="Enqueue a Sonarr episode for transcoding")
    parser.add_argument("--series-id", type=int, help="Sonarr series ID")
    parser.add_argument("--search", help="Search by title (must match exactly one series)")
    parser.add_argument("--episode-file-id", type=int,
                        help="Episode file ID to enqueue (omit to list available files)")
    parser.add_argument("--dry-run", action="store_true", help="Pass ?dry_run=true to the webhook")
    parser.add_argument("--retry", action="store_true", help="Retry the existing job for this episode instead of enqueuing a new one")
    parser.add_argument("--media-test", action="store_true", help="Slice and transcode to /data/media_test instead of in-place")
    parser.add_argument("--start-sec", type=int, help="Exact slice start point in seconds (random if omitted)")
    parser.add_argument("--sonarr-url", required=True, help="Sonarr base URL, e.g. http://sonarr:8989")
    parser.add_argument("--sonarr-key", required=True, help="Sonarr API key")
    parser.add_argument("--webhook-url", default="http://localhost:5001",
                        help="servarr-webhooks base URL (default: http://localhost:5001)")
    args = parser.parse_args()

    if not args.series_id and not args.search:
        parser.error("one of --series-id or --search is required")

    sonarr = dict(base=args.sonarr_url.rstrip("/"), key=args.sonarr_key)

    print("Fetching series from Sonarr...")
    series = find_series(series_id=args.series_id, search=args.search, **sonarr)
    print(f"  {series['id']}: {series.get('title')} ({series.get('year', '?')})")

    episode_files = get_sonarr("episodefile", params={"seriesId": series["id"]}, **sonarr)
    if not episode_files:
        print("No episode files found in Sonarr for this series (not yet imported?)")
        sys.exit(1)

    # Enrich with episode metadata for display
    episodes_by_file = {}
    try:
        all_episodes = get_sonarr("episode", params={"seriesId": series["id"]}, **sonarr)
        for ep in all_episodes:
            fid = ep.get("episodeFileId")
            if fid:
                episodes_by_file.setdefault(fid, []).append(ep)
    except Exception:
        pass
    for ef in episode_files:
        ef["episodes"] = episodes_by_file.get(ef["id"], [])

    if not args.episode_file_id:
        print(f"\n  {len(episode_files)} episode file(s) — pick one with --episode-file-id:")
        list_episode_files(episode_files)
        sys.exit(0)

    episode_file = next((ef for ef in episode_files if ef["id"] == args.episode_file_id), None)
    if not episode_file:
        print(f"Episode file ID {args.episode_file_id} not found for this series")
        sys.exit(1)

    print(f"  File: {episode_file['path']}")
    mi = episode_file.get("mediaInfo", {})
    print(f"  Codec: {mi.get('videoCodec')}  "
          f"Bitrate: {mi.get('videoBitrate')} kbps  "
          f"Audio channels: {mi.get('audioChannels')}")

    webhook_base = args.webhook_url.rstrip("/")

    try:
        if args.retry:
            job = find_job_for_path(webhook_base, episode_file["path"])
            if not job:
                print(f"No existing job found for: {episode_file['path']}")
                sys.exit(1)
            url = f"{webhook_base}/transcode/jobs/{job['id']}/retry"
            if args.dry_run:
                url += "?dry_run=true"
            print(f"\nRetrying job {job['id']} (status={job['status']}, dry_run={args.dry_run})")
            print(f"POST {url}")
            res = requests.post(url, timeout=30)
        else:
            payload = build_payload(series, episode_file)
            url = f"{webhook_base}/transcode-webhook"
            qs = []
            if args.dry_run:
                qs.append("dry_run=true")
            if args.media_test:
                qs.append("media_test=true")
                if args.start_sec is not None:
                    qs.append(f"start_sec={args.start_sec}")
            if qs:
                url += "?" + "&".join(qs)
            print(f"\nPOST {url}")
            print(f"Payload:\n{json.dumps(payload, indent=2)}\n")
            res = requests.post(url, json=payload, timeout=30)

        print(f"Status: {res.status_code}")
        if res.text:
            try:
                print(f"Body:   {json.dumps(res.json(), indent=2)}")
            except Exception:
                print(f"Body:   {res.text}")
    except requests.ConnectionError:
        print(f"Error: could not connect to {args.webhook_url}")
        sys.exit(1)


if __name__ == "__main__":
    main()
