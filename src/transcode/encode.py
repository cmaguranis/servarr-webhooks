import logging
import os
import shlex
import shutil
import subprocess
import threading
import uuid

from src import config
from src.transcode.audio import _audio_needs, _build_audio_filter, get_loudness_stats
from src.transcode.probe import _TEXT_SUB_CODECS, get_stream_info

logger = logging.getLogger(__name__)

TRANSCODE_TEMP_PRIMARY = config.TRANSCODE_TEMP_PRIMARY()
TRANSCODE_TEMP_FALLBACK = config.TRANSCODE_TEMP_FALLBACK()

HEVC_ALIASES = {"x265", "h265", "h.265", "hevc"}

# Only re-encode HEVC that exceeds this bitrate; below it the file is already compact enough.
# 6,667 kbps ≈ 50 MB/min → ~6 GB for a 2-hour movie.
_HEVC_BITRATE_THRESHOLD_KBPS = 6_667


def video_transcode_needed(codec: str | None, bitrate_kbps: int | None) -> bool:
    codec_norm = (codec or "").lower().replace(" ", "")
    return not (codec_norm in HEVC_ALIASES and bitrate_kbps and bitrate_kbps <= _HEVC_BITRATE_THRESHOLD_KBPS)

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
HEVC_ICQ_QUALITY = config.TRANSCODE_HEVC_ICQ_QUALITY()

# Limits concurrent QSV video encodes — iGPU degrades beyond ~2 simultaneous sessions.
_MAX_QSV_SESSIONS = config.TRANSCODE_MAX_CONCURRENT_QSV_SESSIONS()
_qsv_semaphore = threading.BoundedSemaphore(_MAX_QSV_SESSIONS)

# Serializes temp-dir allocation so concurrent workers don't double-count free space.
_disk_lock = threading.Lock()
_primary_reserved: int = 0


