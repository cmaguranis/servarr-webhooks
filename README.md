# servarr-webhooks

A collection of webhooks used by different *arr services — Overseerr, Radarr, and Sonarr.

## Setup

### Docker Compose (recommended)

Copy `docker-compose.yml`, fill in your volume paths and environment variables, then:

```bash
docker compose up -d
```

On first run, `/config/config.ini` is created automatically from the built-in defaults. Edit it on the host to change runtime settings without restarting.

### Environment variables

| Variable | Description |
|---|---|
| `SEERR_BASEURL` | Overseerr base URL (e.g. `http://192.168.1.x:5055`) |
| `SEERR_API_KEY` | Overseerr API key |
| `RADARR_BASEURL` | Radarr base URL (e.g. `http://192.168.1.x:7878`) |
| `RADARR_API_KEY` | Radarr API key |
| `SONARR_BASEURL` | Sonarr base URL (e.g. `http://192.168.1.x:8989`) |
| `SONARR_API_KEY` | Sonarr API key |
| `SONARR_TARGET_QUALITY_PROFILE_ID` | Quality profile ID to apply after first episode download |
| `ROOT_FOLDER_ANIME_MOVIES` | Root folder path for anime movies |
| `TRANSCODE_WORKERS` | Parallel transcode jobs (default: `1`) |

### Runtime config (`/config/config.ini`)

Edit on the host without restarting the container:

```ini
[transcode]
skip_groups = yify, yts, judas   ; comma-separated, case-insensitive
cleanup_done_days = 7
cleanup_failed_days = 21
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

---

## Promote Cache Webhook

Moves media from an SSD cache to spinning disk (HDD) based on the date it was added to Radarr/Sonarr. Items older than 8 days are eligible for promotion.

```
POST http://your-host:5001/promote-cache
```

Trigger this on a schedule (e.g. via cron or a Compose-managed container) to periodically sweep eligible media.

---

## Sonarr Quality Profile Webhook

Updates the quality profile on a series after the premiere episode is downloaded, switching from a fast 720p profile to a high-quality 1080p profile for the remainder of the series.

Requires `SONARR_TARGET_QUALITY_PROFILE_ID` — set it to the numeric ID of the target profile. Find profile IDs at:

```
http://your-sonarr-host:8989/api/v3/qualityprofile?apikey=your_key
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

# Trigger promote-cache sweep
python scripts/test_webhooks.py promote

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
