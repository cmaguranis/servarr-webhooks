import os
import time
import logging
import requests
import copy
from datetime import datetime, timezone, timedeltax
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

def update_media_path(media_item, threshold_date):
  # Extract the added date string for parsing
  added_str = media_item.get('added')
  if not added_str:
    return None

  # Parse ISO format and handle UTC 'Z' suffix
  added_date = datetime.fromisoformat(added_str.replace('Z', '+00:00'))
  
  # Only proceed if the item is older than the 7-day threshold
  if added_date > threshold_date:
    return None

  # Create a copy to prevent in-place mutation of the original item
  updated_item = copy.copy(media_item)
  
  logger.info(f"Promoting '{updated_item.get('title')}' (id={updated_item.get('id')}, added={added_str})")
  
  # Calculate new destination paths
  new_root = updated_item.get('rootFolderPath', '').replace('/media_cache', '/media')
  new_path = updated_item.get('path', '').replace('/media_cache', '/media')
  
  updated_item['path'] = new_path
  updated_item['rootFolderPath'] = new_root

  return updated_item
    
    # Trigger the Arr-internal move
    requests.put(f"{cfg['url']}/api/v3/{cfg['type']}/{media_item.get('id')}?moveFiles=true", headers=headers, json=media_item)

@app.route('/promote-cache', methods=['POST'])
def promote_cache():
  """Moves media from SSD to HDD based on API 'added' date and rating."""
  configs = [
    {'name': 'Radarr', 'url': RADARR_BASEURL, 'key': RADARR_API_KEY, 'type': 'movie'},
    {'name': 'Sonarr', 'url': SONARR_BASEURL, 'key': SONARR_API_KEY, 'type': 'series'}
  ]
  
  # Define the 7-day + 1 day buffer threshold
  threshold_date = datetime.now(timezone.utc) - timedelta(days=8)
  
  for cfg in configs:
    try:
      media = requests.get(f"{cfg['url']}/api/v3/{cfg['type']}", headers={'X-Api-Key': cfg['key']}).json()
      
      for media_item in media:
        updated_media_item = update_media_path(media_item=media_item, threshold_date=threshold_date)
      
        if updated_media_item:
          # Trigger the Arr-internal move
          requests.put(f"{cfg['url']}/api/v3/{cfg['type']}/{updated_media_item.get('id')}?moveFiles=true", headers=headers, json=updated_media_item)
    except Exception as e:
      logger.error(f"[{cfg['name']}] promotion failed for '{media_item.get('title')}' (id={media_item.get('id')}: {e}")

  return jsonify({"status": "promotion check complete"}), 200


if __name__ == '__main__':
  serve(app, host='0.0.0.0', port='5001')
