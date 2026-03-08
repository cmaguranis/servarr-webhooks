#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Enqueue a transcode job from a real Radarr movie.

Fetches movie + file metadata from Radarr and fires the /transcode-webhook endpoint,
just as Radarr itself would on an import event.

Usage:
  uv run scripts/enqueue_from_radarr.py --radarr-url http://radarr:7878 --radarr-key KEY --movie-id 42
  uv run scripts/enqueue_from_radarr.py --radarr-url http://radarr:7878 --radarr-key KEY --movie-id 42 --dry-run
  uv run scripts/enqueue_from_radarr.py --radarr-url http://radarr:7878 --radarr-key KEY --search "The Dark Knight"
  uv run scripts/enqueue_from_radarr.py --radarr-url http://radarr:7878 --radarr-key KEY --movie-id 42 --retry
  uv run scripts/enqueue_from_radarr.py --radarr-url http://radarr:7878 --radarr-key KEY --movie-id 42 --retry --dry-run
"""

import argparse
import json
import sys

import requests


def get_radarr(path, params=None, *, base, key):
    res = requests.get(
        f"{base}/api/v3/{path}",
        params=params,
        headers={"X-Api-Key": key},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


def find_movie(*, base, key, movie_id=None, search=None):
    if movie_id:
        return get_radarr(f"movie/{movie_id}", base=base, key=key)

    all_movies = get_radarr("movie", base=base, key=key)
    query = search.lower()
    matches = [m for m in all_movies if query in m.get("title", "").lower()]
    if not matches:
        print(f"No movies found matching '{search}'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple matches — pick a --movie-id:")
        for m in matches:
            print(f"  {m['id']:5d}  {m['title']} ({m.get('year', '?')})")
        sys.exit(1)
    return matches[0]


def build_payload(movie, movie_file):
    return {
        "eventType": "Download",
        "isUpgrade": False,
        "movie": {
            "id": movie["id"],
            "title": movie.get("title", ""),
            "originalLanguage": movie.get("originalLanguage", {"name": "English"}),
            "tags": movie.get("tags", []),
        },
        "movieFile": {
            "path": movie_file["path"],
            "releaseGroup": movie_file.get("releaseGroup", ""),
            "mediaInfo": movie_file.get("mediaInfo", {}),
        },
    }


def find_job_for_path(webhook_base, path):
    res = requests.get(f"{webhook_base}/transcode/jobs", timeout=15)
    res.raise_for_status()
    jobs = res.json().get("jobs", [])
    return next((j for j in jobs if j["path"] == path), None)


def main():
    parser = argparse.ArgumentParser(description="Enqueue a Radarr movie for transcoding")
    parser.add_argument("--movie-id", type=int, help="Radarr movie ID")
    parser.add_argument("--search", help="Search by title (must match exactly one movie)")
    parser.add_argument("--dry-run", action="store_true", help="Pass ?dry_run=true to the webhook")
    parser.add_argument("--retry", action="store_true", help="Retry the existing job for this movie instead of enqueuing a new one")
    parser.add_argument("--media-test", action="store_true", help="Slice and transcode to /data/media_test instead of in-place")
    parser.add_argument("--start-sec", type=int, help="Exact slice start point in seconds (random if omitted)")
    parser.add_argument("--slice-duration", type=int, default=30, help="Slice duration in seconds (default: 30)")
    parser.add_argument("--radarr-url", required=True, help="Radarr base URL, e.g. http://radarr:7878")
    parser.add_argument("--radarr-key", required=True, help="Radarr API key")
    parser.add_argument("--webhook-url", default="http://localhost:5001",
                        help="servarr-webhooks base URL (default: http://localhost:5001)")
    args = parser.parse_args()

    if not args.movie_id and not args.search:
        parser.error("one of --movie-id or --search is required")

    radarr = dict(base=args.radarr_url.rstrip("/"), key=args.radarr_key)
    webhook_base = args.webhook_url.rstrip("/")

    print(f"Fetching movie from Radarr...")
    movie = find_movie(movie_id=args.movie_id, search=args.search, **radarr)
    print(f"  {movie['id']}: {movie.get('title')} ({movie.get('year', '?')})")

    movie_files = get_radarr("moviefile", params={"movieId": movie["id"]}, **radarr)
    if not movie_files:
        print("No movie file found in Radarr for this movie (not yet imported?)")
        sys.exit(1)
    movie_file = movie_files[0]
    print(f"  File: {movie_file['path']}")
    print(f"  Codec: {movie_file.get('mediaInfo', {}).get('videoCodec')}  "
          f"Bitrate: {movie_file.get('mediaInfo', {}).get('videoBitrate')} kbps  "
          f"Audio channels: {movie_file.get('mediaInfo', {}).get('audioChannels')}")

    try:
        if args.retry:
            job = find_job_for_path(webhook_base, movie_file["path"])
            if not job:
                print(f"No existing job found for: {movie_file['path']}")
                sys.exit(1)
            url = f"{webhook_base}/transcode/jobs/{job['id']}/retry"
            if args.dry_run:
                url += "?dry_run=true"
            print(f"\nRetrying job {job['id']} (status={job['status']}, dry_run={args.dry_run})")
            print(f"POST {url}")
            res = requests.post(url, timeout=30)
        else:
            payload = build_payload(movie, movie_file)
            url = f"{webhook_base}/transcode-webhook"
            qs = []
            if args.dry_run:
                qs.append("dry_run=true")
            if args.media_test:
                qs.append("media_test=true")
                if args.start_sec is not None:
                    qs.append(f"start_sec={args.start_sec}")
                if args.slice_duration != 30:
                    qs.append(f"slice_duration={args.slice_duration}")
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
