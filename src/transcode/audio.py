import json
import logging
import subprocess

logger = logging.getLogger(__name__)

LOUDNESS_SAMPLE_SECONDS = 300  # fixed 5-minute window

_AUDIO_FILTER_PREFIX = "aresample=48000,aformat=sample_fmts=fltp"
_AUDIO_FILTER_PREFIX_STEREO = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


def _run_loudnorm(path: str, audio_map: str, duration: float | None) -> dict | None:
    cmd = ["ffmpeg"]
    sample_seconds = LOUDNESS_SAMPLE_SECONDS
    if duration and duration > 0:
        skip = duration * 0.10
        sample_seconds = min(LOUDNESS_SAMPLE_SECONDS, duration - skip)
        cmd += ["-ss", f"{skip:.1f}"]
    cmd += ["-i", path, "-t", f"{sample_seconds:.1f}"]
    # channel_layouts=stereo forces downmix before loudnorm — avoids EAC3 Atmos object-audio issues.
    cmd += ["-vn", "-map", audio_map, "-af",
            "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,loudnorm=print_format=json",
            "-f", "null", "-"]
    logger.info(f"loudnorm command: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    json_start = res.stderr.rfind("{")
    if json_start == -1:
        logger.warning(f"loudnorm stderr tail ({audio_map}): {res.stderr[-500:]}")
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(res.stderr, json_start)
        return obj
    except Exception:
        logger.warning(f"loudnorm stderr tail ({audio_map}): {res.stderr[-500:]}")
        return None


def get_loudness_stats(
    path: str,
    audio_index: int = 0,
    duration: float | None = None,
) -> dict | None:
    # Use absolute stream index — unambiguous even when language tags are missing/wrong.
    return _run_loudnorm(path, f"0:{audio_index}", duration)


def _audio_needs(stats: dict | None) -> tuple[bool, bool]:
    if not stats:
        return True, True
    lufs = float(stats.get("input_i", 0))
    lra = float(stats.get("input_lra", 0))
    needs_loudnorm = lufs > -14.0 or lufs < -18.0
    needs_dynaudnorm = lra > 12.0
    return needs_loudnorm, needs_dynaudnorm


def _build_audio_filter(stats: dict | None, needs_loudnorm: bool, needs_dynaudnorm: bool, stereo: bool = False) -> str:
    parts = [_AUDIO_FILTER_PREFIX_STEREO if stereo else _AUDIO_FILTER_PREFIX]
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
    # loudnorm can output at 192kHz internally; re-clamp before the encoder.
    if needs_loudnorm:
        parts.append("aresample=48000")
    return ",".join(parts)
