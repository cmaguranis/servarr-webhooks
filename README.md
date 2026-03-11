# servarr-webhooks

A collection of webhooks used by different *arr services — Overseerr, Radarr, and Sonarr.

- [Setup](#setup)
   * [Docker Compose (recommended)](#docker-compose-recommended)
   * [Environment variables](#environment-variables)
   * [Runtime config (`/config/config.ini`)](#runtime-config-configconfigini)
- [Seerr Webhook](#seerr-webhook)
   * [Overseerr Webhook Config](#overseerr-webhook-config)
- [Transcode Webhook](#transcode-webhook)
   * [Radarr (v6+)](#radarr-v6)
   * [Sonarr (v4+)](#sonarr-v4)
   * [Skipping trusted release groups](#skipping-trusted-release-groups)
   * [Dry-run mode](#dry-run-mode)
   * [Enqueue a folder](#enqueue-a-folder)
- [Manual Import Scan](#manual-import-scan)
- [Test Media Generation](#test-media-generation)
   * [Environment variables](#environment-variables-1)
   * [API](#api)
- [Managarr Cleanup](#managarr-cleanup)
   * [API](#api-1)
- [Running Tests](#running-tests)
- [Manual Testing](#manual-testing)
   * [Synthetic payloads](#synthetic-payloads)
   * [Real media from Radarr/Sonarr](#real-media-from-radarrsonarr)
- [Useful Scripts](#useful-scripts)
   * [Finding a Plex token](#finding-a-plex-token)

## Setup

### Docker Compose (recommended)

Copy `docker-compose.yml`, fill in your volume paths and environment variables, then:

```bash
docker compose up -d
```

On first run, `/config/config.ini` is created automatically from the built-in defaults. Edit it on the host to change runtime settings without restarting.

### Environment variables

Required (no defaults — must be set):

| Variable | Description |
|---|---|
| `SEERR_BASEURL` | Overseerr base URL (e.g. `http://192.168.1.x:5055`) |
| `SEERR_API_KEY` | Overseerr API key |
| `RADARR_BASEURL` | Radarr base URL (e.g. `http://192.168.1.x:7878`) |
| `RADARR_API_KEY` | Radarr API key |
| `SONARR_BASEURL` | Sonarr base URL (e.g. `http://192.168.1.x:8989`) |
| `SONARR_API_KEY` | Sonarr API key |
| `PLEX_BASEURL` | Plex server URL (e.g. `http://192.168.1.x:32400`) |
| `PLEX_TOKEN` | Plex authentication token |

Infrastructure overrides (optional):

| Variable | Description |
|---|---|
| `CONFIG_PATH` | Path to config file (default: `/config/config.ini`) |
| `TRANSCODE_DB` | Transcode job queue DB (default: `/config/data/transcode_queue.db`) |
| `PLEX_CLEANUP_DB` | Managarr job queue DB (default: `/config/data/plex_cleanup.db`) |
| `PLEX_MEDIA_DB` | Plex media state DB (default: `/config/data/plex_media.db`) |
| `TRANSCODE_SCHEDULE_PATH` | Transcode schedule file (default: `/config/data/transcode_schedule.json`) |
| `PLEX_SCHEDULE_PATH` | Managarr schedule file (default: `/config/data/plex_cleanup_schedule.json`) |
| `MEDIA_TEST_DB` | Test media job queue DB (default: `/config/data/media_test_queue.db`) |
| `SONARR_TARGET_QUALITY_PROFILE_ID` | Quality profile ID to apply after first episode download |

### Runtime config (`/config/config.ini`)

Edit on the host without restarting. Each key can also be overridden by an env var named `SECTION_KEY` (e.g. `TRANSCODE_WORKER_COUNT`).

```ini
[worker]
poll_interval = 120          ; seconds between job queue polls

[transcode]
skip_groups = yify, yts, judas   ; comma-separated, case-insensitive
cleanup_done_days = 7
cleanup_failed_days = 21
worker_count = 1
hevc_icq_quality = 23            ; ICQ quality target (1–51, lower = better)
max_concurrent_qsv_sessions = 2  ; iGPU degrades beyond ~2 simultaneous encodes
temp_primary = /dev/shm          ; fast temp dir (uses RAM, needs shm_size in compose)
temp_fallback = /transcode-temp  ; fallback when temp_primary is full

[plex]
collection_days = 30      ; days in Cleanup Queue before item is deleted
movie_batch = 100         ; movies fetched per Plex API call
collection_name = Cleanup Queue
worker_count = 2

[test_media]
worker_count = 1
cache_dir = /data/media_cache
output_dir = /data/media_test
media_dir = /media            ; scanned when include_media=true

[seerr]
root_folder_anime_movies =    ; root folder path for anime movies in Radarr
```

---

## Seerr Webhook

Automatically routes anime movie requests to a separate root folder in Radarr via Overseerr webhooks.

When a movie request comes in, the script checks if it is an anime movie (Animation genre + Japanese original language). If so, it updates both the Overseerr request and Radarr to use the configured anime movies root folder.

### Overseerr Webhook Config

In Overseerr, go to **Settings → Notifications → Webhook** and enable the **Request Approved** event. Point it at:

```
http://your-host:5001/seerr_webhook
```

Use this JSON payload template:

```json
{
  "requestID": "{{request_id}}",
  "mediaId": "{{media_tmdbid}}",
  "mediaType": "{{media_type}}",
  "{{extra}}": [],
  "image": "{{image}}",
  "message": "{{message}}"
}
```

---

## Transcode Webhook

Automatically transcodes newly imported or upgraded files to HEVC with normalized audio. Transcodes are queued in SQLite and processed by a background worker using Intel QSV hardware acceleration.

**What it does:**
- Skips files already encoded as HEVC at ≤ 8 Mbps
- Skips files whose loudness is already within target (LUFS and LRA checks)
- Normalizes loud/quiet audio with `loudnorm` and compresses high dynamic range with `dynaudnorm`
- For 5.1 sources: creates a normalized stereo AAC track + normalized 5.1 AC3 track
- Tags processed files as `transcoded` in Radarr/Sonarr so they are never re-queued
- Issues a disk rescan in Radarr/Sonarr after the transcode completes

### Radarr (v6+)

Go to **Settings → Connect → + → Webhook** and configure:

| Field | Value |
|---|---|
| **Name** | `servarr-webhooks` (or any label) |
| **URL** | `http://your-host:5001/transcode-webhook` |
| **Method** | POST |
| **Triggers** | ✔ On Import · ✔ On Upgrade |

Save, then click **Test** to verify connectivity.

### Sonarr (v4+)

Go to **Settings → Connect → + → Webhook** and configure:

| Field | Value |
|---|---|
| **Name** | `servarr-webhooks` (or any label) |
| **URL** | `http://your-host:5001/transcode-webhook` |
| **Method** | POST |
| **Triggers** | ✔ On Import · ✔ On Upgrade |

Save, then click **Test** to verify connectivity.

> **Note:** Both Radarr and Sonarr send `eventType: "Download"` for both On Import and On Upgrade — no custom payload template is needed.

### Skipping trusted release groups

Files from groups listed in `skip_groups` (e.g. YIFY, YTS, Judas) are skipped at enqueue time — no transcode runs. Edit `/config/config.ini` to add or remove groups; takes effect immediately without restarting.

### Dry-run mode

Append `?dry_run=true` to the webhook URL for testing. Analysis (loudness probe, codec check) still runs and logs what *would* happen, but no ffmpeg encode or Radarr/Sonarr tag/rescan is performed:

```
http://your-host:5001/transcode-webhook?dry_run=true
```

### Enqueue a folder

Scan a local folder for media files and enqueue a transcode job for each. Useful for bulk-transcoding the test clips generated by `/media-test/generate`.

```bash
curl -X POST http://localhost:5001/transcode/enqueue-folder \
  -H "Content-Type: application/json" \
  -d '{"path": "/data/media_test"}'
```

Add `?dry_run=true` to log what would be enqueued without creating any jobs.

Response:
```json
{
  "enqueued": [
    {"job_id": 12, "path": "/data/media_test/Movies__Interstellar_3724s.mkv"}
  ],
  "skipped": [],
  "errors": []
}
```

`skipped` lists paths where a job already exists (idempotent). `errors` lists paths where ffprobe failed.

**Monitor progress:**
```bash
curl "http://localhost:5001/transcode/jobs?status=processing" | jq -r '.jobs[] |"[\(.updated_at)] [ID: \(.id)] [\(.status | ascii_upcase)] \(.meta.arr_type) | \(.path | split("/") | .[-1])"'

curl "http://localhost:5001/transcode/jobs?status=done" | jq -r '.jobs[] |"[\(.updated_at)] [ID: \(.id)] [\(.status | ascii_upcase)] \(.meta.arr_type) | \(.path | split("/") | .[-1])"'

curl "http://localhost:5001/transcode/jobs?status=failed" | jq -r '.jobs[] |"[\(.updated_at)] [ID: \(.id)] [\(.status | ascii_upcase)] \(.meta.arr_type) | \(.path | split("/") | .[-1])"'
```

**Clear jobs by status:**
```bash
curl -X DELETE "http://localhost:5001/transcode/jobs?status=done"
```

---

## Manual Import Scan

Triggers Radarr or Sonarr to scan a folder for new files, import them into the library, and fire all configured On Import webhooks (including the transcode webhook).

```
POST http://your-host:5001/import-scan
```

Body:

| Field | Type | Description |
|---|---|---|
| `path` | string | **Required.** Absolute path to the folder or file to import |
| `arr` | string | Which service to notify: `radarr`, `sonarr`, or `both` (default: `both`) |

```bash
# Import a movie (Radarr only)
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/import/The Dark Knight (2008)", "arr": "radarr"}'

# Import a TV episode (Sonarr only)
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/import/Breaking Bad/Season 01", "arr": "sonarr"}'

# Try both (useful when unsure which app owns the file)
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/import/some-file.mkv"}'
```

Response:
```json
{
  "radarr": {"status": "queued", "commandId": 42},
  "sonarr": {"status": "queued", "commandId": 17}
}
```

Radarr issues `DownloadedMoviesScan`; Sonarr issues `DownloadedEpisodesScan`. Both match, rename, and move the file into the library, then fire their On Import webhooks. Errors from one service do not block the other.

---

## Test Media Generation

Generates short test clips from real media files to exercise ffmpeg encoding paths without processing entire files.

Scans `/data/media_cache` (and optionally `/media`) for media files, selects one file per unique codec/audio signature to maximize encoding-path coverage, then slices a random 30-second segment from each. Slicing uses stream copy (no re-encode), so jobs complete in seconds.

### Environment variables

| Variable | Description |
|---|---|
| `MEDIA_TEST_WORKERS` | Parallel slice jobs (default: `1`) |
| `MEDIA_TEST_OUTPUT_DIR` | Where test clips are written (default: `/data/media_test`) |
| `MEDIA_TEST_CACHE_DIR` | Source directory to scan (default: `/data/media_cache`) |
| `MEDIA_DIR` | Main media directory, used when `include_media=true` (default: `/media`) |

### API

**Scan and enqueue:**
```bash
# Scan media_cache, enqueue one slice job per unique codec signature
curl -X POST http://localhost:5001/media-test/generate

# Also scan /media
curl -X POST "http://localhost:5001/media-test/generate?include_media=true"

# Dry-run: logs what would be sliced without creating any jobs or files
curl -X POST "http://localhost:5001/media-test/generate?dry_run=true"
```

Response:
```json
{
  "dry_run": false,
  "enqueued": [
    {
      "job_id": 7,
      "source": "/data/media_cache/Movies/Interstellar/Interstellar.mkv",
      "output": "/data/media_test/Interstellar__Interstellar_3724s.mkv",
      "start_sec": 3724,
      "signature": ["hevc", "eac3", 8]
    }
  ],
  "skipped": []
}
```

`skipped` lists output paths where a job already exists in the queue (idempotent — safe to call repeatedly).

**List jobs:**
```bash
curl http://localhost:5001/media-test/jobs
curl "http://192.168.1.67:5001/media-test/jobs?status=done"
```

---

## Managarr Cleanup

Automatically categorises Plex media against a set of rules and enqueues cleanup actions (add to watchlist collection, promote from cache to main storage, or delete via Radarr/Sonarr). A background worker processes jobs continuously; the enabled/disabled flag lets you pause it without stopping the service.

**Rules applied per item:**

| Condition | Action |
|---|---|
| User rating > 6 and in `/media_cache` | Promote to `/media` |
| User rating > 6 | Do nothing |
| User rating ≤ 6 | Delete |
| Unrated, unwatched, added > 60 days ago | Add to collection |
| Unrated, watched, last viewed > 14 days ago and added > 30 days ago | Add to collection |
| In collection for ≥ `collection_days` | Delete |

### API

**Run the rules pass** (fetches all Plex media, applies rules, enqueues jobs):
```bash
curl -X POST http://localhost:5001/managarr/cleanup/rules
```

Response:
```json
{
  "add_to_collection": 12,
  "delete": 3,
  "promote": 2,
  "do_nothing": 847
}
```

**Check / toggle the worker schedule:**
```bash
# Check current state
curl http://localhost:5001/managarr/cleanup/schedule

# Pause the worker (stops claiming new jobs)
curl -X POST "http://localhost:5001/managarr/cleanup/schedule?enabled=false"

# Resume the worker
curl -X POST "http://localhost:5001/managarr/cleanup/schedule?enabled=true"
```

**Manage jobs:**
```bash
# List all jobs (optionally filter by status: pending, processing, done, failed)
curl http://localhost:5001/managarr/cleanup/jobs
curl "http://localhost:5001/managarr/cleanup/jobs?status=failed"

# Clear jobs by status
curl -X DELETE "http://localhost:5001/managarr/cleanup/jobs?status=done"

# Requeue a failed job
curl -X POST http://localhost:5001/managarr/cleanup/jobs/42/retry
```

---

## Running Tests

Requires [uv](https://github.com/astral-sh/uv):

```bash
uv run pytest tests/
```

Run with verbose output:
```bash
uv run pytest tests/ -v
```

Run a specific test file or class:
```bash
uv run pytest tests/test_queue.py -v
uv run pytest tests/test_media/test_controller.py::TestSignatureDedup -v
```

---

## Manual Testing

### Synthetic payloads

Use `scripts/test_webhooks.py` to fire sample payloads at the running service:

```bash
# Radarr On Import (will enqueue a transcode job)
python scripts/test_webhooks.py radarr

# Sonarr On Import, dry-run (logs analysis only, no encode)
python scripts/test_webhooks.py --dry-run sonarr

# Seerr request approved
python scripts/test_webhooks.py seerr

# Skip-group check (YIFY — should return 200 without enqueuing)
python scripts/test_webhooks.py skip-group

# Non-download event (eventType=Test — should return 200 without enqueuing)
python scripts/test_webhooks.py non-download

# List all jobs
python scripts/test_webhooks.py jobs

# Retry a specific job
python scripts/test_webhooks.py --job-id 3 retry

# Point at a non-local host
python scripts/test_webhooks.py --url http://192.168.1.10:5001 radarr
```

### Real media from Radarr/Sonarr

Use `scripts/enqueue_from_radarr.py` or `scripts/enqueue_from_sonarr.py` to fetch real file metadata from the Arr apps and fire the transcode webhook exactly as the app would on import. Requires [uv](https://github.com/astral-sh/uv) — dependencies are installed automatically.

**Radarr:**

```bash
# Search by title (prints the movie ID, then fires the webhook)
uv run scripts/enqueue_from_radarr.py \
  --radarr-url http://radarr:7878 --radarr-key KEY \
  --search "The Dark Knight"

# By movie ID, dry-run (analysis only, no encode)
uv run scripts/enqueue_from_radarr.py \
  --radarr-url http://radarr:7878 --radarr-key KEY \
  --movie-id 42 --dry-run
```

**Sonarr:**

```bash
# Step 1: list episode files for a series
uv run scripts/enqueue_from_sonarr.py \
  --sonarr-url http://sonarr:8989 --sonarr-key KEY \
  --search "Breaking Bad"

# Step 2: enqueue a specific episode file by ID
uv run scripts/enqueue_from_sonarr.py \
  --sonarr-url http://sonarr:8989 --sonarr-key KEY \
  --series-id 7 --episode-file-id 42

# Dry-run
uv run scripts/enqueue_from_sonarr.py \
  --sonarr-url http://sonarr:8989 --sonarr-key KEY \
  --series-id 7 --episode-file-id 42 --dry-run
```

Both scripts print the full payload before firing it and show the HTTP status and response body.


## Useful Scripts

### Finding a Plex token

Plex server is running in a container:

```bash
docker exec -it plex /bin/bash

cat /config/Library/Application\ Support/Plex\ Media\ Server/Preferences.xml | grep -oP 'PlexOnlineToken="\K[^"]+' 