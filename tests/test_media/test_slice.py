"""Unit tests for src/test_media/slice.py."""

import pytest
from unittest.mock import patch, MagicMock, call

from src.test_media.slice import (
    get_duration,
    get_media_signature,
    build_output_path,
    slice_file,
)

_STREAM_INFO = {
    "format": {"duration": "7200.0"},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "eac3", "channels": 8},
    ],
}


# ---------------------------------------------------------------------------
# get_duration
# ---------------------------------------------------------------------------

class TestGetDuration:
    def test_returns_float_from_format(self):
        with patch("src.test_media.slice.get_stream_info", return_value=_STREAM_INFO):
            assert get_duration("/a.mkv") == 7200.0

    def test_raises_on_missing_duration(self):
        with patch("src.test_media.slice.get_stream_info", return_value={"format": {}}):
            with pytest.raises(RuntimeError, match="no duration"):
                get_duration("/a.mkv")

    def test_raises_on_empty_format(self):
        with patch("src.test_media.slice.get_stream_info", return_value={}):
            with pytest.raises(RuntimeError):
                get_duration("/a.mkv")


# ---------------------------------------------------------------------------
# get_media_signature
# ---------------------------------------------------------------------------

class TestGetMediaSignature:
    def test_returns_tuple_of_video_audio_channels(self):
        with patch("src.test_media.slice.get_stream_info", return_value=_STREAM_INFO):
            sig = get_media_signature("/a.mkv")
        assert sig == ("hevc", "eac3", 8)

    def test_falls_back_to_unknown_when_no_video(self):
        info = {
            "format": {"duration": "100"},
            "streams": [
                {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            ],
        }
        with patch("src.test_media.slice.get_stream_info", return_value=info):
            sig = get_media_signature("/a.mkv")
        assert sig == ("unknown", "aac", 2)

    def test_falls_back_to_unknown_when_no_audio(self):
        info = {
            "format": {"duration": "100"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
            ],
        }
        with patch("src.test_media.slice.get_stream_info", return_value=info):
            sig = get_media_signature("/a.mkv")
        assert sig == ("h264", "unknown", 0)

    def test_empty_streams(self):
        with patch("src.test_media.slice.get_stream_info", return_value={"streams": []}):
            sig = get_media_signature("/a.mkv")
        assert sig == ("unknown", "unknown", 0)


# ---------------------------------------------------------------------------
# build_output_path
# ---------------------------------------------------------------------------

class TestBuildOutputPath:
    def test_includes_parent_dir_and_start_sec(self):
        result = build_output_path(
            source_path="/data/media_cache/Movies/Interstellar.mkv",
            start_sec=3724,
            output_dir="/data/media_test",
        )
        assert result == "/data/media_test/Movies__Interstellar_3724s.mkv"

    def test_preserves_source_extension(self):
        result = build_output_path("/cache/TV/show.mp4", 10, "/out")
        assert result.endswith(".mp4")

    def test_zero_start_sec(self):
        result = build_output_path("/cache/Movies/film.mkv", 0, "/out")
        assert result == "/out/Movies__film_0s.mkv"


# ---------------------------------------------------------------------------
# slice_file
# ---------------------------------------------------------------------------

class TestSliceFile:
    def _patch_duration(self, duration):
        return patch("src.test_media.slice.get_duration", return_value=duration)

    def test_dry_run_logs_and_does_not_run_ffmpeg(self):
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run") as mock_run, \
             patch("src.test_media.slice.os.makedirs"):
            slice_file("/src.mkv", "/out.mkv", start_sec=10, dry_run=True)
        mock_run.assert_not_called()

    def test_raises_value_error_if_slice_exceeds_duration(self):
        with self._patch_duration(35.0):
            with pytest.raises(ValueError, match="out of bounds"):
                slice_file("/src.mkv", "/out.mkv", start_sec=10, duration_sec=30)

    def test_ffmpeg_called_with_correct_args(self):
        mock_result = MagicMock(returncode=0)
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run", return_value=mock_result) as mock_run, \
             patch("src.test_media.slice.os.makedirs"):
            slice_file("/src.mkv", "/out.mkv", start_sec=10, duration_sec=30)

        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "ffmpeg"
        assert "-ss" in cmd
        assert str(10) in cmd
        assert "-t" in cmd
        assert str(30) in cmd
        assert "-c" in cmd and "copy" in cmd
        assert "-map" in cmd and "0" in cmd
        assert "/src.mkv" in cmd
        assert "/out.mkv" in cmd

    def test_ss_appears_before_input(self):
        """Input-side seek: -ss must appear before -i."""
        mock_result = MagicMock(returncode=0)
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run", return_value=mock_result) as mock_run, \
             patch("src.test_media.slice.os.makedirs"):
            slice_file("/src.mkv", "/out.mkv", start_sec=10)

        cmd = mock_run.call_args.args[0]
        assert cmd.index("-ss") < cmd.index("-i")

    def test_raises_runtime_error_on_ffmpeg_failure(self):
        mock_result = MagicMock(returncode=1, stderr="error output")
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run", return_value=mock_result), \
             patch("src.test_media.slice.os.makedirs"), \
             patch("src.test_media.slice.os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="ffmpeg exited 1"):
                slice_file("/src.mkv", "/out.mkv", start_sec=10)

    def test_cleans_up_partial_output_on_failure(self):
        mock_result = MagicMock(returncode=1, stderr="")
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run", return_value=mock_result), \
             patch("src.test_media.slice.os.makedirs"), \
             patch("src.test_media.slice.os.path.exists", return_value=True) as mock_exists, \
             patch("src.test_media.slice.os.remove") as mock_remove:
            with pytest.raises(RuntimeError):
                slice_file("/src.mkv", "/out.mkv", start_sec=10)
        mock_remove.assert_called_once_with("/out.mkv")

    def test_no_cleanup_if_no_partial_file(self):
        mock_result = MagicMock(returncode=1, stderr="")
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run", return_value=mock_result), \
             patch("src.test_media.slice.os.makedirs"), \
             patch("src.test_media.slice.os.path.exists", return_value=False), \
             patch("src.test_media.slice.os.remove") as mock_remove:
            with pytest.raises(RuntimeError):
                slice_file("/src.mkv", "/out.mkv", start_sec=10)
        mock_remove.assert_not_called()

    def test_creates_output_directory(self):
        mock_result = MagicMock(returncode=0)
        with self._patch_duration(120.0), \
             patch("src.test_media.slice.subprocess.run", return_value=mock_result), \
             patch("src.test_media.slice.os.makedirs") as mock_makedirs:
            slice_file("/src.mkv", "/out/subdir/clip.mkv", start_sec=10)
        mock_makedirs.assert_called_once_with("/out/subdir", exist_ok=True)
