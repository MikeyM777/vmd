"""Runs real ffmpeg. Skipped automatically if ffmpeg is not on PATH."""

import datetime
import shutil
import subprocess
import time
from pathlib import Path

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


@pytest.fixture
def camera_clip(tmp_path):
    """Six seconds of what the operator's camera actually sends.

    H.264 video and pcm_mulaw audio, which is the pairing that stopped recording
    dead for a whole day: MP4 has no tag for pcm_mulaw, ffmpeg refuses to write
    the header, and every segment is a file of zero bytes. That camera has sent
    that codec since the beginning - it was in a stream probe months ago - and
    no test in this suite had audio in it at all.

    A .nut container, because it will carry anything; keyframes every ten frames
    with `-g`, because `-c copy` can only cut a segment on a keyframe and
    libx264's default GOP is longer than this clip - without it the segment
    length is whatever the encoder felt like and a test that counts segments
    fails for a reason that has nothing to do with what it is testing.
    """
    path = tmp_path / "camera.nut"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=8000",
            "-t", "6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10",
            "-c:a", "pcm_mulaw", "-ar", "8000", "-ac", "1",
            str(path),
        ],
        check=True,
    )
    return path


def test_a_camera_with_audio_mp4_cannot_carry_still_records(tmp_path, camera_clip):
    """The deployment failure, in one recorder and six seconds.

    `-c copy` copied the audio too, and MP4 refused it: "Could not find tag for
    codec pcm_mulaw", "Could not write header (incorrect codec parameters ?)",
    exit 234, and a segment of zero bytes - created before it failed, which is
    why the laptop had 24 of them rather than none.
    """
    recorder = SegmentRecorder(
        stream="thermal",
        source_url=str(camera_clip),
        output_dir=tmp_path / "out",
        segment_seconds=2,
    )
    written = record_until_finished(recorder)

    assert len(written) >= 2, [path.name for path in written]
    empty = [path.name for path in written if path.stat().st_size == 0]
    assert not empty, (
        f"{len(empty)} empty segment(s) - ffmpeg exited before writing a header. "
        "It said:\n"
        + (recorder.log_path.read_text(errors="replace") if recorder.log_path.exists() else "")
    )
    assert recorder.exit_code == 0, recorder.log_path.read_text(errors="replace")


def test_the_recorded_video_survives_dropping_the_audio(tmp_path, camera_clip):
    """And what is written is still the camera's picture, not a re-encode."""
    recorder = SegmentRecorder(
        stream="thermal",
        source_url=str(camera_clip),
        output_dir=tmp_path / "out",
        segment_seconds=2,
    )
    written = record_until_finished(recorder)

    result = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-show_entries", "stream=codec_type,codec_name",
            "-of", "csv=p=0", str(written[0]),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    streams = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert streams == ["h264,video"] or streams == ["video,h264"], streams


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


# ------------------------------------------ the whole of it, as it is deployed
#
# Everything above tests one ffmpeg. What the machine on the hill actually runs
# is the console starting `python -m vmd.record_main`, which pulls from the
# go2rtc the console started, writes segments, indexes them, and leaves behind a
# catalogue the Playback tab reads. Each of those has been tested on its own,
# and the storm that shipped - the console starting a recorder every two seconds
# and none of them ever recording anything - was invisible to all of them,
# because none of them ran the real chain.
#
# So this runs the real chain: a synthetic camera through the real go2rtc, the
# real recorder as a real child process, and then the question the operator
# actually asks, which is whether there is footage on that day and where.
#
# Every wait is bounded and every failure says what the child said.


