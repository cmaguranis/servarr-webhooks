"""Tests for transcode job priority computation."""

import pytest
from unittest.mock import patch

from src.transcode.encode import video_transcode_needed
from src.transcode.queue import _compute_priority


# ---------------------------------------------------------------------------
# video_transcode_needed
# ---------------------------------------------------------------------------

class TestVideoTranscodeNeeded:
    @pytest.mark.parametrize("codec", ["hevc", "HEVC", "x265", "X265", "h265", "H265", "h.265", "H.265"])
    def test_hevc_under_8000_kbps_not_needed(self, codec):
        assert video_transcode_needed(codec, 8000) is False

    def test_hevc_exactly_8000_kbps_not_needed(self):
        assert video_transcode_needed("hevc", 8000) is False

    def test_hevc_over_8000_kbps_needed(self):
        assert video_transcode_needed("hevc", 8001) is True

    def test_hevc_zero_bitrate_needed(self):
        # Unknown bitrate → assume transcode needed
        assert video_transcode_needed("hevc", 0) is True

    def test_hevc_none_bitrate_needed(self):
        assert video_transcode_needed("hevc", None) is True

    def test_h264_always_needed(self):
        assert video_transcode_needed("h264", 3000) is True

    def test_avc_always_needed(self):
        assert video_transcode_needed("AVC", 3000) is True

    def test_none_codec_needed(self):
        assert video_transcode_needed(None, 3000) is True

    def test_empty_codec_needed(self):
        assert video_transcode_needed("", 3000) is True

    def test_hevc_with_spaces_normalized(self):
        # "h evc" → "hevc" after space removal
        assert video_transcode_needed("h evc", 5000) is False


# ---------------------------------------------------------------------------
# _compute_priority
# ---------------------------------------------------------------------------

class TestComputePriority:
    def test_audio_only_gets_priority_2(self):
        meta = {"codec": "hevc", "bitrate_kbps": 5000}
        assert _compute_priority(meta) == 2

    def test_video_encode_needed_gets_priority_1(self):
        meta = {"codec": "h264", "bitrate_kbps": 5000}
        assert _compute_priority(meta) == 1

    def test_hevc_high_bitrate_gets_priority_1(self):
        meta = {"codec": "hevc", "bitrate_kbps": 12000}
        assert _compute_priority(meta) == 1

    def test_missing_codec_gets_priority_1(self):
        meta = {"bitrate_kbps": 3000}
        assert _compute_priority(meta) == 1

    def test_missing_bitrate_gets_priority_1(self):
        meta = {"codec": "hevc"}
        assert _compute_priority(meta) == 1

    def test_empty_meta_gets_priority_1(self):
        assert _compute_priority({}) == 1


# ---------------------------------------------------------------------------
# Priority propagated through transcode enqueue_job
# ---------------------------------------------------------------------------

class TestTranscodeEnqueuePriority:
    def test_hevc_low_bitrate_enqueued_with_priority_2(self, tmp_path):
        from src.queue import JobQueue
        q = JobQueue(db_path=str(tmp_path / "t.db"), table="transcode_jobs")
        q.init_db()
        with patch("src.transcode.queue._q", q):
            from src.transcode import queue as tq
            tq.enqueue_job("/a.mkv", {"codec": "hevc", "bitrate_kbps": 5000})
        assert q.list_jobs()[0]["priority"] == 2

    def test_h264_enqueued_with_priority_1(self, tmp_path):
        from src.queue import JobQueue
        q = JobQueue(db_path=str(tmp_path / "t.db"), table="transcode_jobs")
        q.init_db()
        with patch("src.transcode.queue._q", q):
            from src.transcode import queue as tq
            tq.enqueue_job("/a.mkv", {"codec": "h264", "bitrate_kbps": 5000})
        assert q.list_jobs()[0]["priority"] == 1
