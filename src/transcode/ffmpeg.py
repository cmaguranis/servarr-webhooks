import os
import json
import uuid
import shutil
import logging
import subprocess

logger = logging.getLogger(__name__)

TRANSCODE_TEMP_PRIMARY = os.getenv("TRANSCODE_TEMP_DIR", "/dev/shm")
TRANSCODE_TEMP_FALLBACK = os.getenv("TRANSCODE_TEMP_FALLBACK", "/transcode-temp")

HEVC_ALIASES = {"x265", "h265", "h.265", "hevc"}

_LANG_MAP = {
    "english": "eng",
    "japanese": "jpn",
    "french": "fra",
    "spanish": "spa",
    "german": "deu",
    "korean": "kor",
    "chinese": "zho",
    "portuguese": "por",
    "italian": "ita",
}


def get_stream_info(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(res.stdout)


def get_loudness_stats(path: str) -> dict | None:
    cmd = ["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        json_start = res.stderr.rfind("{")
        return json.loads(res.stderr[json_start:])
    except Exception:
        return None


def _audio_needs(stats: dict | None) -> tuple[bool, bool]:
    if not stats:
        return True, True
    lufs = float(stats.get("input_i", 0))
    lra = float(stats.get("input_lra", 0))
    needs_loudnorm = lufs > -14.0 or lufs < -18.0
    needs_dynaudnorm = lra > 12.0
    return needs_loudnorm, needs_dynaudnorm


def _build_audio_filter(stats: dict | None, needs_loudnorm: bool, needs_dynaudnorm: bool) -> str:
    parts = []
    if needs_loudnorm and stats:
        parts.append(
            f"loudnorm=I=-16:LRA=7:TP=-1.5"
            f":measured_I={stats['input_i']}"
            f":measured_LRA={stats['input_lra']}"
            f":measured_TP={stats['input_tp']}"
            f":measured_thresh={stats['input_thresh']}"
        )
    elif needs_loudnorm:
        parts.append("loudnorm=I=-16:LRA=7:TP=-1.5")
    if needs_dynaudnorm:
        parts.append("dynaudnorm=f=150:g=15")
    return ",".join(parts)


def _pick_temp_dir(source_path: str) -> str:
    required = os.path.getsize(source_path)
    if shutil.disk_usage(TRANSCODE_TEMP_PRIMARY).free >= required:
        return TRANSCODE_TEMP_PRIMARY
    logger.warning(f"Not enough space in {TRANSCODE_TEMP_PRIMARY}, using {TRANSCODE_TEMP_FALLBACK}")
    os.makedirs(TRANSCODE_TEMP_FALLBACK, exist_ok=True)
    return TRANSCODE_TEMP_FALLBACK


def transcode_file(
    path: str,
    codec: str | None = None,
    bitrate_kbps: int | None = None,
    orig_lang: str | None = None,
    has_51: bool | None = None,
    dry_run: bool = False,
):
    try:
        # Fill missing metadata via ffprobe only for fields that are None
        if any(v is None for v in [codec, bitrate_kbps, orig_lang, has_51]):
            info = get_stream_info(path)
            streams = info.get("streams", [])
            fmt = info.get("format", {})
            v_stream = next((s for s in streams if s["codec_type"] == "video"), {})
            a_streams = [s for s in streams if s["codec_type"] == "audio"]
            if codec is None:
                codec = v_stream.get("codec_name", "")
            if bitrate_kbps is None:
                bitrate_kbps = int(fmt.get("bitrate", 0)) // 1000
            if orig_lang is None:
                main_audio = a_streams[0] if a_streams else {}
                lang_name = main_audio.get("tags", {}).get("language", "eng")
                orig_lang = _LANG_MAP.get(lang_name.lower(), lang_name)
            if has_51 is None:
                has_51 = any(s.get("channels", 0) >= 6 for s in a_streams)

        codec_norm = (codec or "").lower().replace(" ", "")
        needs_video = not (codec_norm in HEVC_ALIASES and (bitrate_kbps or 0) <= 8000)

        stats = get_loudness_stats(path)
        needs_loudnorm, needs_dynaudnorm = _audio_needs(stats)
        needs_audio = needs_loudnorm or needs_dynaudnorm

        if not needs_video and not needs_audio:
            logger.info(f"No transcode needed: '{path}'")
            return

        if dry_run:
            logger.info(
                f"[DRY RUN] '{path}': video={needs_video}, loudnorm={needs_loudnorm}, "
                f"dynaudnorm={needs_dynaudnorm}, lang={orig_lang}, 5.1={has_51}"
            )
            return

        # Build ffmpeg command
        cmd = ["ffmpeg", "-y", "-i", path, "-map", "0:v"]

        if needs_video:
            cmd += ["-c:v", "hevc_qsv", "-b:v", "5000k", "-preset", "slow"]
        else:
            cmd += ["-c:v", "copy"]

        if needs_audio:
            audio_filter = _build_audio_filter(stats, needs_loudnorm, needs_dynaudnorm)
            if has_51:
                cmd += [
                    "-map", f"0:a:m:language:{orig_lang}",
                    "-map", f"0:a:m:language:{orig_lang}",
                    "-c:a:0", "aac", "-ac:0", "2", "-b:a:0", "192k",
                    "-c:a:1", "ac3", "-b:a:1", "640k",
                    "-disposition:a:0", "default", "-disposition:a:1", "0",
                ]
                if audio_filter:
                    cmd += ["-af:0", audio_filter, "-af:1", audio_filter]
            else:
                cmd += [
                    "-map", f"0:a:m:language:{orig_lang}",
                    "-c:a", "aac", "-ac", "2", "-b:a", "192k",
                ]
                if audio_filter:
                    cmd += ["-af", audio_filter]
        else:
            cmd += [
                "-map", f"0:a:m:language:{orig_lang}" if orig_lang else "0:a",
                "-c:a", "copy",
            ]

        cmd += ["-map", "0:s?", "-c:s", "copy"]

        temp_dir = _pick_temp_dir(path)
        tmp = os.path.join(temp_dir, f"{uuid.uuid4().hex}.mkv")
        try:
            result = subprocess.run(cmd + [tmp], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}")
            os.replace(tmp, path)
            logger.info(f"Transcode complete: '{path}'")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    except Exception as e:
        logger.error(f"transcode_file failed for '{path}': {e}")
        raise
