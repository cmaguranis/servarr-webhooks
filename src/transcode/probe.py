import json
import subprocess

_TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}

_VIDEO_FIELDS = (
    "codec_name", "profile", "level", "pix_fmt",
    "width", "height", "avg_frame_rate", "r_frame_rate",
    "field_order", "color_space", "color_transfer", "color_primaries",
    "bit_rate",
)
_AUDIO_FIELDS = ("index", "codec_name", "channels", "channel_layout", "sample_rate", "bit_rate")


def get_stream_info(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(res.stdout)


def extract_probe_summary(info: dict) -> dict:
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    return {
        "video": {k: v.get(k) for k in _VIDEO_FIELDS},
        "audio": [
            {**{k: s.get(k) for k in _AUDIO_FIELDS}, "language": (s.get("tags") or {}).get("language")}
            for s in a_streams
        ],
        "format": {k: fmt.get(k) for k in ("format_name", "duration", "bit_rate", "size")},
    }
