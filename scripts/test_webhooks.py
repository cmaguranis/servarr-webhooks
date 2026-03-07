#!/usr/bin/env python3
"""
Manual test script for servarr-webhooks endpoints.

Usage:
  python scripts/test_webhooks.py [--url URL] [--dry-run] {radarr,sonarr,seerr,promote}

Examples:
  python scripts/test_webhooks.py radarr
  python scripts/test_webhooks.py --dry-run radarr
  python scripts/test_webhooks.py --url http://192.168.1.10:5001 sonarr
"""

import argparse
import json
import sys
try:
    import requests
except ImportError:
    print("requests not installed — run: pip install requests")
    sys.exit(1)

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
    parser.add_argument("target", choices=["radarr", "sonarr", "seerr", "promote"])
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
    elif args.target == "promote":
        url = f"{base}/promote-cache"
        payload = None
        method = "POST"

    print(f"{method} {url}")
    if payload:
        print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        if payload:
            res = requests.post(url, json=payload, timeout=30)
        else:
            res = requests.post(url, timeout=30)
        print(f"\nStatus: {res.status_code}")
        if res.text:
            print(f"Body:   {res.text}")
    except requests.ConnectionError:
        print(f"\nError: could not connect to {base}")
        sys.exit(1)


if __name__ == "__main__":
    main()
