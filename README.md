# servarr-webhooks

A collection of webhooks used by different *arr services, currently Seerr, Radarr, and Sonarr.

## Setup

### .env
```
SEERR_BASEURL=http://192.168.1.x:5055
SEERR_API_KEY=your_key
RADARR_BASEURL=http://192.168.1.x:7878
RADARR_API_KEY=your_key
SONARR_BASEURL=http://192.168.1.x:8989
SONARR_API_KEY=your_key
ROOT_FOLDER_ANIME_MOVIES=/path/to/anime/movies
```

### Native
1. Install dependencies
```bash
pip install -r requirements.txt
```
2. Configure `.env`
3. Run with `python main.py` or detached: `nohup python main.py &`

#### Docker
1. Build the image
```
docker build -t servarr-webhooks .
```
2. Run with env file
```
docker run --env-file ./.env servarr-webhooks
```

## Seer Webhook

Automatically routes anime movie requests to a separate root folder in Radarr via Seerr webhooks.

When a movie request comes in, the script checks if it is an anime movie (Animation genre + Japanese original language). If so, it updates both the Seerr request and Radarr directly to use the configured anime movies root folder.

### Seerr Webhook Config
In Seerr, enable the **Webhook** notification agent with the **Request Approved** event and point it at `http://your-host:5001/seerr_webhook`.

Use this JSON template:
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

## Sonarr `On Import` Webhook

Updates the Quality Profile from a 720p quick DL for the first episode to a high quality 1080p for the series. Shows are configured this way to try and get the first episode as fast as possible for a more seamless streaming experience.

Also requires `SONARR_TARGET_QUALITY_PROFILE_ID` in `.env` — set it to the numeric ID of the quality profile you want applied to the series after the premiere is downloaded. You can find profile IDs at `http://your-sonarr-host:8989/api/v3/qualityprofile?apikey=your_key`.

### Sonarr Webhook Config
In Sonarr, go to **Settings → Connect → + → Webhook** and configure:

- **Name**: anything (e.g. `servarr-webhooks`)
- **Triggers**: enable **On Import** only
- **URL**: `http://your-host:5001/sonarr-webhook`
- **Method**: POST

Save, then use **Test** to verify connectivity.
