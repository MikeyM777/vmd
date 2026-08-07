"""Runs real ffmpeg. Skipped automatically if ffmpeg is not on PATH."""

import shutil
import subprocess
import time

import pytest

from vmd.storage.discovery import find_closed_segments, parse_segment_start
from vmd.storage.recorder import SegmentRecorder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
]


@pytest.fixture
def source_clip(tmp_path):
    """12 seconds of H.264 test pattern, so segmenting has something to copy."""
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
            "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-g", "10", str(path),
        ],
        check=True,
    )
    return path


def record_until_finished(recorder, timeout=60.0):
    """Run the recorder to completion, then fail loudly with ffmpeg's own words.

    Without this, a broken ffmpeg command surfaces as an empty directory and an
    assertion about list length, sending the reader hunting through a temporary
    directory for the log. The recorder already captures exit code and stderr;
    this puts them in the failure message.
    """
    recorder.start()
    deadline = time.time() + timeout
    while recorder.running and time.time() < deadline:
        time.sleep(0.5)
    timed_out = recorder.running
    recorder.stop()

    written = sorted(recorder.output_dir.glob("*.mp4"))
    if timed_out or not written:
        log = ""
        if recorder.log_path.exists():
            log = recorder.log_path.read_text(errors="replace")[-2000:]
        pytest.fail(
            f"recorder produced {len(written)} segment(s)"
            f"{' and timed out' if timed_out else ''}.\n"
            f"exit code: {recorder.exit_code}\n"
            f"command: {' '.join(recorder.build_command())}\n"
            f"ffmpeg stderr:\n{log or '(log file empty or missing)'}"
        )
    return written


def test_produces_multiple_playable_segments(tmp_path, source_clip):
    recorder = SegmentRecorder(
        stream="test",
        source_url=str(source_clip),
        output_dir=tmp_path / "out",
        segment_seconds=4,
    )
    written = record_until_finished(recorder)

    assert len(written) >= 2, f"expected several segments, got {[p.name for p in written]}"
    for path in written:
        assert path.stat().st_size > 0
        assert parse_segment_start(path.name) is not None


def test_segments_are_readable_by_ffprobe(tmp_path, source_clip):
    recorder = SegmentRecorder(
        stream="test",
        source_url=str(source_clip),
        output_dir=tmp_path / "out",
        segment_seconds=4,
    )
    first = record_until_finished(recorder)[0]

    result = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-show_entries", "format=duration", "-of", "csv=p=0", str(first),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert float(result.stdout.strip()) > 0


def test_discovery_finds_the_completed_segments(tmp_path, source_clip):
    recorder = SegmentRecorder(
        stream="test",
        source_url=str(source_clip),
        output_dir=tmp_path / "out",
        segment_seconds=4,
    )
    record_until_finished(recorder)

    closed = find_closed_segments(recorder.output_dir, now=time.time() + 10)
    assert len(closed) >= 1
