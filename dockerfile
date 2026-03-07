FROM python:3.13-slim-bookworm

# Copy uv binary directly (faster than pip install uv)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# jellyfin-ffmpeg7: full Intel QSV/oneVPL support (bookworm/amd64)
# intel-media-va-driver: iHD driver for Intel Gen9+ QSV
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://repo.jellyfin.org/ubuntu/jellyfin_team.gpg.key | gpg --dearmor -o /usr/share/keyrings/jellyfin.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian bookworm main" > /etc/apt/sources.list.d/jellyfin.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      jellyfin-ffmpeg7 intel-media-va-driver libva2 libva-drm2 && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/lib/jellyfin-ffmpeg:$PATH"

ENV SEERR_BASEURL='' SEERR_API_KEY=''
ENV RADARR_BASEURL='' RADARR_API_KEY=''
ENV SONARR_BASEURL='' SONARR_API_KEY=''
ENV SONARR_TARGET_QUALITY_PROFILE_ID=''
ENV ROOT_FOLDER_ANIME_MOVIES=''
ENV TRANSCODE_WORKERS='1'
ENV TRANSCODE_DB='/config/data/transcode_queue.db'
ENV TRANSCODE_TEMP_DIR='/dev/shm'
ENV TRANSCODE_TEMP_FALLBACK='/transcode-temp'
ENV CONFIG_PATH='/config/config.ini'

WORKDIR /app
ADD pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

ADD main.py config.ini.default ./
ADD src/ ./src/

CMD ["uv", "run", "python", "main.py"]
