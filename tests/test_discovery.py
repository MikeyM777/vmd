import os

from vmd.storage.discovery import find_closed_segments, parse_segment_start


def touch(path, mtime):
    path.write_bytes(b"x" * 10)
    os.utime(path, (mtime, mtime))


def test_no_files_returns_nothing(tmp_path):
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_single_file_is_never_closed(tmp_path):
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 100.0)
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_older_file_is_closed_when_a_newer_one_exists(tmp_path):
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    closed = find_closed_segments(tmp_path, now=1000.0)
    assert [p.name for p in closed] == ["2026-08-07_10-00-00.mp4"]


def test_recently_written_file_is_not_closed_yet(tmp_path):
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 998.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 999.0)
    assert find_closed_segments(tmp_path, now=1000.0, settle_seconds=5.0) == []


def test_empty_files_are_ignored(tmp_path):
    (tmp_path / "2026-08-07_10-00-00.mp4").write_bytes(b"")
    os.utime(tmp_path / "2026-08-07_10-00-00.mp4", (100.0, 100.0))
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_non_mp4_files_are_ignored(tmp_path):
    touch(tmp_path / "notes.txt", 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    assert find_closed_segments(tmp_path, now=1000.0) == []


def test_already_seen_paths_are_skipped(tmp_path):
    first = tmp_path / "2026-08-07_10-00-00.mp4"
    touch(first, 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    closed = find_closed_segments(tmp_path, now=1000.0, seen={str(first)})
    assert closed == []


def test_tied_newest_mtime_still_yields_the_finished_file(tmp_path):
    # Two files share the newest mtime. Only the one ffmpeg is still writing
    # should be withheld; the other is finished and must be indexed.
    touch(tmp_path / "2026-08-07_10-00-00.mp4", 100.0)
    touch(tmp_path / "2026-08-07_10-05-00.mp4", 400.0)
    touch(tmp_path / "2026-08-07_10-10-00.mp4", 400.0)
    closed = find_closed_segments(tmp_path, now=1000.0)
    assert len(closed) == 2
    assert tmp_path / "2026-08-07_10-00-00.mp4" in closed


def test_parse_segment_start():
    assert parse_segment_start("2026-08-07_14-35-00.mp4") is not None


def test_parse_segment_start_returns_none_for_junk():
    assert parse_segment_start("recording.mp4") is None


def test_parse_segment_start_reads_a_name_carrying_a_run_number():
    """Segment names carry which ffmpeg run wrote them, so that two runs can
    never be handed the same filename by a clock that moved between them. The
    time in the name is unchanged, and archives written before this still read."""
    plain = parse_segment_start("2026-08-07_14-35-00.mp4")
    assert parse_segment_start("2026-08-07_14-35-00_7.mp4") == plain
    assert parse_segment_start("2026-08-07_14-35-00_142.mp4") == plain


def test_a_run_number_is_not_confused_with_the_time():
    assert parse_segment_start("2026-08-07_14-35-00_notanumber.mp4") is None


def test_parse_segment_start_is_utc_epoch(tmp_path):
    # Segment filenames are written by ffmpeg under TZ=UTC, so they must be read back
    # as UTC. Reading them as local time would shift every timestamp by the UTC offset
    # and would make the autumn daylight-saving hour ambiguous.
    import datetime

    parsed = parse_segment_start("2026-08-07_14-35-00.mp4")
    expected = datetime.datetime(
        2026, 8, 7, 14, 35, 0, tzinfo=datetime.timezone.utc
    ).timestamp()
    assert parsed == expected
