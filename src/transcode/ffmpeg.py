import os
import json
import uuid
import shutil
import logging
import threading
import subprocess

logger = logging.getLogger(__name__)

TRANSCODE_TEMP_PRIMARY = os.getenv("TRANSCODE_TEMP_DIR", "/dev/shm")
TRANSCODE_TEMP_FALLBACK = os.getenv("TRANSCODE_TEMP_FALLBACK", "/transcode-temp")

HEVC_ALIASES = {"x265", "h265", "h.265", "hevc"}
_TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}


def get_stream_info(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(res.stdout)


LOUDNESS_SAMPLE_SECONDS = int(os.getenv("LOUDNESS_SAMPLE_SECONDS", "600"))

# QSV hardware decoders for common input codecs (Gen 8+ iGPU).
# Keeping frames on the GPU avoids a CPU↔GPU copy before hevc_qsv encoding.
_QSV_DECODER = {
    "h264": "h264_qsv", "avc": "h264_qsv", "x264": "h264_qsv",
    "hevc": "hevc_qsv", "h265": "hevc_qsv", "x265": "hevc_qsv",
    "mpeg2video": "mpeg2_qsv", "mpeg2": "mpeg2_qsv",
    "vc1": "vc1_qsv",
    "vp9": "vp9_qsv",
}

# ICQ quality target (1–51, lower = better). ~23 is visually transparent for 1080p HEVC.
HEVC_ICQ_QUALITY = int(os.getenv("HEVC_ICQ_QUALITY", "23"))

# Limits concurrent QSV video encodes — iGPU degrades beyond ~2 simultaneous sessions.
_MAX_QSV_SESSIONS = int(os.getenv("MAX_CONCURRENT_QSV_SESSIONS", "2"))
_qsv_semaphore = threading.BoundedSemaphore(_MAX_QSV_SESSIONS)

# Serializes temp-dir allocation so concurrent workers don't double-count free space.
_disk_lock = threading.Lock()
_primary_reserved: int = 0


def get_loudness_stats(
    path: str,
    orig_lang: str | None = None,
) -> dict | None:
    # Select the same stream the encoder will use:
    # prefer original language; if 5.1 present use that, else stereo fallback.
    # Set LOUDNESS_SAMPLE_SECONDS=0 to analyze the full file.
    if orig_lang:
        audio_map = f"0:a:m:language:{orig_lang}"
    else:
        audio_map = "0:a:0"

    cmd = ["ffmpeg", "-i", path]
    if LOUDNESS_SAMPLE_SECONDS > 0:
        cmd += ["-t", str(LOUDNESS_SAMPLE_SECONDS)]
    # -vn skips video demux entirely; downmix to stereo before loudnorm avoids EAC3 Atmos issues.
    cmd += ["-vn", "-map", audio_map, "-af", "aresample=48000,aformat=sample_fmts=fltp,loudnorm=print_format=json", "-ac", "2", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        json_start = res.stderr.rfind("{")
        if json_start == -1:
            raise ValueError("no JSON in stderr")
        return json.loads(res.stderr[json_start:])
    except Exception:
        logger.debug(f"loudnorm stderr tail: {res.stderr[-500:]}")
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


def _pick_temp_dir(required: int) -> str:
    global _primary_reserved
    with _disk_lock:
        free = shutil.disk_usage(TRANSCODE_TEMP_PRIMARY).free
        if free - _primary_reserved >= required:
            _primary_reserved += required
            return TRANSCODE_TEMP_PRIMARY
    logger.warning(f"Not enough space in {TRANSCODE_TEMP_PRIMARY}, using {TRANSCODE_TEMP_FALLBACK}")
    os.makedirs(TRANSCODE_TEMP_FALLBACK, exist_ok=True)
    return TRANSCODE_TEMP_FALLBACK


def _release_temp_reservation(temp_dir: str, size: int):
    global _primary_reserved
    if temp_dir == TRANSCODE_TEMP_PRIMARY:
        with _disk_lock:
            _primary_reserved = max(0, _primary_reserved - size)


def transcode_file(
    path: str,
    codec: str | None = None,
    bitrate_kbps: int | None = None,
    orig_lang: str | None = None,
    has_51: bool | None = None,
    dry_run: bool = False,
    job_id: int | None = None,
):
    prefix = f"[job {job_id}] " if job_id is not None else ""

    try:
        # Always probe — needed to identify text subtitle tracks and fill any missing metadata
        logger.info(f"{prefix}Probing stream info...")
        info = get_stream_info(path)
        streams = info.get("streams", [])
        fmt = info.get("format", {})
        v_stream = next((s for s in streams if s["codec_type"] == "video"), {})
        a_streams = [s for s in streams if s["codec_type"] == "audio"]
        s_streams = [s for s in streams if s["codec_type"] == "subtitle"]
        if codec is None:
            codec = v_stream.get("codec_name", "")
        if bitrate_kbps is None:
            bitrate_kbps = int(fmt.get("bitrate", 0)) // 1000
        if orig_lang is None:
            main_audio = a_streams[0] if a_streams else {}
            orig_lang = main_audio.get("tags", {}).get("language", "eng")
        # Resolve the audio-only index of the first stream matching orig_lang.
        # Using a numeric index (0:a:N) is unambiguous vs language metadata selectors.
        audio_idx = next(
            (i for i, s in enumerate(a_streams) if s.get("tags", {}).get("language") == orig_lang),
            0,
        )
        target_audio = a_streams[audio_idx] if a_streams else {}
        audio_abs_idx = target_audio.get("index", 0)
        if has_51 is None:
            has_51 = target_audio.get("channels", 0) >= 6
        logger.info(f"{prefix}Stream info: codec={codec}, bitrate={bitrate_kbps}kbps, lang={orig_lang}, 5.1={has_51}")

        text_sub_indices = [
            s["index"] for s in s_streams
            if s.get("codec_name", "").lower() in _TEXT_SUB_CODECS
        ]
        image_sub_count = len(s_streams) - len(text_sub_indices)
        if image_sub_count > 0:
            logger.info(f"{prefix}Dropping {image_sub_count} image-based subtitle track(s), keeping {len(text_sub_indices)} text track(s)")
        elif text_sub_indices:
            logger.info(f"{prefix}Keeping {len(text_sub_indices)} text subtitle track(s)")

        needs_sub_strip = image_sub_count > 0

        codec_norm = (codec or "").lower().replace(" ", "")
        needs_video = not (codec_norm in HEVC_ALIASES and (bitrate_kbps or 0) <= 8000)
        logger.info(f"{prefix}Needs video transcode: {needs_video} (codec={codec}, bitrate={bitrate_kbps}kbps)")

        logger.info(f"{prefix}Running loudness analysis (this may take several minutes)...")
        stats = get_loudness_stats(path, orig_lang=orig_lang)
        needs_loudnorm, needs_dynaudnorm = _audio_needs(stats)
        needs_audio = needs_loudnorm or needs_dynaudnorm
        if stats:
            logger.info(f"{prefix}Loudness: {stats.get('input_i')} LUFS, LRA={stats.get('input_lra')} LU → loudnorm={needs_loudnorm}, dynaudnorm={needs_dynaudnorm}")
        else:
            logger.warning(f"{prefix}Loudness analysis returned no stats — will apply full audio normalization")

        if not needs_video and not needs_audio and not needs_sub_strip:
            logger.info(f"{prefix}No transcode needed: '{path}'")
            return

        if dry_run:
            logger.info(
                f"{prefix}[DRY RUN] '{path}': video={needs_video}, loudnorm={needs_loudnorm}, "
                f"dynaudnorm={needs_dynaudnorm}, lang={orig_lang}, 5.1={has_51}, sub_strip={needs_sub_strip}"
            )
            return

        # Build ffmpeg command — use QSV hw decode when available to keep
        # frames on GPU and avoid CPU↔GPU copy before hevc_qsv encoding.
        qsv_decoder = _QSV_DECODER.get(codec_norm) if needs_video else None
        if qsv_decoder:
            cmd = [
                "ffmpeg", "-y",
                "-hwaccel", "qsv", "-hwaccel_output_format", "qsv",
                "-c:v", qsv_decoder,
                "-i", path, "-map", "0:v",
            ]
            logger.info(f"{prefix}Using QSV hw decode: {qsv_decoder}")
        else:
            cmd = ["ffmpeg", "-y", "-i", path, "-map", "0:v"]

        if needs_video:
            # ICQ (Intelligent Constant Quality) — better quality/size than fixed
            # bitrate; QSV allocates bits per-scene rather than averaging.
            # -fps_mode cfr: enforce constant frame rate so QSV hardware encoder
            # doesn't produce A/V desync on VFR source material.
            cmd += ["-fps_mode", "cfr", "-c:v", "hevc_qsv", "-rc_mode", "icq",
                    "-global_quality", str(HEVC_ICQ_QUALITY), "-preset", "medium",
                    "-extbrc", "1", "-look_ahead", "1", "-look_ahead_depth", "20"]
        else:
            cmd += ["-c:v", "copy"]

        if needs_audio:
            audio_filter = _build_audio_filter(stats, needs_loudnorm, needs_dynaudnorm)
            if has_51:
                cmd += [
                    "-map", f"0:{audio_abs_idx}",
                    "-map", f"0:{audio_abs_idx}",
                    "-c:a:0", "aac", "-ac:0", "2", "-b:a:0", "192k",
                    "-c:a:1", "ac3", "-b:a:1", "640k",
                    "-disposition:a:0", "default", "-disposition:a:1", "0",
                ]
                if audio_filter:
                    cmd += ["-af:0", audio_filter, "-af:1", audio_filter]
            else:
                cmd += [
                    "-map", f"0:{audio_abs_idx}",
                    "-c:a", "aac", "-ac", "2", "-b:a", "192k",
                ]
                if audio_filter:
                    cmd += ["-af", audio_filter]
        else:
            cmd += [
                "-map", f"0:{audio_abs_idx}",
                "-c:a", "copy",
            ]

        for idx in text_sub_indices:
            cmd += ["-map", f"0:{idx}"]
        if text_sub_indices:
            cmd += ["-c:s", "copy"]

        file_size = os.path.getsize(path)
        temp_dir = _pick_temp_dir(file_size)
        tmp = os.path.join(temp_dir, f"{uuid.uuid4().hex}.mkv")
        logger.info(f"{prefix}Encoding to temp file in {temp_dir}...")
        try:
            if needs_video:
                with _qsv_semaphore:
                    logger.info(f"{prefix}QSV slot acquired, encoding...")
                    result = subprocess.run(cmd + [tmp], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            else:
                result = subprocess.run(cmd + [tmp], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}")
            shutil.move(tmp, path)
            logger.info(f"{prefix}Transcode complete: '{path}'")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
            _release_temp_reservation(temp_dir, file_size)

    except Exception as e:
        logger.error(f"{prefix}transcode_file failed for '{path}': {e}")
        raise
