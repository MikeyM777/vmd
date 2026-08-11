"""A clip, written by the real ffmpeg out of really recorded files.

A clip that cannot be opened again is the worst failure this feature has. The
operator saves the one minute that mattered, deletes nothing, walks away, and
finds out months later - if he ever finds out - that the file is a header and
no frames. Nothing short of a real run proves it did not happen: every part of
this is exactly the arithmetic and the arrangement a unit test can check, and
none of that says whether the bytes ffmpeg wrote can be decoded.

So the footage here is recorded by the same `SegmentRecorder` the console runs,
into the same five-minute-style segments with `-reset_timestamps 1`, indexed by
the same `SegmentIndex`, planned by the same `clip_plan`, and written by the
same `export_clip`. What is then asserted is what a player would find: the
streams, the codec, and the number of frames actually decodable from end to
end.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from vmd.desktop.export import export_clip, suggested_name, unique_path
from vmd.desktop.timeline import clip_plan
from vmd.storage.discovery import (
    find_closed_segments,
    next_segment_start,
    parse_segment_start,
    segment_starts,
)
from vmd.storage.index import SegmentIndex
from vmd.storage.recorder import SegmentRecorder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed"),
]

SEGMENT_SECONDS = 2
FRAME_RATE = 10


@pytest.fixture
def source_clip(tmp_path: Path) -> Path:
    """Twelve seconds of test pattern, with keyframes often enough to cut on.

    `-g 10` is a keyframe a second. `-c copy` can only cut on a keyframe, so
    without it the segment lengths would be whatever libx264 felt like and this
    test would be measuring the encoder rather than the export.
    """
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate={FRAME_RATE}",
            "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-g", str(FRAME_RATE), str(path),
        ],
        check=True,
    )
    return path


def recorded(tmp_path: Path, source: Path) -> tuple[SegmentIndex, list]:
    """Real segments on disk, catalogued the way the recorder catalogues them."""
    out = tmp_path / "recordings" / "thermal"
    recorder = SegmentRecorder(
        stream="thermal",
        source_url=str(source),
        output_dir=out,
        segment_seconds=SEGMENT_SECONDS,
    )
    recorder.start()
    # The source is a file read with -re, so it takes its own twelve seconds and
    # then ffmpeg exits on its own. Waited for rather than slept through, and
    # bounded, because a test may fail but may not hang.
    deadline = time.time() + 60
    while time.time() < deadline and recorder.running:
        time.sleep(0.5)
    recorder.stop()

    index = SegmentIndex(tmp_path / "segments.db")
    closed = sorted(
        find_closed_segments(out, now=time.time() + 60, settle_seconds=0.0),
        key=lambda p: p.name,
    )
    # Where each recording really stops is where the next one starts, which is
    # exactly how the recording service catalogues them: the nominal length is
    # not what ffmpeg wrote, and believing it produces overlapping rows and a
    # plan that asks for the same second twice.
    starts = segment_starts(out)
    for path in closed:
        start = parse_segment_start(path.name)
        assert start is not None, path
        after = next_segment_start(starts, start)
        index.add(
            stream="thermal",
            path=str(path),
            start=start,
            end=after if after is not None else start + SEGMENT_SECONDS,
            size_bytes=path.stat().st_size,
        )
    return index, index.all("thermal")


def probe(path: Path) -> dict:
    finished = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-show_streams", "-show_format", "-of", "json", str(path),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(finished.stdout.decode("utf-8", "replace"))


def decodable_frames(path: Path) -> int:
    """How many frames really come out of it, decoded from end to end.

    Not the header's opinion. A file whose container says ninety frames and
    whose bitstream falls over at frame four is exactly the failure this whole
    test exists to catch, and only decoding the lot finds it.
    """
    finished = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(path), "-f", "null", "-",
        ],
        capture_output=True,
    )
    assert finished.returncode == 0, finished.stderr.decode("utf-8", "replace")
    counted = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames", "-of", "json", str(path),
        ],
        capture_output=True,
        check=True,
    )
    streams = json.loads(counted.stdout.decode("utf-8", "replace"))["streams"]
    return int(streams[0]["nb_read_frames"])


def test_a_clip_across_several_recordings_is_a_file_that_really_plays(
    tmp_path: Path, source_clip: Path
) -> None:
    """The whole feature, end to end, with nothing pretended.

    A range that starts inside one recording, crosses at least one boundary and
    ends inside another - which is the ordinary case, because the recorder
    writes five-minute files and nothing an operator wants to keep is aligned
    to them.
    """
    index, segments = recorded(tmp_path, source_clip)
    try:
        assert len(segments) >= 3, f"not enough was recorded to cut across: {segments}"
        first, last = segments[0], segments[2]
        start = first.start + 0.5
        end = last.start + 1.0

        plan = clip_plan(segments, start, end)
        assert len(plan.parts) == 3, plan
        assert plan.whole, plan.gaps
        assert plan.covered_seconds == pytest.approx(plan.requested_seconds)

        folder = tmp_path / "he chose this one"
        destination = unique_path(folder, suggested_name("thermal", start, end))
        outcome = export_clip(plan, destination=destination, stream="thermal")

        assert outcome.ok, outcome.message
        assert outcome.path is not None and outcome.path.exists()
        assert outcome.path.stat().st_size > 0
        assert str(folder) in outcome.message

        details = probe(outcome.path)
        video = [s for s in details["streams"] if s["codec_type"] == "video"]
        assert len(video) == 1, details["streams"]
        # Copied, not re-encoded: what he keeps is what the camera sent.
        assert video[0]["codec_name"] == "h264"

        # At least everything that was asked for is in it. It may hold a little
        # more: `-c copy` can only cut on a keyframe, so the clip begins at the
        # keyframe at or before each mark - which is a second in his favour and
        # the price of not re-encoding an hour of footage on this laptop.
        held = float(details["format"]["duration"])
        assert held >= plan.covered_seconds - 0.5, (
            f"{held:.2f}s came out of a clip that should hold "
            f"{plan.covered_seconds:.2f}s"
        )
        assert held <= plan.covered_seconds + 1.2 * len(plan.parts) + 0.5, held

        frames = decodable_frames(outcome.path)
        # Every frame of it decodes, not only the ones before the first join.
        assert frames >= plan.covered_seconds * FRAME_RATE * 0.9, (
            f"{frames} frames came out of a clip that should hold about "
            f"{plan.covered_seconds * FRAME_RATE:.0f}"
        )
    finally:
        index.close()


def test_a_clip_inside_one_recording_plays_too(tmp_path: Path, source_clip: Path) -> None:
    """The other half of the ordinary case, through the same one code path."""
    index, segments = recorded(tmp_path, source_clip)
    try:
        # The longest recording there is, and a range well inside it: ffmpeg's
        # first segment is whatever is left of the first second, which is not a
        # file anything can be cut out of.
        longest = max(segments, key=lambda s: s.duration)
        assert longest.duration > 1.0, segments
        start = longest.start + 0.2
        end = longest.end - 0.2
        plan = clip_plan(segments, start, end)
        assert len(plan.parts) == 1, plan

        destination = tmp_path / "keep" / "one.mp4"
        outcome = export_clip(plan, destination=destination, stream="thermal")
        assert outcome.ok, outcome.message
        assert decodable_frames(outcome.path) > 0
    finally:
        index.close()


def test_a_clip_across_a_real_gap_is_shorter_and_says_so(
    tmp_path: Path, source_clip: Path
) -> None:
    """Footage on both sides of a hole, and nothing in it.

    The clip is what exists, the sentence says how much of the range did not,
    and the file still plays - a clip he believes is the whole range when it is
    half of it is worse than no clip.
    """
    index, segments = recorded(tmp_path, source_clip)
    try:
        assert len(segments) >= 5
        # Take the middle recordings out of the catalogue: from Playback's point
        # of view those minutes were never recorded, which is what retention
        # leaves behind and what a recorder that was down leaves behind.
        with_a_hole = [segments[0], segments[4]]
        start = segments[0].start + 0.5
        end = segments[4].start + 1.0

        plan = clip_plan(with_a_hole, start, end)
        assert not plan.whole
        assert plan.missing_seconds >= 3.0, plan

        destination = tmp_path / "keep" / "over a hole.mp4"
        outcome = export_clip(plan, destination=destination, stream="thermal")
        assert outcome.ok, outcome.message
        assert "shorter" in outcome.message.lower(), outcome.message
        assert decodable_frames(outcome.path) > 0

        details = probe(outcome.path)
        # Shorter than the range asked for, by about the size of the hole - and
        # the sentence above is the only thing that tells him so.
        assert float(details["format"]["duration"]) < plan.requested_seconds - 1.5
    finally:
        index.close()


def test_a_range_with_nothing_in_it_writes_no_file_at_all(
    tmp_path: Path, source_clip: Path
) -> None:
    index, segments = recorded(tmp_path, source_clip)
    try:
        folder = tmp_path / "keep"
        folder.mkdir()
        plan = clip_plan(segments, segments[0].start + 86400, segments[0].start + 86500)
        outcome = export_clip(
            plan, destination=folder / "nothing.mp4", stream="thermal"
        )
        assert not outcome.ok
        assert list(folder.iterdir()) == []
    finally:
        index.close()


def test_a_folder_that_is_not_there_is_made_rather_than_refused(
    tmp_path: Path, source_clip: Path
) -> None:
    """He types a new folder name into the chooser. That is not an error."""
    index, segments = recorded(tmp_path, source_clip)
    try:
        destination = tmp_path / "new" / "deeper" / "clip.mp4"
        plan = clip_plan(segments, segments[0].start, segments[0].start + 1.0)
        outcome = export_clip(plan, destination=destination, stream="thermal")
        assert outcome.ok, outcome.message
        assert destination.exists()
    finally:
        index.close()
