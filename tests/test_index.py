from vmd.storage.index import Segment, SegmentIndex


def build(tmp_path):
    return SegmentIndex(tmp_path / "segments.db")


def test_add_and_read_back(tmp_path):
    index = build(tmp_path)
    segment_id = index.add("thermal", "/rec/a.mp4", start=100.0, end=400.0, size_bytes=1000)
    segments = index.all()
    assert len(segments) == 1
    assert segments[0].id == segment_id
    assert segments[0].stream == "thermal"
    assert segments[0].path == "/rec/a.mp4"
    assert segments[0].start == 100.0
    assert segments[0].size_bytes == 1000
    index.close()


def test_all_is_ordered_by_start(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/c.mp4", 300.0, 600.0, 10)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.add("thermal", "/rec/b.mp4", 200.0, 500.0, 10)
    assert [s.path for s in index.all()] == ["/rec/a.mp4", "/rec/b.mp4", "/rec/c.mp4"]
    index.close()


def test_filter_by_stream(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/t.mp4", 100.0, 400.0, 10)
    index.add("visible", "/rec/v.mp4", 100.0, 400.0, 10)
    assert [s.stream for s in index.all(stream="visible")] == ["visible"]
    index.close()


def test_oldest(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/b.mp4", 200.0, 500.0, 10)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    assert index.oldest().path == "/rec/a.mp4"
    index.close()


def test_oldest_is_none_when_empty(tmp_path):
    index = build(tmp_path)
    assert index.oldest() is None
    index.close()


def test_total_bytes(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 1500)
    index.add("visible", "/rec/b.mp4", 100.0, 400.0, 2500)
    assert index.total_bytes() == 4000
    index.close()


def test_delete_removes_row(tmp_path):
    index = build(tmp_path)
    segment_id = index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.delete(segment_id)
    assert index.all() == []
    assert index.total_bytes() == 0
    index.close()


def test_adding_the_same_path_twice_is_ignored(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    assert len(index.all()) == 1
    index.close()


def test_a_path_offered_again_with_different_content_corrects_the_row(tmp_path):
    """A row that no longer describes its file is worse than no row at all.

    A clock set backwards makes ffmpeg reopen a name it has already used and
    truncate it. The old behaviour - INSERT OR IGNORE - kept the first row's
    start, end and size, so Playback offered a file whose contents were from a
    different hour, retention deleted it by the wrong timestamp, and the
    coverage bar drew hours that were no longer there.
    """
    index = build(tmp_path)
    first = index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    again = index.add("thermal", "/rec/a.mp4", 100.0, 250.0, 6000)
    segments = index.all()
    assert again == first, "it is still the same row"
    assert len(segments) == 1
    assert segments[0].size_bytes == 6000
    assert segments[0].end == 250.0
    index.close()


def test_gaps_between_segments(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 0.0, 300.0, 10)
    index.add("thermal", "/rec/b.mp4", 300.0, 600.0, 10)
    index.add("thermal", "/rec/c.mp4", 900.0, 1200.0, 10)  # 300s hole before this
    gaps = index.gaps("thermal", 0.0, 1200.0)
    assert gaps == [(600.0, 900.0)]
    index.close()


def test_gaps_include_edges(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 200.0, 400.0, 10)
    gaps = index.gaps("thermal", 0.0, 600.0)
    assert gaps == [(0.0, 200.0), (400.0, 600.0)]
    index.close()


def test_gaps_with_no_segments_is_the_whole_window(tmp_path):
    index = build(tmp_path)
    assert index.gaps("thermal", 0.0, 600.0) == [(0.0, 600.0)]
    index.close()


def test_index_survives_reopen(tmp_path):
    index = build(tmp_path)
    index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
    index.close()
    reopened = build(tmp_path)
    assert len(reopened.all()) == 1
    reopened.close()


def test_adding_a_duplicate_path_returns_that_path_s_own_id(tmp_path):
    """An ignored insert used to return the id of whatever was inserted last."""
    index = build(tmp_path)
    try:
        first = index.add("thermal", "a.mp4", 1000.0, 1010.0, 10)
        index.add("thermal", "b.mp4", 1010.0, 1020.0, 10)
        again = index.add("thermal", "a.mp4", 1000.0, 1010.0, 10)
        assert again == first
    finally:
        index.close()


# ------------------------------------------------- reading a window, not the lot
#
# Playback used to load every row a stream had, on every redraw, to draw one
# day. A month of five-minute segments on two streams is ~17,000 rows, and the
# console reads them on the thread that draws the window. These three questions
# are the ones the tab actually asks, answered in SQL against the index that is
# already there.


def test_a_window_holds_only_the_segments_that_touch_it(tmp_path):
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/before.mp4", 100.0, 200.0, 10)
        index.add("thermal", "/rec/inside.mp4", 300.0, 400.0, 10)
        index.add("thermal", "/rec/after.mp4", 900.0, 1000.0, 10)
        found = index.between("thermal", 250.0, 500.0)
        assert [s.path for s in found] == ["/rec/inside.mp4"]
    finally:
        index.close()


def test_a_segment_overlapping_either_edge_of_the_window_is_in_it(tmp_path):
    """A recording that started before midnight and ran into the day is part of
    that day, and dropping it would draw a gap where there is footage."""
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/straddles_start.mp4", 100.0, 300.0, 10)
        index.add("thermal", "/rec/straddles_end.mp4", 400.0, 600.0, 10)
        found = index.between("thermal", 200.0, 500.0)
        assert {s.path for s in found} == {
            "/rec/straddles_start.mp4",
            "/rec/straddles_end.mp4",
        }
    finally:
        index.close()


def test_a_window_is_ordered_by_start(tmp_path):
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/c.mp4", 300.0, 400.0, 10)
        index.add("thermal", "/rec/a.mp4", 100.0, 200.0, 10)
        index.add("thermal", "/rec/b.mp4", 200.0, 300.0, 10)
        assert [s.path for s in index.between("thermal", 0.0, 1000.0)] == [
            "/rec/a.mp4",
            "/rec/b.mp4",
            "/rec/c.mp4",
        ]
    finally:
        index.close()


def test_a_window_belongs_to_one_stream(tmp_path):
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/t.mp4", 100.0, 200.0, 10)
        index.add("visible", "/rec/v.mp4", 100.0, 200.0, 10)
        assert [s.path for s in index.between("thermal", 0.0, 1000.0)] == ["/rec/t.mp4"]
    finally:
        index.close()


def test_the_bounds_of_a_stream_are_its_first_start_and_its_last_end(tmp_path):
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/a.mp4", 100.0, 400.0, 10)
        index.add("thermal", "/rec/b.mp4", 800.0, 900.0, 10)
        index.add("visible", "/rec/v.mp4", 1.0, 5000.0, 10)
        assert index.bounds("thermal") == (100.0, 900.0)
    finally:
        index.close()


def test_the_last_end_is_the_latest_end_not_the_end_of_the_latest_row(tmp_path):
    """A recorder killed mid-segment writes a short file after a long one. The
    archive still reaches as far as the longest one did."""
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/long.mp4", 100.0, 900.0, 10)
        index.add("thermal", "/rec/short.mp4", 200.0, 300.0, 10)
        assert index.bounds("thermal") == (100.0, 900.0)
    finally:
        index.close()


def test_a_stream_with_nothing_has_no_bounds(tmp_path):
    index = build(tmp_path)
    try:
        assert index.bounds("thermal") is None
    finally:
        index.close()


def test_bounds_with_no_stream_named_covers_everything(tmp_path):
    index = build(tmp_path)
    try:
        index.add("thermal", "/rec/t.mp4", 100.0, 200.0, 10)
        index.add("visible", "/rec/v.mp4", 50.0, 900.0, 10)
        assert index.bounds() == (50.0, 900.0)
    finally:
        index.close()