def test_the_console_s_recorder_writes_footage_playback_can_find(tmp_path):
    """Recording is what this system is for; this is the acceptance test for it."""
    import json
    import socket

    from vmd.desktop.services import ConsoleServices, RecorderProcess
    from vmd.record_main import process_image
    from vmd.desktop.timeline import coverage_bars, day_bounds
    from vmd.settings import (
        CameraSettings,
        Settings,
        StorageSettings,
        StreamSettings,
        save_settings,
    )
    from vmd.storage.index import SegmentIndex
    from vmd.streaming.go2rtc import Go2rtcService, find_binary

    binary = find_binary()
    if binary is None:
        pytest.skip("needs the go2rtc binary")

    def free_port() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    api_port, rtsp_port = free_port(), free_port()
    # With audio, and with the codec the operator's camera actually sends.
    # MP4 cannot carry pcm_mulaw: ffmpeg refuses to write the header, exits
    # before the first frame, and is restarted every five seconds for ever -
    # which is what that camera did for a whole day, leaving 24 files of zero
    # bytes and a console that said "recording". No test in this suite modelled
    # a camera with audio, so nothing caught it.
    camera = (
        "exec:ffmpeg -hide_banner -re -f lavfi -i testsrc=size=320x180:rate=15 "
        "-f lavfi -i sine=frequency=800:sample_rate=8000 "
        "-c:v libx264 -preset ultrafast -tune zerolatency -g 15 "
        "-c:a pcm_mulaw -ar 8000 -ac 1 -f rtsp {output}"
    )
    streaming = Go2rtcService(
        Settings(
            camera=CameraSettings(
                streams=[
                    StreamSettings.model_construct(
                        name="thermal", url=camera, enabled=True
                    )
                ]
            )
        ),
        config_path=tmp_path / "go2rtc.json",
        binary=binary,
        endpoint_path=tmp_path / "streaming.json",
        pid_path=tmp_path / "go2rtc.pid",
        api_port=api_port,
        rtsp_port=rtsp_port,
    )

    root = tmp_path / "recordings"
    settings = Settings(
        camera=CameraSettings(
            streams=[
                StreamSettings(
                    name="thermal",
                    url=f"rtsp://127.0.0.1:{rtsp_port}/thermal",
                    enabled=True,
                )
            ]
        ),
        storage=StorageSettings(root=root, segment_seconds=4),
    )
    settings_path = tmp_path / "settings.json"
    save_settings(settings, settings_path)

    said: list[str] = []
    recorder = RecorderProcess(settings_path)
    recorder._log_line = said.append
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=recorder,
    )

    started = time.monotonic()
    written: list = []
    try:
        services.start()
        assert streaming.running, "the streaming server did not start"
        # The claim names the recorder itself, not the launcher that started it.
        # That is the whole of the respawn storm: the console used to write its
        # Popen.pid here, which under uv's trampoline is the launcher's number,
        # and the recorder stood down to it and exited.
        # The console's own heartbeat, driven by hand. Bounded, so a recorder
        # that never records fails this in a couple of minutes rather than
        # hanging the suite. The disk is read on its own slower cadence - see
        # POLL_SECONDS in vmd\desktop\disk.py - so waiting for the console to
        # say "recording" is waiting for the folder to be read at least twice,
        # which is the whole point: the answer comes from the folder.
        deadline = started + 150.0
        state = services.state()
        while time.monotonic() < deadline:
            written = sorted((root / "thermal").glob("*.mp4"))
            services.tick()
            state = services.state()
            if (
                len(written) >= 3
                and (root / "segments.db").exists()
                and state["recording"]
            ):
                break
            time.sleep(1.0)

        recorded_pid = (tmp_path / "recorder.pid").read_text(encoding="utf-8").strip()
        assert recorded_pid.isdigit(), "the recorder never claimed the folder"
        claim = json.loads((tmp_path / "recorder.pid.json").read_text(encoding="utf-8"))
        assert claim["pid"] == int(recorded_pid), (
            "the claim and the recorder that wrote it must agree, or the next "
            "console adopts a process that is not the recorder"
        )
        assert process_image(int(recorded_pid)) is not None, (
            "the claim names a process that is not running, which is what the "
            "console wrote when it claimed the file on the recorder's behalf"
        )

        assert len(written) >= 3, (
            f"{len(written)} segment(s) in {time.monotonic() - started:.0f}s. "
            "The recorder said:\n  " + "\n  ".join(said[-25:])
        )
        # And the console must be saying so while it happens: this is what the
        # recording indicator is driven from, and it has to mean footage on the
        # disk rather than a process that was alive when it was asked.
        assert state["recording"] is True, state["recording_state"]
        assert state["recording_state"]["reason"] == "recording"
    finally:
        services.stop()
        streaming.stop()

    # Stopped, so the last segment is closed and indexed. Now ask what the
    # Playback tab asks: what is there, on this day, for this stream.
    empty = [path.name for path in written if path.stat().st_size == 0]
    assert not empty, (
        f"{len(empty)} of {len(written)} segments are zero bytes: an ffmpeg that "
        "died before it wrote a header, restarted every five seconds. The "
        "recorder said:\n  " + "\n  ".join(said[-25:])
    )

    index = SegmentIndex(root / "segments.db")
    try:
        segments = index.all("thermal")
    finally:
        index.close()

    assert len(segments) >= 2, (
        f"{len(written)} files on disk and {len(segments)} in the index: footage "
        "that is not indexed is invisible to Playback, to the storage budget and "
        "to retention"
    )
    for segment in segments:
        assert Path(segment.path).exists(), f"the index names a file that is not there: {segment.path}"
        assert segment.size_bytes > 0
        assert segment.end > segment.start, "a segment that covers no time"

    day = datetime.datetime.fromtimestamp(segments[0].start)
    day_start, day_end = day_bounds(day.year, day.month, day.day)
    on_that_day = [s for s in segments if s.end > day_start and s.start < day_end]
    bars = coverage_bars(on_that_day, day_start, day_end)

    assert bars, "the Playback timeline would draw an empty day over real footage"
    assert all(0.0 <= left <= 1.0 and width > 0 for left, width in bars)
    covered = sum(min(s.end, day_end) - max(s.start, day_start) for s in on_that_day)
    assert covered > 4.0, f"only {covered:.1f}s of coverage from {len(segments)} segments"
