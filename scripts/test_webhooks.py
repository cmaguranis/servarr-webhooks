#!/usr/bin/env python3
"""
Manual test script for servarr-webhooks endpoints.

Usage:
  python scripts/test_webhooks.py [--url URL] [--dry-run] [--job-id ID] TARGET

Targets:
  radarr          POST /transcode-webhook with a Radarr Download payload
  sonarr          POST /transcode-webhook with a Sonarr Download payload
  seerr           POST /seerr_webhook
  skip-group      POST /transcode-webhook with a YIFY release (should be skipped, 200)
  non-download    POST /transcode-webhook with eventType=Test (should be ignored, 200)
  retry           POST /transcode/jobs/<id>/retry  (requires --job-id)
  retry-missing   POST /transcode/jobs/999999/retry  (should 404)
  jobs            GET  /transcode/jobs

Examples:
  python scripts/test_webhooks.py radarr
  python scripts/test_webhooks.py --dry-run radarr
  python scripts/test_webhooks.py --url http://192.168.1.10:5001 sonarr
  python scripts/test_webhooks.py skip-group
  python scripts/test_webhooks.py non-download
  python scripts/test_webhooks.py --job-id 3 retry
  python scripts/test_webhooks.py jobs
"""

import argparse
import json
import sys
try:
    import requests
except ImportError:
    print("requests not installed — run: pip install requests")
    sys.exit(1)

SKIP_GROUP_PAYLOAD = {
    "eventType": "Download",
    "isUpgrade": False,
    "movie": {
        "id": 2,
        "title": "YIFY Test Movie",
        "originalLanguage": {"name": "English"},
        "tags": [],
    },
    "movieFile": {
        "path": "/media/movies/YIFY Test Movie (2020)/YIFY Test Movie (2020).mkv",
        "releaseGroup": "YIFY",
        "mediaInfo": {
            "videoCodec": "x265",
            "videoBitrate": 1500,
            "audioChannels": 2,
            "audioLanguages": "English",
        },
    },
}

NON_DOWNLOAD_PAYLOAD = {"eventType": "Test"}

RADARR_PAYLOAD = {
    "eventType": "Download",
    "isUpgrade": False,
    "movie": {
        "id": 1,
        "title": "Test Movie",
        "originalLanguage": {"name": "English"},
        "tags": [],
    },
    "movieFile": {
        "path": "/media/movies/Test Movie (2020)/Test Movie (2020).mkv",
        "releaseGroup": "TestGroup",
        "mediaInfo": {
            "videoCodec": "AVC",
            "videoBitrate": 12000,
            "audioChannels": 6,
            "audioLanguages": "English",
        },
    },
}

SONARR_PAYLOAD = {
    "eventType": "Download",
    "isUpgrade": False,
    "series": {
        "id": 1,
        "title": "Test Show",
        "originalLanguage": {"name": "Japanese"},
        "tags": [],
    },
    "episodeFile": {
        "path": "/media/tv/Test Show/Season 01/Test Show - S01E01.mkv",
        "releaseGroup": "TestGroup",
        "mediaInfo": {
            "videoCodec": "x265",
            "videoBitrate": 3000,
            "audioChannels": 2,
            "audioLanguages": "Japanese",
        },
    },
}

SEERR_PAYLOAD = {
    "requestID": 1,
    "mediaId": 12345,
    "mediaType": "movie",
}


def main():
    parser = argparse.ArgumentParser(description="Fire sample webhook payloads at servarr-webhooks")
    parser.add_argument("--url", default="http://localhost:5001", help="Base URL (default: http://localhost:5001)")
    parser.add_argument("--dry-run", action="store_true", help="Append ?dry_run=true to transcode webhook")
    parser.add_argument("--job-id", type=int, help="Job ID for retry target")
    parser.add_argument("target", choices=["radarr", "sonarr", "seerr", "skip-group", "non-download", "retry", "retry-missing", "jobs"])
    args = parser.parse_args()

    base = args.url.rstrip("/")

    if args.target == "radarr":
        url = f"{base}/transcode-webhook"
        if args.dry_run:
            url += "?dry_run=true"
        payload = RADARR_PAYLOAD
        method = "POST"
    elif args.target == "sonarr":
        url = f"{base}/transcode-webhook"
        if args.dry_run:
            url += "?dry_run=true"
        payload = SONARR_PAYLOAD
        method = "POST"
    elif args.target == "seerr":
        url = f"{base}/seerr_webhook"
        payload = SEERR_PAYLOAD
        method = "POST"
    elif args.target == "skip-group":
        url = f"{base}/transcode-webhook"
        payload = SKIP_GROUP_PAYLOAD
        method = "POST"
    elif args.target == "non-download":
        url = f"{base}/transcode-webhook"
        payload = NON_DOWNLOAD_PAYLOAD
        method = "POST"
    elif args.target == "retry":
        job_id = args.job_id
        if not job_id:
            print("Error: --job-id required for retry")
            sys.exit(1)
        url = f"{base}/transcode/jobs/{job_id}/retry"
        if args.dry_run:
            url += "?dry_run=true"
        payload = None
        method = "POST"
    elif args.target == "retry-missing":
        url = f"{base}/transcode/jobs/999999/retry"
        payload = None
        method = "POST"
    elif args.target == "jobs":
        status = args.dry_run  # reuse flag slot — not applicable here, just GET all
        url = f"{base}/transcode/jobs"
        payload = None
        method = "GET"

    print(f"{method} {url}")
    if payload:
        print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        if method == "GET":
            res = requests.get(url, timeout=30)
        elif payload:
            res = requests.post(url, json=payload, timeout=30)
        else:
            res = requests.post(url, timeout=30)
        print(f"\nStatus: {res.status_code}")
        if res.text:
            try:
                print(f"Body:   {json.dumps(res.json(), indent=2)}")
            except Exception:
                print(f"Body:   {res.text}")
    except requests.ConnectionError:
        print(f"\nError: could not connect to {base}")
        sys.exit(1)


if __name__ == "__main__":
    main()
