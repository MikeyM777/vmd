"""The event store: where a confirmed track goes, and how it comes back."""

import pytest

from vmd.detect.events import Event, EventStore


def build(tmp_path):
    return EventStore(tmp_path / "events.db")


def test_add_and_read_back(tmp_path):
    store = build(tmp_path)
    try:
        event_id = store.add(
            stream="thermal",
            started=1000.0,
            ended=1004.5,
            box=(10, 20, 30, 40),
            travelled_px=37.5,
            label="person",
            confidence=0.62,
            clip_path="clips/a.mp4",
        )
        events = store.recent()
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, Event)
        assert event.id == event_id
        assert event.stream == "thermal"
        assert event.started == 1000.0
        assert event.ended == 1004.5
        assert event.box == (10, 20, 30, 40)
        assert event.travelled_px == 37.5
        assert event.label == "person"
        assert event.confidence == 0.62
        assert event.clip_path == "clips/a.mp4"
    finally:
        store.close()


def test_an_unlabelled_event_is_stored_and_returned_intact(tmp_path):
    """The case the whole design turns on.

    At 700 m a person is about 13 pixels: the classifier cannot name it, and the
    operator still needs to know something moved. So an event with no label is a
    real event, and it must come back as an empty string - not as None, which
    would make every reader of this table handle a null it never asked for.
    """
    store = build(tmp_path)
    try:
        store.add(
            stream="thermal",
            started=1000.0,
            ended=1002.0,
            box=(5, 6, 7, 8),
            travelled_px=19.0,
        )
        event = store.recent()[0]
        assert event.label == ""
        assert event.label is not None
        assert event.confidence == 0.0
        assert event.clip_path == ""
        assert event.box == (5, 6, 7, 8)
    finally:
        store.close()


def test_an_explicit_none_label_is_stored_as_an_empty_string(tmp_path):
    """A caller with nothing to say must not be able to put a null in the table."""
    store = build(tmp_path)
    try:
        store.add(
            stream="thermal",
            started=1000.0,
            ended=1002.0,
            box=(5, 6, 7, 8),
            travelled_px=19.0,
            label=None,
            clip_path=None,
        )
        event = store.recent()[0]
        assert event.label == ""
        assert event.clip_path == ""
    finally:
        store.close()


def test_the_table_refuses_a_null_label(tmp_path):
    """The reader does not defend against nulls, so the schema must forbid them."""
    import sqlite3

    store = build(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute(
                "INSERT INTO events "
                "(stream, started, ended, x, y, w, h, travelled_px, label, confidence, clip_path) "
                "VALUES ('thermal', 1.0, 2.0, 0, 0, 4, 4, 20.0, NULL, 0.0, '')"
            )
    finally:
        store.close()


def test_recent_is_newest_first(tmp_path):
    store = build(tmp_path)
    try:
        for started in (100.0, 300.0, 200.0):
            store.add("thermal", started, started + 1.0, (0, 0, 4, 4), 20.0)
        assert [e.started for e in store.recent()] == [300.0, 200.0, 100.0]
    finally:
        store.close()


def test_recent_honours_the_limit(tmp_path):
    store = build(tmp_path)
    try:
        for started in range(10):
            store.add("thermal", float(started), started + 1.0, (0, 0, 4, 4), 20.0)
        assert [e.started for e in store.recent(limit=3)] == [9.0, 8.0, 7.0]
    finally:
        store.close()


def test_between_returns_events_overlapping_the_window(tmp_path):
    store = build(tmp_path)
    try:
        store.add("thermal", 100.0, 110.0, (0, 0, 4, 4), 20.0)  # before
        store.add("thermal", 195.0, 205.0, (0, 0, 4, 4), 20.0)  # straddles the start
        store.add("thermal", 300.0, 310.0, (0, 0, 4, 4), 20.0)  # inside
        store.add("thermal", 500.0, 510.0, (0, 0, 4, 4), 20.0)  # after
        found = store.between(200.0, 400.0)
        assert [e.started for e in found] == [195.0, 300.0]
    finally:
        store.close()


def test_between_can_be_filtered_by_stream(tmp_path):
    store = build(tmp_path)
    try:
        store.add("thermal", 300.0, 310.0, (0, 0, 4, 4), 20.0)
        store.add("visible", 320.0, 330.0, (0, 0, 4, 4), 20.0)
        assert [e.stream for e in store.between(0.0, 1000.0, stream="visible")] == ["visible"]
        assert len(store.between(0.0, 1000.0)) == 2
    finally:
        store.close()


def test_between_is_ordered_oldest_first(tmp_path):
    store = build(tmp_path)
    try:
        for started in (300.0, 100.0, 200.0):
            store.add("thermal", started, started + 1.0, (0, 0, 4, 4), 20.0)
        assert [e.started for e in store.between(0.0, 1000.0)] == [100.0, 200.0, 300.0]
    finally:
        store.close()


def test_delete_before_removes_the_old_and_counts_them(tmp_path):
    store = build(tmp_path)
    try:
        store.add("thermal", 100.0, 110.0, (0, 0, 4, 4), 20.0)
        store.add("thermal", 200.0, 210.0, (0, 0, 4, 4), 20.0)
        store.add("thermal", 400.0, 410.0, (0, 0, 4, 4), 20.0)
        assert store.delete_before(300.0) == 2
        assert [e.started for e in store.recent()] == [400.0]
    finally:
        store.close()


def test_delete_before_keeps_an_event_that_straddles_the_cutoff(tmp_path):
    """Its footage is only half gone, so the event still points at something."""
    store = build(tmp_path)
    try:
        store.add("thermal", 290.0, 310.0, (0, 0, 4, 4), 20.0)
        assert store.delete_before(300.0) == 0
        assert len(store.recent()) == 1
    finally:
        store.close()


def test_delete_before_can_be_limited_to_one_stream(tmp_path):
    """Retention reclaims one stream's footage at a time; the other stream's
    events still point at files that are very much still there."""
    store = build(tmp_path)
    try:
        store.add("thermal", 100.0, 110.0, (0, 0, 4, 4), 20.0)
        store.add("visible", 100.0, 110.0, (0, 0, 4, 4), 20.0)
        assert store.delete_before(300.0, stream="thermal") == 1
        assert [e.stream for e in store.recent()] == ["visible"]
    finally:
        store.close()


def test_events_survive_reopening_the_file(tmp_path):
    store = build(tmp_path)
    store.add("thermal", 100.0, 110.0, (1, 2, 3, 4), 20.0)
    store.close()
    reopened = build(tmp_path)
    try:
        assert reopened.recent()[0].box == (1, 2, 3, 4)
    finally:
        reopened.close()


def test_started_is_indexed(tmp_path):
    """The timeline queries by time range and this table grows all day."""
    store = build(tmp_path)
    try:
        plan = store._connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM events WHERE started BETWEEN 1 AND 2"
        ).fetchall()
        assert any("INDEX" in str(tuple(row)).upper() for row in plan), plan
    finally:
        store.close()


def test_the_database_is_in_wal_mode(tmp_path):
    """Same settings as segments.db: the console reads this while it is written."""
    store = build(tmp_path)
    try:
        mode = store._connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        store.close()


def test_the_parent_directory_is_created(tmp_path):
    store = EventStore(tmp_path / "fresh" / "events.db")
    try:
        assert (tmp_path / "fresh" / "events.db").exists()
    finally:
        store.close()
