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
   * [Dry-run mode](#dry-run-mode)
   * [Enqueue a single file](#enqueue-a-single-file)
   * [Enqueue a folder](#enqueue-a-folder)
- [Manual Import Scan](#manual-import-scan)
- [Transcoding Manually Added Media](#transcoding-manually-added-media)
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

### Runtime config (`/config/config.ini`)

Edit on the host without restarting. Each key can also be overridden by an env var named `SECTION_KEY` (e.g. `TRANSCODE_WORKER_COUNT`).

```ini
[worker]
poll_interval = 120          ; seconds between job queue polls

[transcode]
cleanup_done_days = 7
cleanup_failed_days = 21
worker_count = 1
hevc_icq_quality = 23            ; ICQ quality target (1–51, lower = better)
max_concurrent_qsv_sessions = 2  ; iGPU degrades beyond ~2 simultaneous encodes
temp_primary = /dev/shm          ; fast temp dir (uses RAM, needs shm_size in compose)
temp_fallback = /transcode-temp  ; fallback when temp_primary is full

[plex]
collection_days = 14      ; days in Leaving Soon before item is deleted
unwatched_days = 30       ; days since added before an unwatched item is added to collection
watched_last_viewed_days = 14  ; days since last viewed before a watched item is added to collection
watched_added_days = 14   ; minimum days since added for the watched rule to apply
movie_batch = 100         ; movies fetched per Plex API call
collection_name = Leaving Soon
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

Automatically transcodes newly imported or upgraded files to HEVC with normalized audio. Jobs are queued in SQLite and processed by a background worker using Intel QSV hardware acceleration.

**What it does:**
- Skips files already tagged `transcoded` in Radarr/Sonarr (bypass with `?media_test=true`)
- Skips files already encoded as HEVC at ≤ 8 Mbps
- Skips files whose loudness is already within target (LUFS and LRA checks)
- Normalizes loud/quiet audio with `loudnorm` and compresses high dynamic range with `dynaudnorm`
- For 5.1 sources: creates a normalized stereo AAC track + normalized 5.1 AC3 track
- Tags processed files as `transcoded` in Radarr/Sonarr after encode completes
- Issues a disk rescan in Radarr/Sonarr after the transcode completes

### Setup

**Radarr (v6+)** — go to **Settings → Connect → + → Webhook**:

| Field | Value |
|---|---|
| **URL** | `http://your-host:5001/transcode-webhook` |
| **Method** | POST |
| **Triggers** | ✔ On Import · ✔ On Upgrade |

**Sonarr (v4+)** — same settings, same URL. Both send `eventType: "Download"` for On Import and On Upgrade — no custom payload template is needed.

### Webhook URL parameters

Append to the webhook URL in Radarr/Sonarr:

| Parameter | Description |
|---|---|
| `dry_run=true` | Analyse without encoding. Logs source media info and the ffmpeg command that would run. Stores `ffmpeg_cmd` in the job record. Bypasses the schedule pause. |
| `media_test=true` | Write the encoded output to `/data/media_test` instead of overwriting the source file. Safe for testing the full encode pipeline on real media. |

Example: `http://your-host:5001/transcode-webhook?media_test=true`

---

### API

#### Schedule

Controls whether the worker processes real encode jobs. When disabled, dry-run jobs still execute.

```bash
# Check state
curl http://localhost:5001/transcode/schedule

# Enable
curl -X POST "http://localhost:5001/transcode/schedule?enabled=true"

# Disable (defers real encodes; dry-run jobs still run)
curl -X POST "http://localhost:5001/transcode/schedule?enabled=false"
```

#### List jobs

```bash
# All jobs
curl http://localhost:5001/transcode/jobs

# Filter by status: pending, processing, done, failed
curl "http://localhost:5001/transcode/jobs?status=failed"
```

One-line summary per job:
```bash
curl "http://localhost:5001/transcode/jobs?status=done" | jq -r '
  .jobs[] | [
    "[\(.updated_at)]",
    "[ID: \(.id)]",
    (.status | ascii_upcase),
    (.meta.arr_type // "manual"),
    (.path | split("/") | .[-1]),
    "\(.meta.codec // "?")@\(.meta.bitrate_kbps // "?")kbps",
    (if .ffmpeg_cmd then "cmd=yes" else "cmd=no" end),
    (if .output_probe then "probe=yes" else "probe=no" end)
  ] | join(" | ")'
```

#### Inspect a job

Each completed job stores the source probe (at enqueue), the ffmpeg command used, and the output probe (after encode):

```bash
JOB_ID=58

# Source media info captured at enqueue time
curl "http://localhost:5001/transcode/jobs" | \
  jq --argjson id $JOB_ID '.jobs[] | select(.id==$id) | .probe | if . then fromjson else null end'

# ffmpeg command (with <output.mkv> placeholder for dry-run jobs)
curl "http://localhost:5001/transcode/jobs" | \
  jq -r --argjson id $JOB_ID '.jobs[] | select(.id==$id) | .ffmpeg_cmd'

# Output media info (null for dry-run jobs)
curl "http://localhost:5001/transcode/jobs" | \
  jq --argjson id $JOB_ID '.jobs[] | select(.id==$id) | .output_probe | if . then fromjson else null end'

# Side-by-side source vs output video fields
curl "http://localhost:5001/transcode/jobs" | jq --argjson id $JOB_ID '
  .jobs[] | select(.id==$id) | {
    source: (.probe | if . then fromjson | .video else null end),
    output: (.output_probe | if . then fromjson | .video else null end)
  }'
```

#### Retry a job

Preserves the job's current `dry_run` flag by default. Pass `?dry_run=true/false` to override.

```bash
# Retry preserving dry_run state
curl -X POST http://localhost:5001/transcode/jobs/58/retry

# Force a real encode
curl -X POST "http://localhost:5001/transcode/jobs/58/retry?dry_run=false"

# Force a dry-run
curl -X POST "http://localhost:5001/transcode/jobs/58/retry?dry_run=true"
```

#### Clear jobs

```bash
curl -X DELETE "http://localhost:5001/transcode/jobs?status=done"
curl -X DELETE "http://localhost:5001/transcode/jobs?status=failed"
```

#### Enqueue a single file

Enqueue a transcode job for one specific file.

```bash
curl -X POST http://localhost:5001/transcode/enqueue-file \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/movies/Interstellar (2014)/Interstellar.mkv"}'
```

Body fields:

| Field | Type | Description |
|---|---|---|
| `path` | string | **Required.** Absolute path to the file |
| `orig_lang` | string | Override audio language detection (e.g. `eng`, `jpn`) |

Query params:

| Parameter | Description |
|---|---|
| `dry_run=true` | Analyse without encoding |
| `media_test=true` | Write output to the media_test dir instead of overwriting the source |
| `full=true` | Transcode the entire file and write to the media_test dir (no slicing). Output is named `{parent_dir}__{filename}`. |

`full=true` and `media_test=true` are mutually exclusive — `full` takes priority.

```json
{"job_id": 42, "path": "/media/movies/Interstellar (2014)/Interstellar.mkv"}
```

#### Enqueue a folder

Scan a local folder and enqueue a transcode job for each media file found. Useful for bulk-transcoding test clips or files that missed the webhook.

```bash
curl -X POST http://localhost:5001/transcode/enqueue-folder \
  -H "Content-Type: application/json" \
  -d '{"path": "/data/media_test"}'
```

Query params: `?dry_run=true` enqueues jobs in dry-run mode (analyse without encoding). `?media_test=true` writes output to `/data/media_test` instead of overwriting the source. Both can be combined.

```json
{
  "enqueued": [{"job_id": 12, "path": "/data/media_test/Movies__Interstellar_3724s.mkv"}],
  "skipped": [],
  "errors": []
}
```

`skipped` = job already exists (idempotent). `errors` = ffprobe failed.

---

### Troubleshooting

#### Diagnosing an encode problem

1. Retry the job as a dry-run to see source info and the planned ffmpeg command without touching the file:
   ```bash
   curl -X POST "http://localhost:5001/transcode/jobs/58/retry?dry_run=true"
   ```

2. After it runs, fetch the stored ffmpeg command and source probe:
   ```bash
   curl "http://localhost:5001/transcode/jobs" | \
     jq -r '.jobs[] | select(.id==58) | .ffmpeg_cmd'

   curl "http://localhost:5001/transcode/jobs" | \
     jq '.jobs[] | select(.id==58) | .probe | fromjson | .video'
   ```

3. Or check the logs directly:
   ```
   [job 58] [DRY RUN] source media info: video=hevc pix_fmt=yuv420p10le ...
   [job 58] [DRY RUN] ffmpeg command: ffmpeg -y -hwaccel qsv ...
   ```

#### Verifying encode output

After a real or `media_test` encode, compare source and output video fields:

```bash
curl "http://localhost:5001/transcode/jobs" | jq '
  .jobs[] | select(.id==58) | {
    source: (.probe | if . then fromjson | .video else null end),
    output: (.output_probe | if . then fromjson | .video else null end)
  }'
```

Key fields: `pix_fmt` (should be `yuv420p`, not `yuv420p10le`), `color_transfer` (should be `bt709` for SDR).

#### Testing on real media without risk

Use `?media_test=true` on `enqueue-folder` or the webhook URL. Encodes write to `/data/media_test` — source files are never touched.

```bash
# Enqueue a folder in media_test mode (can combine with dry_run)
curl -X POST "http://localhost:5001/transcode/enqueue-folder?media_test=true" \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/Movies/The Dark Knight (2008)"}'

# Enable the schedule to process jobs
curl -X POST "http://localhost:5001/transcode/schedule?enabled=true"
```

---

## Manual Import Scan

Triggers Radarr or Sonarr to scan a folder for new files, import them into the library, and fire all configured On Import webhooks (including the transcode webhook).

```
POST http://your-host:5001/import-scan
```

| Field | Type | Description |
|---|---|---|
| `path` | string | **Required.** Absolute path to the folder or file to import |
| `arr` | string | `radarr`, `sonarr`, or `both` (default: `both`) |

```bash
# Movie (Radarr only)
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/import/The Dark Knight (2008)", "arr": "radarr"}'

# TV episode (Sonarr only)
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/import/Breaking Bad/Season 01", "arr": "sonarr"}'

# Try both (useful when unsure which app owns the file)
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/import/some-file.mkv"}'
```

```json
{
  "radarr": {"status": "queued", "commandId": 42},
  "sonarr": {"status": "queued", "commandId": 17}
}
```

Radarr issues `DownloadedMoviesScan`; Sonarr issues `DownloadedEpisodesScan`. Both match, rename, and move the file, then fire their On Import webhooks. Errors from one service do not block the other.

---

## Transcoding Manually Added Media

If a file was added to Plex outside of Radarr/Sonarr, the transcode webhook was never fired.

**Not yet in Radarr/Sonarr** — use `/import-scan` to have the arr app import the file and fire the webhook automatically:

```bash
curl -X POST http://localhost:5001/import-scan \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/Movies/The Dark Knight (2008)", "arr": "radarr"}'
```

**Already in Radarr/Sonarr** — enqueue a single file directly:

```bash
# Dry-run first (analyses without encoding)
curl -X POST "http://localhost:5001/transcode/enqueue-file?dry_run=true" \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/Movies/The Dark Knight (2008)/The.Dark.Knight.mkv"}'

# Then enqueue for real
curl -X POST http://localhost:5001/transcode/enqueue-file \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/Movies/The Dark Knight (2008)/The.Dark.Knight.mkv"}'
```

Or use `enqueue-folder` to process all files in a directory:

```bash
curl -X POST http://localhost:5001/transcode/enqueue-folder \
  -H "Content-Type: application/json" \
  -d '{"path": "/media/Movies/The Dark Knight (2008)"}'
```

---

## Test Media Generation

Generates short test clips from real media files to exercise ffmpeg encoding paths without processing entire files.

Scans `/data/media_cache` (and optionally `/media`) for media files, selects one file per unique codec/audio signature to maximize encoding-path coverage, then slices a random 30-second segment from each using stream copy (no re-encode).

### Environment variables

| Variable | Description |
|---|---|
| `MEDIA_TEST_WORKERS` | Parallel slice jobs (default: `1`) |
| `MEDIA_TEST_OUTPUT_DIR` | Where test clips are written (default: `/data/media_test`) |
| `MEDIA_TEST_CACHE_DIR` | Source directory to scan (default: `/data/media_cache`) |
| `MEDIA_DIR` | Main media directory, used when `include_media=true` (default: `/media`) |

### API

```bash
# Scan media_cache, enqueue one slice job per unique codec signature
curl -X POST http://localhost:5001/media-test/generate

# Also scan /media
curl -X POST "http://localhost:5001/media-test/generate?include_media=true"

# Dry-run: log what would be sliced without creating jobs or files
curl -X POST "http://localhost:5001/media-test/generate?dry_run=true"
```

```json
{
  "dry_run": false,
  "enqueued": [
    {
      "job_id": 7,
      "source": "/data/media_cache/Movies/Interstellar/Interstellar.mkv",
      "output": "/data/media_test/Interstellar__Interstellar_3724s.mkv",
      "start_sec": 3724,
      "signature": ["hevc", "eac3", 8],
      "arr_type": "radarr",
      "orig_lang": "eng"
    }
  ],
  "skipped": []
}
```

`skipped` lists output paths where a job already exists (idempotent).

```bash
# List jobs
curl http://localhost:5001/media-test/jobs
curl "http://localhost:5001/media-test/jobs?status=done"

# Clear jobs
curl -X DELETE "http://localhost:5001/media-test/jobs?status=done"
```

---

## Managarr Cleanup

Automatically categorises Plex media (movies and TV shows) against a set of rules and enqueues cleanup actions (add to watchlist collection, promote from cache to main storage, or delete via Radarr/Sonarr). A background worker processes jobs continuously; the enabled/disabled flag lets you pause it without stopping the service.

**Rules applied per item (movies and shows):**

| Condition | Action |
|---|---|
| Rating ≥ 8 and in `/media_cache` | Promote to `/media` |
| Rating ≥ 8 | Do nothing |
| Rating ≤ 3 | Delete |
| Unrated or rated 4–7, unwatched, added > `unwatched_days` ago | Add to collection |
| Unrated or rated 4–7, watched, last viewed > `watched_last_viewed_days` ago and added > `watched_added_days` ago | Add to collection |
| In collection for ≥ `collection_days` | Delete |

**Delete behavior for TV shows:**
- Continuing series (currently airing in Sonarr): episode files are deleted but the series remains monitored in Sonarr so new episodes are still downloaded
- Ended/cancelled series: the entire series is deleted from Sonarr including files

**State machine:** actions follow valid transition rules. Once a delete is issued it is permanent. A promote or add-to-collection can transition back to do-nothing (e.g. if the item is re-rated), but do-nothing can never skip directly to delete — the add-to-collection timer must elapse first.

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