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

## Manual Testing

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

# Point at a non-local host
python scripts/test_webhooks.py --url http://192.168.1.10:5001 radarr
```
