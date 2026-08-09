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
