import os
import time
import logging
import requests
from flask import Flask, request, jsonify
from waitress import serve
from dotenv import load_dotenv

load_dotenv()

SEERR_BASEURL = os.getenv("SEERR_BASEURL")
SEERR_API_KEY = os.getenv("SEERR_API_KEY")
RADARR_BASEURL = os.getenv("RADARR_BASEURL")
RADARR_API_KEY = os.getenv("RADARR_API_KEY")
SONARR_BASEURL = os.getenv("SONARR_BASEURL")
SONARR_API_KEY = os.getenv("SONARR_API_KEY")
SONARR_TARGET_QUALITY_PROFILE_ID = os.getenv("SONARR_TARGET_QUALITY_PROFILE_ID")
ROOT_FOLDER_ANIME_MOVIES = os.getenv("ROOT_FOLDER_ANIME_MOVIES")

logging.basicConfig(
  level=logging.INFO,
  format='[%(asctime)s] %(levelname)s: %(message)s',
  datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def update_radarr_path(tmdb_id, new_root_path):
  """Updates the path for an existing movie directly in Radarr."""
  search_url = f"{RADARR_BASEURL}/api/v3/movie?tmdbId={tmdb_id}"
  headers = {'X-Api-Key': RADARR_API_KEY}

  try:
    response = requests.get(search_url, headers=headers)
    movies = response.json()

    if not movies:
      logger.warning(f"Radarr: movie tmdbId={tmdb_id} not found")
      return

    movie = movies[0]
    movie_id = movie['id']

    folder_name = os.path.basename(movie['path'])
    movie['path'] = os.path.join(new_root_path, folder_name)
    movie['rootFolderPath'] = new_root_path

    put_url = f"{RADARR_BASEURL}/api/v3/movie/{movie_id}"
    res = requests.put(put_url, headers=headers, json=movie)
    if res.status_code in (200, 202):
      logger.info(f"Radarr: moved '{movie['title']}' to {new_root_path}")
    else:
      logger.error(f"Radarr: path update failed for tmdbId={tmdb_id} — {res.status_code} {res.text}")

  except Exception as e:
    logger.error(f"Radarr: unexpected error for tmdbId={tmdb_id} — {e}")

@app.route('/seerr_webhook', methods=['POST'])
def handle_seerr_webhook():
  request_data = request.get_json()

  request_id = request_data.get('requestID')
  media_tmdbid = request_data.get('mediaId')
  media_type = request_data.get('mediaType')

  if not all([request_id, media_tmdbid, media_type]):
    logger.warning(f"Seerr: missing fields in payload — {request_data}")
    return ('Bad Request', 400)

  # Wait for initial auto-approve to finish
  time.sleep(3)

  headers = {'X-Api-Key': SEERR_API_KEY, 'accept': 'application/json'}
  try:
    data = requests.get(f"{SEERR_BASEURL}/api/v1/{media_type}/{media_tmdbid}", headers=headers).json()
  except Exception as e:
    logger.error(f"Seerr: failed to fetch metadata for {media_type} tmdbId={media_tmdbid} — {e}")
    return ('Error', 500)

  title = data.get('title') or data.get('name', f'tmdbId={media_tmdbid}')
  is_anime = ('animation' in [g['name'].lower() for g in data.get('genres', [])] and data.get('originalLanguage', '').lower() == 'ja')

  if media_type == 'movie' and is_anime:
    res = requests.put(f"{SEERR_BASEURL}/api/v1/request/{request_id}", headers=headers,
                       json={"mediaType": "movie", "rootFolder": ROOT_FOLDER_ANIME_MOVIES})
    if res.status_code in (200, 202):
      logger.info(f"Seerr: routed '{title}' to anime folder (requestId={request_id})")
    else:
      logger.error(f"Seerr: failed to update request {request_id} for '{title}' — {res.status_code} {res.text}")

    update_radarr_path(media_tmdbid, ROOT_FOLDER_ANIME_MOVIES)
  else:
    logger.info(f"Seerr: no action for '{title}' (type={media_type}, anime={is_anime})")

  return ('Success', 202)

def update_quality_profile(series_id, series_title):
  endpoint = f"{SONARR_BASEURL}/api/v3/series/{series_id}"
  headers = {"X-Api-Key": SONARR_API_KEY}

  response = requests.get(endpoint, headers=headers)
  if response.status_code != 200:
    logger.error(f"Sonarr: failed to fetch series id={series_id} — {response.status_code}")
    return False

  series_data = response.json()
  series_data["qualityProfileId"] = SONARR_TARGET_QUALITY_PROFILE_ID

  put_response = requests.put(endpoint, headers=headers, json=series_data)
  if put_response.status_code in (200, 202):
    logger.info(f"Sonarr: updated quality profile for '{series_title}' (id={series_id}) to profile {SONARR_TARGET_QUALITY_PROFILE_ID}")
    return True
  else:
    logger.error(f"Sonarr: failed to update quality profile for '{series_title}' (id={series_id}) — {put_response.status_code} {put_response.text}")
    return False

@app.route('/sonarr-webhook', methods=['POST'])
def handle_sonarr_webhook():
  data = request.json

  event_type = data.get('eventType')

  if event_type and event_type.lower() == 'download':
    series = data.get('series', {})
    series_id = series.get('id')
    series_title = series.get('title', f'id={series_id}')
    episodes = data.get('episodes', [])

    is_premiere = any(ep.get('seasonNumber') == 1 and ep.get('episodeNumber') == 1 for ep in episodes)

    if is_premiere and series_id:
      success = update_quality_profile(series_id, series_title)
      status = "updated" if success else "failed"
      return jsonify({"status": f"quality profile {status}"}), 200

    logger.info(f"Sonarr: no action for '{series_title}' (event={event_type}, premiere={is_premiere})")

  return jsonify({"status": "ignored"}), 200

if __name__ == '__main__':
  serve(app, host='0.0.0.0', port='5001')
