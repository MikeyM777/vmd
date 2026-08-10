"""Events are reclaimed with the footage they point at, and never before it."""

from vmd.detect.events import EventStore
from vmd.storage.index import SegmentIndex
from vmd.storage.retention import apply_plan, plan_retention

GB = 1024**3


def make_file(tmp_path, name, size=1024):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def setup(tmp_path, streams=("thermal",), count=4):
    index = SegmentIndex(tmp_path / "segments.db")
    events = EventStore(tmp_path / "events.db")
    for stream in streams:
        for i in range(count):
            path = make_file(tmp_path, f"{stream}-{i}.mp4")
            index.add(stream, str(path), start=i * 300.0, end=i * 300.0 + 300.0, size_bytes=GB)
    return index, events


def over_budget(index, budget_gb):
    return plan_retention(
        index.all(),
        now=10_000.0,
        budget_bytes=budget_gb * GB,
        budget_enabled=True,
        retention_days=None,
        warn_at_fraction=0.9,
        bytes_per_second=1000.0,
    )


def test_events_are_deleted_with_the_footage_they_point_at(tmp_path):
    """The list must never point at a file that has been reclaimed."""
    index, events = setup(tmp_path)
    try:
        events.add("thermal", 100.0, 150.0, (0, 0, 4, 4), 20.0)  # inside segment 0
        events.add("thermal", 400.0, 450.0, (0, 0, 4, 4), 20.0)  # inside segment 1
        events.add("thermal", 1000.0, 1050.0, (0, 0, 4, 4), 20.0)  # inside segment 3

        removed = apply_plan(over_budget(index, 2), index, events=events)

        assert len(removed) == 2  # the two oldest segments, covering 0-600s
        assert [e.started for e in events.recent()] == [1000.0]
    finally:
        index.close()
        events.close()


def test_an_event_in_surviving_footage_is_kept(tmp_path):
    index, events = setup(tmp_path)
    try:
        events.add("thermal", 700.0, 750.0, (0, 0, 4, 4), 20.0)
        apply_plan(over_budget(index, 2), index, events=events)
        assert len(events.recent()) == 1
    finally:
        index.close()
        events.close()


def test_the_other_stream_s_events_are_untouched(tmp_path):
    """Retention deletes one stream's oldest footage; the other stream's
    recording of the same minutes may still be on disk, and its events still
    point at it."""
    index = SegmentIndex(tmp_path / "segments.db")
    events = EventStore(tmp_path / "events.db")
    try:
        old = make_file(tmp_path, "thermal-0.mp4")
        index.add("thermal", str(old), start=0.0, end=300.0, size_bytes=4 * GB)
        keep = make_file(tmp_path, "visible-0.mp4")
        index.add("visible", str(keep), start=0.0, end=300.0, size_bytes=1)

        events.add("thermal", 100.0, 150.0, (0, 0, 4, 4), 20.0)
        events.add("visible", 100.0, 150.0, (0, 0, 4, 4), 20.0)

        removed = apply_plan(over_budget(index, 1), index, events=events)

        assert [s.stream for s in removed] == ["thermal"]
        assert [e.stream for e in events.recent()] == ["visible"]
    finally:
        index.close()
        events.close()


def test_a_segment_that_could_not_be_deleted_keeps_its_events(tmp_path):
    """The footage is still there, so the event still points at something."""
    index, events = setup(tmp_path)
    try:
        events.add("thermal", 100.0, 150.0, (0, 0, 4, 4), 20.0)

        def refuse(_path):
            raise PermissionError("file is in use")

        removed = apply_plan(over_budget(index, 2), index, unlink=refuse, events=events)

        assert removed == []
        assert len(events.recent()) == 1
    finally:
        index.close()
        events.close()


def test_retention_still_works_without_an_event_store(tmp_path):
    """The recorder can run on a machine where detection was never turned on."""
    index, events = setup(tmp_path)
    events.close()
    try:
        assert len(apply_plan(over_budget(index, 2), index)) == 2
    finally:
        index.close()


def test_a_broken_event_store_does_not_stop_the_disk_being_freed(tmp_path):
    """Freeing the disk is the job. If it needed a working events.db to happen,
    a locked events.db would fill the disk and stop recording."""
    index, events = setup(tmp_path)

    class BrokenStore:
        def delete_before(self, cutoff, stream=None):
            raise RuntimeError("database is locked")

    try:
        removed = apply_plan(over_budget(index, 2), index, events=BrokenStore())
        assert len(removed) == 2
        assert len(index.all()) == 2
    finally:
        index.close()
        events.close()


def test_an_event_straddling_the_edge_of_the_deleted_footage_survives(tmp_path):
    """Half of what it points at is still on disk."""
    index, events = setup(tmp_path)
    try:
        events.add("thermal", 550.0, 650.0, (0, 0, 4, 4), 20.0)  # spans the 600s edge
        apply_plan(over_budget(index, 2), index, events=events)
        assert len(events.recent()) == 1
    finally:
        index.close()
        events.close()
