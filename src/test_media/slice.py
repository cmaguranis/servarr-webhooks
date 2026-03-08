import logging
import os
import shlex
import subprocess
from pathlib import Path

from src.transcode.probe import get_stream_info

logger = logging.getLogger(__name__)


def get_duration(path: str) -> float:
    """Returns file duration in seconds via ffprobe. Raises RuntimeError on failure."""
    info = get_stream_info(path)
    raw = (info.get("format") or {}).get("duration")
    if not raw:
        raise RuntimeError(f"ffprobe returned no duration for: {path}")
    return float(raw)


def get_media_signature(path: str) -> tuple:
    """Returns (video_codec, audio_codec, audio_channels) for codec-based deduplication."""
    info = get_stream_info(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return (
        video.get("codec_name", "unknown"),
        audio.get("codec_name", "unknown"),
        audio.get("channels", 0),
    )


def build_output_path(source_path: str, start_sec: int, output_dir: str) -> str:
    """Builds output path including parent dir name to avoid cross-subdir filename collisions.
    e.g. /data/media_test/Movies__Interstellar_3724s.mkv"""
    p = Path(source_path)
    subdir = p.parent.name
    return os.path.join(output_dir, f"{subdir}__{p.stem}_{start_sec}s{p.suffix}")


def slice_file(
    source_path: str,
    output_path: str,
    start_sec: int,
    duration_sec: int = 30,
    dry_run: bool = False,
    job_id: int | None = None,
):
    label = f"[job {job_id}]" if job_id is not None else "[slice]"

    # Validate duration before running ffmpeg
    file_duration = get_duration(source_path)
    if start_sec + duration_sec > file_duration:
        raise ValueError(
            f"Slice out of bounds: start={start_sec}s + duration={duration_sec}s "
            f"exceeds file length {file_duration:.1f}s"
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),   # input-side seek (fast keyframe seek)
        "-i", source_path,
        "-t", str(duration_sec),
        "-c", "copy",            # stream copy — no re-encode
        "-map", "0",             # copy all streams (video, audio, subtitles)
        output_path,
    ]

    if dry_run:
        logger.info(f"{label} Dry run: {shlex.join(cmd)}")
        return

    logger.info(f"{label} Slicing {duration_sec}s from {source_path} at {start_sec}s → {output_path}")
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        # Clean up partial output before raising so a future enqueue attempt isn't blocked
        if os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError(
            f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}"
        )

    logger.info(f"{label} Done: {output_path}")