def _pick_temp_dir(required: int) -> str:
    global _primary_reserved
    with _disk_lock:
        free = shutil.disk_usage(TRANSCODE_TEMP_PRIMARY).free
        if free - _primary_reserved >= required:
            _primary_reserved += required
            return TRANSCODE_TEMP_PRIMARY
    available_mb = (free - _primary_reserved) // (1024 * 1024)
    required_mb = required // (1024 * 1024)
    logger.warning(f"Not enough space in {TRANSCODE_TEMP_PRIMARY} ({available_mb} MB available, {required_mb} MB needed), using {TRANSCODE_TEMP_FALLBACK}")
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
    output_path: str | None = None,
    start_sec: int | None = None,
    slice_duration: int | None = None,
    force_audio_only: bool = False,
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
        ffprobe_bitrate = int(v_stream.get("bit_rate") or fmt.get("bitrate") or 0) // 1000
        if not bitrate_kbps:  # 0, None, or missing from webhook
            bitrate_kbps = ffprobe_bitrate
        if orig_lang is None:
            main_audio = a_streams[0] if a_streams else {}
            orig_lang = main_audio.get("tags", {}).get("language", "eng")
        orig_lang_streams = [s for s in a_streams if s.get("tags", {}).get("language") == orig_lang]
        other_lang_count = len(a_streams) - len(orig_lang_streams)
        if other_lang_count:
            logger.info(f"{prefix}Stripping {other_lang_count} non-orig-lang audio stream(s) (keeping '{orig_lang}' only)")
        # Prefer the 5.1 stream when multiple orig_lang streams exist; fall back to first.
        # Using absolute stream index (not language selector) — unambiguous for EAC3 Atmos.
        if has_51 is None:
            target_audio = (
                next((s for s in orig_lang_streams if s.get("channels", 0) >= 6), None)
                or (orig_lang_streams[0] if orig_lang_streams else a_streams[0] if a_streams else {})
            )
            has_51 = target_audio.get("channels", 0) >= 6
        elif has_51:
            target_audio = (
                next((s for s in orig_lang_streams if s.get("channels", 0) >= 6), None)
                or (orig_lang_streams[0] if orig_lang_streams else a_streams[0] if a_streams else {})
            )
        else:
            target_audio = orig_lang_streams[0] if orig_lang_streams else (a_streams[0] if a_streams else {})
        audio_abs_idx = target_audio.get("index", 0)
        has_stereo = any(s.get("channels", 0) <= 2 for s in orig_lang_streams)
        needs_stereo_encode = has_51 and not has_stereo
        logger.info(f"{prefix}Stream info: codec={codec}, bitrate={bitrate_kbps}kbps, lang={orig_lang}, 5.1={has_51}, has_stereo={has_stereo}, strip_other_lang={other_lang_count}")

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
        needs_video = video_transcode_needed(codec, bitrate_kbps) and not force_audio_only
        logger.info(f"{prefix}Needs video transcode: {needs_video} (codec={codec}, bitrate={bitrate_kbps}kbps)")

        duration = float(fmt.get("duration") or 0)
        logger.info(f"{prefix}Running loudness analysis (5 min window, skip first 10%)...")
        stats = get_loudness_stats(path, audio_index=audio_abs_idx, duration=duration)
        needs_loudnorm = _audio_needs(stats)
        needs_audio = needs_loudnorm
        if stats:
            logger.info(f"{prefix}Loudness: {stats.get('input_i')} LUFS, LRA={stats.get('input_lra')} LU → loudnorm={needs_loudnorm}")
        else:
            logger.warning(f"{prefix}Loudness analysis returned no stats — will apply full audio normalization")

        if not needs_video and not needs_audio and not needs_sub_strip and not needs_stereo_encode:
            logger.info(f"{prefix}No transcode needed: '{path}'")
            return None

        # Build ffmpeg command — use QSV hw decode when available to keep
        # frames on GPU and avoid CPU↔GPU copy before hevc_qsv encoding.
        slice_args = ["-ss", str(start_sec), "-t", str(slice_duration or 30)] if start_sec is not None else []

        qsv_decoder = _QSV_DECODER.get(codec_norm) if needs_video else None
        if qsv_decoder:
            cmd = [
                "ffmpeg", "-y",
                "-hwaccel", "qsv", "-hwaccel_output_format", "qsv",
                "-c:v", qsv_decoder,
                *slice_args,
                "-i", path, "-map", "0:v",
            ]
            logger.info(f"{prefix}Using QSV hw decode: {qsv_decoder}")
        else:
            cmd = ["ffmpeg", "-y", *slice_args, "-i", path, "-map", "0:v"]

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

        is_mono = target_audio.get("channels", 2) == 1
        stereo_filter = _build_audio_filter(stats, needs_loudnorm, stereo=True)
        surround_filter = _build_audio_filter(stats, needs_loudnorm, stereo=False)
        if has_51:
            # Always dual tracks: AAC stereo (downmix from 5.1) + AC3 5.1.
            # stereo_filter uses aformat channel_layouts=stereo to force proper Atmos downmix.
            cmd += [
                "-map", f"0:{audio_abs_idx}",
                "-map", f"0:{audio_abs_idx}",
                "-c:a:0", "aac", "-b:a:0", "192k", "-ac:a:0", "2",
                "-c:a:1", "ac3", "-b:a:1", "640k",
                "-disposition:a:0", "default", "-disposition:a:1", "0",
                "-metadata:s:a:0", "title=AAC Stereo",
                "-metadata:s:a:1", "title=AC3 5.1",
                "-af:0", stereo_filter, "-af:1", surround_filter,
            ]
        elif is_mono and needs_audio:
            cmd += [
                "-map", f"0:{audio_abs_idx}",
                "-c:a", "aac", "-b:a", "192k",
                "-af", surround_filter,
            ]
        elif needs_audio:
            cmd += [
                "-map", f"0:{audio_abs_idx}",
                "-c:a", "aac", "-b:a", "192k",
                "-af", stereo_filter,
            ]
        else:
            cmd += [
                "-map", f"0:{audio_abs_idx}",
                "-c:a", "copy",
            ]

        for idx in text_sub_indices:
            cmd += ["-map", f"0:{idx}"]
        if text_sub_indices:
            cmd += ["-c:s", "copy"]

        if dry_run:
            logger.info(
                f"{prefix}[DRY RUN] source media info: "
                f"video={codec} profile={v_stream.get('profile')} level={v_stream.get('level')} "
                f"pix_fmt={v_stream.get('pix_fmt')} "
                f"{v_stream.get('width')}x{v_stream.get('height')} "
                f"fps={v_stream.get('avg_frame_rate')} field_order={v_stream.get('field_order', 'progressive')} "
                f"color_space={v_stream.get('color_space')} color_transfer={v_stream.get('color_transfer')} "
                f"audio={target_audio.get('codec_name')} channels={target_audio.get('channels')} "
                f"sample_rate={target_audio.get('sample_rate')} "
                f"needs_video={needs_video} needs_loudnorm={needs_loudnorm} needs_sub_strip={needs_sub_strip} needs_stereo={needs_stereo_encode}"
            )
            logger.info(f"{prefix}[DRY RUN] ffmpeg command: {shlex.join(cmd + ['<output.mkv>'])}")
            return shlex.join(cmd + ["<output.mkv>"])

        file_size = os.path.getsize(path)
        temp_dir = _pick_temp_dir(file_size)
        tmp = os.path.join(temp_dir, f"{uuid.uuid4().hex}.mkv")
        logger.info(f"{prefix}ffmpeg command: {shlex.join(cmd + [tmp])}")
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
            dest = output_path or path
            try:
                os.replace(tmp, dest)  # atomic if same filesystem
            except OSError:
                shutil.copy2(tmp, dest)  # cross-device fallback; finally block cleans tmp
            logger.info(f"{prefix}Transcode complete: '{dest}'")
            return shlex.join(cmd + [dest])
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
            _release_temp_reservation(temp_dir, file_size)

    except Exception as e:
        logger.error(f"{prefix}transcode_file failed for '{path}': {e}")
        raise
