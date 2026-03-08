#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Generate test media clips and enqueue them for transcoding.

Steps:
  1. POST /media-test/generate  — slice one clip per codec signature from media_cache
  2. POST /transcode/enqueue-folder — enqueue transcode jobs for the generated clips

Usage:
  uv run scripts/test_media_pipeline.py
  uv run scripts/test_media_pipeline.py --include-media
  uv run scripts/test_media_pipeline.py --dry-run
  uv run scripts/test_media_pipeline.py --generate-only
  uv run scripts/test_media_pipeline.py --transcode-only
  uv run scripts/test_media_pipeline.py --webhook-url http://myserver:5001
"""

import argparse
import json
import sys

import requests


def post(url, *, body=None, label):
    print(f"\nPOST {url}")
    if body:
        print(f"Body: {json.dumps(body, indent=2)}")
    try:
        res = requests.post(url, json=body, timeout=60)
    except requests.ConnectionError:
        print(f"Error: could not connect to {url}")
        sys.exit(1)
    print(f"Status: {res.status_code}")
    try:
        data = res.json()
        print(f"Response:\n{json.dumps(data, indent=2)}")
    except Exception:
        print(f"Response: {res.text}")
        data = {}
    if not res.ok:
        print(f"\nError: {label} failed (HTTP {res.status_code})")
        sys.exit(1)
    return data


def main():
    parser = argparse.ArgumentParser(description="Generate test media clips and transcode them")
    parser.add_argument("--webhook-url", default="http://localhost:5001",
                        help="servarr-webhooks base URL (default: http://localhost:5001)")
    parser.add_argument("--media-test-dir", default="/data/media_test",
                        help="Output directory for test clips (default: /data/media_test)")
    parser.add_argument("--include-media", action="store_true",
                        help="Also scan /media when generating clips")
    parser.add_argument("--dry-run", action="store_true",
                        help="Pass ?dry_run=true to both steps")
    parser.add_argument("--generate-only", action="store_true",
                        help="Only run step 1 (generate clips), skip transcode enqueue")
    parser.add_argument("--transcode-only", action="store_true",
                        help="Only run step 2 (enqueue-folder), skip clip generation")
    args = parser.parse_args()

    base = args.webhook_url.rstrip("/")
    dry_run_qs = "?dry_run=true" if args.dry_run else ""

    # ------------------------------------------------------------------
    # Step 1: Generate test clips
    # ------------------------------------------------------------------
    if not args.transcode_only:
        print("=" * 60)
        print("Step 1: Generate test media clips")
        print("=" * 60)

        generate_qs = dry_run_qs
        if args.include_media:
            sep = "&" if generate_qs else "?"
            generate_qs += f"{sep}include_media=true"

        generate_data = post(
            f"{base}/media-test/generate{generate_qs}",
            label="media-test/generate",
        )

        enqueued = generate_data.get("enqueued", [])
        skipped  = generate_data.get("skipped", [])
        print(f"\nSummary: {len(enqueued)} enqueued, {len(skipped)} skipped")

        if not enqueued and not args.dry_run:
            print("\nNothing enqueued — no clips to transcode. Exiting.")
            sys.exit(0)

    # ------------------------------------------------------------------
    # Step 2: Enqueue transcode jobs for the clips folder
    # ------------------------------------------------------------------
    if not args.generate_only:
        print("\n" + "=" * 60)
        print("Step 2: Enqueue transcode jobs for test clips")
        print("=" * 60)

        transcode_data = post(
            f"{base}/transcode/enqueue-folder{dry_run_qs}",
            body={"path": args.media_test_dir},
            label="transcode/enqueue-folder",
        )

        enqueued = transcode_data.get("enqueued", [])
        skipped  = transcode_data.get("skipped", [])
        errors   = transcode_data.get("errors", [])
        print(f"\nSummary: {len(enqueued)} enqueued, {len(skipped)} skipped, {len(errors)} errors")

        if errors:
            print("\nErrors:")
            for e in errors:
                print(f"  {e['path']}: {e['error']}")

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to execute.")
    else:
        print("Done. Monitor progress:")
        print(f"  curl \"{base}/transcode/jobs?status=processing\"")
        print(f"  curl \"{base}/transcode/jobs?status=done\"")
        print(f"  curl \"{base}/transcode/jobs?status=failed\"")
    print("=" * 60)


if __name__ == "__main__":
    main()
