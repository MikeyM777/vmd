import json
import logging
import os
import subprocess
import time

import pytest

from vmd import record_main as record_main_module
from vmd.record_main import RecordingService, main, parse_args
from vmd.settings import Settings, StreamSettings
from vmd.storage.index import SegmentIndex

GB = 1024**3


def build_settings(tmp_path, budget_gb=100.0, retention_days=None):
    settings = Settings()
    settings.camera.streams = [
        StreamSettings(name="thermal", url="rtsp://example/thermal"),
        StreamSettings(name="visible", url="rtsp://example/visible", enabled=False),
    ]
    settings.storage.root = tmp_path / "recordings"
    settings.storage.budget_gb = budget_gb
    settings.storage.retention_days = retention_days
    settings.storage.segment_seconds = 4
    return settings


class FakeProcess:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def spawn_fake(command, log_path=None):
    # SegmentRecorder passes the log path as a second argument, so this stand-in
    # must accept it even though the fake never writes anything.
    return FakeProcess()


def test_only_enabled_streams_get_recorders(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    assert [r.stream for r in service.recorders] == ["thermal"]
    service.stop()


def test_each_stream_records_into_its_own_directory(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    assert (tmp_path / "recordings" / "thermal").is_dir()
    service.stop()


def test_run_once_starts_the_recorder(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    assert service.recorders[0].running is True
    service.stop()


def test_finished_segments_are_indexed(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()

    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))

    service.run_once(now=1000.0)
    indexed = service.index.all()
    assert [os.path.basename(s.path) for s in indexed] == ["2026-08-07_10-00-00.mp4"]
    assert indexed[0].stream == "thermal"
    assert indexed[0].size_bytes == 2048
    service.stop()


def test_a_segment_is_not_indexed_twice(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))
    service.run_once(now=1000.0)
    service.run_once(now=2000.0)
    assert len(service.index.all()) == 1
    service.stop()


def test_retention_deletes_over_budget(tmp_path):
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)  # a 3000-byte budget
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))
    service.run_once(now=1000.0)
    assert not (directory / names[0]).exists()
    service.stop()


def test_status_reports_what_the_ui_needs(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    status = service.status()
    assert "streams" in status
    assert status["streams"][0]["name"] == "thermal"
    assert "used_bytes" in status
    assert "budget_bytes" in status
    assert "oldest" in status
    assert "warning" in status
    service.stop()


def test_index_persists_across_restarts(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))
    service.run_once(now=1000.0)
    service.stop()

    restarted = RecordingService(settings, spawn=spawn_fake)
    # Both, not one. The pass while recording indexes the older file and leaves
    # the newest alone - it is the one ffmpeg would have open - and stop() picks
    # that one up once there is no ffmpeg left to have it open. This used to
    # read 1, which recorded the fact that the last segment of every run was
    # being dropped rather than the behaviour anyone wanted.
    indexed = sorted(os.path.basename(s.path) for s in restarted.index.all())
    assert indexed == ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4"]
    restarted.stop()


def test_parse_args_defaults():
    args = parse_args([])
    assert args.settings == "settings.json"
    assert args.once is False


def test_parse_args_accepts_settings_path():
    args = parse_args(["--settings", "/tmp/s.json", "--once"])
    assert args.settings == "/tmp/s.json"
    assert args.once is True


def test_status_reports_unhealthy_when_a_stream_is_down(tmp_path):
    class DeadProcess(FakeProcess):
        def poll(self):
            return 1

    service = RecordingService(build_settings(tmp_path), spawn=lambda c, log=None: DeadProcess())
    service.run_once()
    status = service.status()
    # A stream that never starts keeps `restarts` at zero, so health must never be
    # inferred from the restart count alone.
    assert status["streams"][0]["running"] is False
    assert status["streams"][0]["restarts"] == 0
    assert status["healthy"] is False
    service.stop()


def test_stuck_deletions_are_reported(tmp_path, monkeypatch):
    # `apply_plan`'s `unlink` default is bound at import time, so patching os.unlink
    # afterwards cannot reach it. The seam that does work is record_main's own module
    # reference to apply_plan, which is resolved at call time.
    from vmd import record_main as record_main_module
    from vmd.storage.retention import apply_plan as real_apply_plan

    def refuse(_path):
        raise PermissionError("file is in use")

    def refusing_apply_plan(plan, index, unlink=None, events=None):
        return real_apply_plan(plan, index, unlink=refuse, events=events)

    monkeypatch.setattr(record_main_module, "apply_plan", refusing_apply_plan)

    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))

    service.run_once(now=1000.0)
    status = service.status()
    assert status["stuck_deletions"] > 0
    assert status["healthy"] is False
    assert (directory / names[0]).exists(), "a refused deletion must leave the file alone"
    service.stop()


def test_the_segment_being_written_is_never_deleted(tmp_path):
    # The whole design rests on this: discovery excludes the file ffmpeg still has
    # open, so it never enters the index and retention can never reach it. If that
    # ever stopped being true, retention would delete a recording in progress.
    settings = build_settings(tmp_path, budget_gb=1 / 1024**3)  # absurdly small budget
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    closed = directory / "2026-08-07_10-00-00.mp4"
    open_now = directory / "2026-08-07_10-05-00.mp4"
    closed.write_bytes(b"x" * 2000)
    open_now.write_bytes(b"x" * 2000)
    os.utime(closed, (100.0, 100.0))
    os.utime(open_now, (400.0, 400.0))

    service.run_once(now=1000.0)

    assert open_now.exists(), "the segment still being written must never be deleted"
    assert str(open_now) not in [s.path for s in service.index.all()]
    service.stop()


def test_retention_survives_the_clock_going_backwards(tmp_path):
    # This machine may correct its clock by NTP after boot. A backwards step must not
    # stall retention until the clock catches up, or the disk fills in the meantime.
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=60.0)
    service.run_once(now=1_000_000.0)  # retention runs, remembers a large timestamp

    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))

    service.run_once(now=500.0)  # clock stepped far backwards
    assert not (directory / names[0]).exists(), "retention must still run after a clock step"
    service.stop()


def test_retention_does_not_run_on_every_pass(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=60.0)
    calls = []
    original = service._apply_retention

    def counted(now):
        calls.append(now)
        return original(now)

    service._apply_retention = counted
    service.run_once(now=1000.0)
    service.run_once(now=1005.0)
    service.run_once(now=1010.0)
    # Called every pass, but the expensive index read inside is rate-limited.
    assert len(calls) == 3
    assert service._last_retention == 1000.0
    service.stop()


def test_run_forever_survives_a_failing_pass(tmp_path, monkeypatch):
    # A transient error must never end the service: nothing restarts this process.
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient disk error")
        if calls["n"] >= 3:
            raise KeyboardInterrupt
        return None

    monkeypatch.setattr(service, "run_once", flaky)
    monkeypatch.setattr("vmd.record_main.time.sleep", lambda _s: None)

    service.run_forever(interval=0.0)  # must return normally, not raise

    assert calls["n"] >= 3, "the loop must have continued after the failure"


def test_stop_indexes_the_final_segment(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    for name, mtime in (("2026-08-07_10-00-00.mp4", 100.0), ("2026-08-07_10-05-00.mp4", 400.0)):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (mtime, mtime))

    index_path = tmp_path / "recordings" / "segments.db"
    service.stop()

    from vmd.storage.index import SegmentIndex
    reopened = SegmentIndex(index_path)
    names = [os.path.basename(s.path) for s in reopened.all()]
    assert "2026-08-07_10-00-00.mp4" in names
    reopened.close()


def test_stuck_paths_stay_in_seen(tmp_path, monkeypatch):
    # A segment whose deletion failed must not be re-discovered and re-indexed on
    # every pass; that is wasted work exactly when the disk is under pressure.
    from vmd import record_main as record_main_module
    from vmd.storage.retention import apply_plan as real_apply_plan

    def refuse(_path):
        raise PermissionError("file is in use")

    monkeypatch.setattr(
        record_main_module,
        "apply_plan",
        lambda plan, index, unlink=None, events=None: real_apply_plan(
            plan, index, unlink=refuse, events=events
        ),
    )

    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    names = ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"]
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2000)
        os.utime(path, (100.0 + offset, 100.0 + offset))

    service.run_once(now=1000.0)
    assert str(directory / names[0]) in service._seen
    service.stop()


def test_main_reports_a_broken_settings_file_without_crashing(tmp_path, capsys):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert main(["--settings", str(path), "--once"]) == 1


def test_main_refuses_to_start_with_no_enabled_streams(tmp_path):
    from vmd.settings import Settings, save_settings

    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    assert main(["--settings", str(path), "--once"]) == 1


def test_main_runs_a_single_pass_over_a_file_source(tmp_path):
    from vmd.settings import Settings, StreamSettings, save_settings

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"not really a video")  # ffmpeg will fail; that is fine here
    settings = Settings()
    settings.camera.streams = [StreamSettings(name="thermal", url=str(source))]
    settings.storage.root = tmp_path / "recordings"
    path = tmp_path / "settings.json"
    save_settings(settings, path)

    # A single pass must complete and clean up even though the source is unusable.
    assert main(["--settings", str(path), "--once"]) == 0


# --------------------------------------------------------------------------
# Where the recorder pulls from. Every stream that crosses the radio link twice
# is a stream the link cannot carry once.
# --------------------------------------------------------------------------


def test_records_from_the_local_streaming_server_when_it_is_running(tmp_path, monkeypatch):
    import socket

    from vmd.settings import CameraSettings, Settings, StorageSettings, StreamSettings

    # A listener standing in for the streaming server's RTSP port.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    endpoint = tmp_path / "streaming.json"
    endpoint.write_text(
        json.dumps(
            {
                "api_port": 1984,
                "rtsp_port": port,
                "streams": {"thermal": f"rtsp://127.0.0.1:{port}/thermal"},
            }
        ),
        encoding="utf-8",
    )

    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/thermal", enabled=True)],
        ),
        storage=StorageSettings(root=tmp_path / "rec"),
    )
    try:
        service = RecordingService(settings, spawn=spawn_fake, endpoint_path=endpoint)
        assert service.recorders[0].source_url == f"rtsp://127.0.0.1:{port}/thermal"
    finally:
        listener.close()


def test_falls_back_to_the_camera_when_the_streaming_server_is_gone(tmp_path):
    """Recording something matters more than recording it cheaply."""
    from vmd.settings import CameraSettings, Settings, StorageSettings, StreamSettings

    endpoint = tmp_path / "streaming.json"
    endpoint.write_text(
        json.dumps(
            {"api_port": 1984, "rtsp_port": 59999, "streams": {"thermal": "rtsp://127.0.0.1:59999/thermal"}}
        ),
        encoding="utf-8",
    )

    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/thermal", enabled=True)],
        ),
        storage=StorageSettings(root=tmp_path / "rec"),
    )
    service = RecordingService(settings, spawn=spawn_fake, endpoint_path=endpoint)
    assert service.recorders[0].source_url == "rtsp://10.0.0.2/thermal"


# ------------------------------------------------- the events beside the index
#
# Events point at footage. Retention deletes footage. Nothing was passing the
# event store to apply_plan, so the list would have gone on offering to play
# files that had been reclaimed months earlier.


def write_segments(tmp_path, names, size=2000):
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * size)
        os.utime(path, (100.0 + offset, 100.0 + offset))
    return directory


def test_a_machine_without_detection_gets_no_events_database(tmp_path):
    """The recorder must not create the detector's database. A file that
    appears on every machine is a file every machine has to explain."""
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    write_segments(tmp_path, ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4"])
    service.run_once(now=1000.0)
    service.stop()

    assert not (tmp_path / "recordings" / "events.db").exists()


def test_retention_reclaims_the_events_whose_footage_it_deleted(tmp_path):
    from vmd.detect.events import EventStore
    from vmd.storage.discovery import parse_segment_start

    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    root = tmp_path / "recordings"
    root.mkdir(parents=True, exist_ok=True)

    names = [
        "2026-08-07_10-00-00.mp4",
        "2026-08-07_10-05-00.mp4",
        "2026-08-07_10-10-00.mp4",
    ]
    oldest = parse_segment_start(names[0])
    store = EventStore(root / "events.db")
    store.add("thermal", oldest - 60, oldest - 55, (1, 2, 3, 4), 40.0)  # inside the doomed file
    store.add("thermal", oldest + 600, oldest + 605, (1, 2, 3, 4), 40.0)  # still on disk
    store.add("visible", oldest - 60, oldest - 55, (1, 2, 3, 4), 40.0)  # another camera
    store.close()

    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    write_segments(tmp_path, names)
    service.run_once(now=1000.0)
    service.stop()

    reader = EventStore(root / "events.db")
    try:
        kept = reader.recent()
        # Which rows survived, not what order they come back in: `recent()`
        # answers in insertion order, because this machine's clock is set by
        # hand and an event stamped before the rows already in the table must
        # not sort itself off the end of the alarm's window.
        assert sorted((e.stream, e.started) for e in kept) == sorted(
            [
                ("thermal", oldest + 600),
                ("visible", oldest - 60),
            ]
        ), "only the thermal events under the deleted footage should have gone"
    finally:
        reader.close()


def test_a_detector_enabled_after_the_recorder_started_still_gets_reclaimed(tmp_path):
    """The recorder runs for months. Detection is ticked on in the Settings
    tab one afternoon, and the store appears underneath a service that has
    already decided there was none."""
    from vmd.detect.events import EventStore
    from vmd.storage.discovery import parse_segment_start

    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    service.run_once()
    root = tmp_path / "recordings"
    assert not (root / "events.db").exists()

    names = [
        "2026-08-07_10-00-00.mp4",
        "2026-08-07_10-05-00.mp4",
        "2026-08-07_10-10-00.mp4",
    ]
    oldest = parse_segment_start(names[0])
    store = EventStore(root / "events.db")  # the detector, started this afternoon
    store.add("thermal", oldest - 60, oldest - 55, (1, 2, 3, 4), 40.0)
    store.close()

    write_segments(tmp_path, names)
    service.run_once(now=1000.0)
    service.stop()

    reader = EventStore(root / "events.db")
    try:
        assert reader.count() == 0, "the events were never reclaimed"
    finally:
        reader.close()


def test_stopping_the_recorder_lets_go_of_the_events_database(tmp_path):
    """A handle left open would stop the next recorder from opening it, and on
    Windows would stop anything from moving the file at all."""
    from vmd.detect.events import EventStore

    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    root = tmp_path / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    EventStore(root / "events.db").close()

    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    write_segments(tmp_path, ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4"])
    service.run_once(now=1000.0)
    service.stop()

    (root / "events.db").unlink()  # PermissionError on Windows if a handle is open


def test_an_unreadable_events_database_does_not_stop_retention(tmp_path):
    """Freeing the disk is what retention is for. If it needed a working
    events.db to finish, a corrupt one would fill the disk and stop recording."""
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3)
    root = tmp_path / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    (root / "events.db").write_bytes(b"this is not a database")

    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once()
    directory = write_segments(
        tmp_path,
        ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4", "2026-08-07_10-10-00.mp4"],
    )
    service.run_once(now=1000.0)
    service.stop()

    assert not (directory / "2026-08-07_10-00-00.mp4").exists()


def test_stop_indexes_the_segment_that_was_still_being_written(tmp_path):
    """The one file discovery is built never to touch, once it is safe to touch.

    While recording runs, the newest file in a directory is the one ffmpeg has
    open, so find_closed_segments always leaves it alone and waits for it to
    settle. After stop_all() there is no ffmpeg left to have it open - which is
    exactly what stop() says it is indexing, and did not: the last segment of
    every run stayed on disk invisible to the storage budget and missing from
    the Playback timeline until some later run happened to write a newer file
    beside it.
    """
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once(now=100.0)

    directory = tmp_path / "recordings" / "thermal"
    last = directory / "2026-08-07_10-00-00.mp4"
    last.write_bytes(b"x" * 2048)
    now = time.time()
    os.utime(last, (now, now))  # written a moment ago, as a live segment is

    index_path = tmp_path / "recordings" / "segments.db"
    service.stop()

    reopened = SegmentIndex(index_path)
    try:
        indexed = [os.path.basename(s.path) for s in reopened.all()]
    finally:
        reopened.close()
    assert indexed == ["2026-08-07_10-00-00.mp4"], indexed


def test_stop_leaves_a_directory_alone_when_its_ffmpeg_would_not_die(tmp_path):
    """A recorder that survived the kill may still have its file open.

    SegmentRecorder keeps `running` True when it cannot confirm the process is
    dead, precisely so that nothing treats the directory as finished. Indexing
    the file it is still writing would expose a live recording to retention,
    which is a worse failure than one unindexed segment.
    """

    class Immortal(FakeProcess):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

        def kill(self):
            pass

    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=lambda command, log_path=None: Immortal())
    service.run_once(now=100.0)

    directory = tmp_path / "recordings" / "thermal"
    (directory / "2026-08-07_10-00-00.mp4").write_bytes(b"x" * 2048)

    index_path = tmp_path / "recordings" / "segments.db"
    service.stop()
    assert service.recorders[0].running is True, "the fake process was supposed to survive"

    reopened = SegmentIndex(index_path)
    try:
        assert reopened.all() == []
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# Following the Settings tab. This process outlives the console window, so the
# next console adopts it with the configuration it started with - which makes
# re-reading the file the only way a saved setting ever reaches it.
# ---------------------------------------------------------------------------


def save_settings_file(path, root, budget_gb=100.0, retention_days=None,
                       segment_seconds=60, streams=(("thermal", True),)):
    # 60s rather than the 4s the older tests use: the stall check restarts any
    # recorder that has produced nothing for twice the segment length, and these
    # tests step a fake clock by a hundred seconds between passes.
    from vmd.settings import save_settings

    settings = Settings()
    settings.camera.streams = [
        StreamSettings(name=name, url=f"rtsp://example/{name}", enabled=enabled)
        for name, enabled in streams
    ]
    settings.storage.root = root
    settings.storage.budget_gb = budget_gb
    settings.storage.retention_days = retention_days
    settings.storage.segment_seconds = segment_seconds
    save_settings(settings, path)
    return settings


def touch_later(path):
    """Make the file look newer, without waiting for a clock to move."""
    stamp = os.stat(path).st_mtime + 10
    os.utime(path, (stamp, stamp))


def service_following(tmp_path, **kwargs):
    path = tmp_path / "settings.json"
    settings = save_settings_file(path, tmp_path / "recordings", **kwargs)
    service = RecordingService(
        settings, spawn=spawn_fake, settings_path=path, retention_interval=0.0
    )
    return service, path


def test_a_saved_budget_reaches_a_recorder_that_is_already_running(tmp_path):
    """The operator has no terminal and cannot restart this process.

    The console can restart itself; it cannot restart this, because this is
    meant to survive the window closing and the next window adopts it exactly
    as it was.
    """
    service, path = service_following(tmp_path, budget_gb=100.0)
    try:
        service.run_once(now=100.0)
        assert service.status()["budget_bytes"] == int(100.0 * 1024**3)

        save_settings_file(path, tmp_path / "recordings", budget_gb=7.0, retention_days=3)
        touch_later(path)
        service.run_once(now=200.0)

        assert service.status()["budget_bytes"] == int(7.0 * 1024**3)
        assert service.settings.storage.retention_days == 3
    finally:
        service.stop()


def test_an_unchanged_file_is_not_read_again(tmp_path, monkeypatch):
    """The trigger is the file's timestamp, on a loop that runs for months."""
    service, path = service_following(tmp_path)
    reads = []
    real = record_main_module.load_settings
    monkeypatch.setattr(
        record_main_module, "load_settings", lambda p: (reads.append(p), real(p))[1]
    )
    try:
        for _ in range(5):
            service.run_once()
        assert reads == []
    finally:
        service.stop()


def test_a_settings_file_that_has_gone_leaves_recording_exactly_as_it_is(tmp_path):
    """A missing file is never read as "the operator wants the defaults".

    load_settings answers a missing path with defaults, which is right for a
    first run and catastrophic here: it would silently move the recording
    folder and change the budget because a file was momentarily absent.
    """
    service, path = service_following(tmp_path, budget_gb=7.0)
    try:
        service.run_once(now=100.0)
        before = service.status()["budget_bytes"]
        root_before = service.root

        path.unlink()
        service.run_once(now=200.0)

        assert service.status()["budget_bytes"] == before
        assert service.root == root_before
        assert service.recorders[0].running is True
    finally:
        service.stop()


def test_an_unreadable_settings_file_does_not_stop_recording(tmp_path, caplog):
    """Recording on stale settings beats not recording."""
    service, path = service_following(tmp_path, budget_gb=7.0)
    try:
        service.run_once(now=100.0)
        before = service.status()["budget_bytes"]

        path.write_text("{ this is not json", encoding="utf-8")
        touch_later(path)
        with caplog.at_level(logging.ERROR):
            service.run_once(now=200.0)

        assert service.status()["budget_bytes"] == before
        assert service.recorders[0].running is True
        assert "last settings that worked" in caplog.text
    finally:
        service.stop()


def test_a_broken_settings_file_is_complained_about_once_not_every_pass(tmp_path, caplog):
    service, path = service_following(tmp_path)
    try:
        path.write_text("{ this is not json", encoding="utf-8")
        touch_later(path)
        with caplog.at_level(logging.ERROR):
            for _ in range(6):
                service.run_once()
        assert caplog.text.count("last settings that worked") == 1
    finally:
        service.stop()


def test_a_stream_ticked_for_recording_starts_being_recorded(tmp_path):
    service, path = service_following(tmp_path, streams=(("thermal", True), ("visible", False)))
    try:
        service.run_once(now=100.0)
        assert [r.stream for r in service.recorders] == ["thermal"]

        save_settings_file(
            path, tmp_path / "recordings", streams=(("thermal", True), ("visible", True))
        )
        touch_later(path)
        service.run_once(now=200.0)

        assert [r.stream for r in service.recorders] == ["thermal", "visible"]
        assert all(r.running for r in service.recorders)
    finally:
        service.stop()


def test_a_changed_segment_length_reaches_the_ffmpeg_command(tmp_path):
    service, path = service_following(tmp_path, segment_seconds=300)
    try:
        service.run_once(now=100.0)
        command = service.recorders[0].build_command()
        assert command[command.index("-segment_time") + 1] == "300"

        save_settings_file(path, tmp_path / "recordings", segment_seconds=60)
        touch_later(path)
        service.run_once(now=200.0)

        command = service.recorders[0].build_command()
        assert command[command.index("-segment_time") + 1] == "60"
    finally:
        service.stop()


def test_moving_the_folder_keeps_the_segment_that_was_being_written(tmp_path):
    """The hard one: an in-flight segment lives in the folder being left behind.

    It must be closed, indexed into the catalogue that knows where it is, and
    still findable there afterwards - not orphaned in a tree nothing reads.
    """
    service, path = service_following(tmp_path)
    old_root = tmp_path / "recordings"
    new_root = tmp_path / "elsewhere"
    try:
        service.run_once(now=100.0)
        in_flight = old_root / "thermal" / "2026-08-07_10-00-00.mp4"
        in_flight.write_bytes(b"x" * 4096)

        save_settings_file(path, new_root)
        touch_later(path)
        service.run_once(now=200.0)

        assert service.root == new_root
        assert in_flight.exists(), "the footage must not be moved or deleted"
        assert service.recorders[0].output_dir == new_root / "thermal"

        old_index = SegmentIndex(old_root / "segments.db")
        try:
            found = [s.path for s in old_index.all()]
        finally:
            old_index.close()
        assert str(in_flight) in found, "the segment left behind is unfindable"
    finally:
        service.stop()


def test_moving_the_folder_never_applies_retention_to_the_wrong_one(tmp_path):
    """Deleting the old folder's footage because the new one is over budget - or
    the reverse - is the worst outcome available here, so the catalogue and the
    budget are swapped together and never mixed.
    """
    service, path = service_following(tmp_path, budget_gb=100.0)
    old_root = tmp_path / "recordings"
    new_root = tmp_path / "elsewhere"
    try:
        service.run_once(now=100.0)
        write_segments(tmp_path, ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4"])
        service.run_once(now=1000.0)
        old_files = sorted((old_root / "thermal").glob("*.mp4"))
        assert len(old_files) == 2

        # A budget far too small for anything, so the very next retention pass
        # deletes whatever it can see.
        save_settings_file(path, new_root, budget_gb=1 / 1024**3)
        touch_later(path)
        service.run_once(now=2000.0)

        assert [p for p in old_files if p.exists()] == old_files, (
            "retention reached into the folder it no longer manages"
        )
        assert service.index.all() == []
    finally:
        service.stop()


def test_consecutive_segments_never_claim_the_same_second_twice(tmp_path):
    """Measured on a real run: 20 of 20 pairs overlapped, mean 0.463 s.

    `start` is read out of the filename, which carries whole seconds; the close
    time carries the fraction as well. ffmpeg shuts one file and opens the next
    in the same instant, so that fraction was counted in both. Seeking survived
    it - it takes the first match - but the index is what a gap detector, a
    total duration and a coverage figure are all computed from, and the error
    accumulated once per segment.
    """
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    names = [
        "2026-08-07_10-00-00.mp4",
        "2026-08-07_10-00-30.mp4",
        "2026-08-07_10-01-00.mp4",
        "2026-08-07_10-01-30.mp4",
    ]
    from vmd.storage.discovery import parse_segment_start

    for index, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * 2048)
        # Closed 30.463 s after it opened, which is what a 30 s target really
        # produces: the segmenter cuts at the next keyframe.
        closed = parse_segment_start(name) + 30.463
        os.utime(path, (closed, closed))

    try:
        service.run_once(now=parse_segment_start(names[-1]) + 600)
        segments = service.index.all()
        assert len(segments) >= 3, [s.path for s in segments]
        for earlier, later in zip(segments, segments[1:]):
            assert earlier.end <= later.start, (
                f"{os.path.basename(earlier.path)} runs "
                f"{earlier.end - later.start:.3f}s into "
                f"{os.path.basename(later.path)}"
            )
        assert all(s.end > s.start for s in segments), "a segment covering nothing"
    finally:
        service.stop()


def test_a_segment_with_no_successor_keeps_its_measured_end(tmp_path):
    """The last of a run, and the one whose successor was never written.

    A recorder that died mid-run leaves a segment with nothing after it. There
    is no boundary to clamp against and none should be invented: its close time
    is the truth about how much footage it holds.
    """
    from vmd.storage.discovery import parse_segment_start

    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    name = "2026-08-07_10-00-00.mp4"
    start = parse_segment_start(name)
    path = directory / name
    path.write_bytes(b"x" * 2048)
    os.utime(path, (start + 12.75, start + 12.75))

    service.stop()  # no ffmpeg left, so this file is finished and indexable

    reopened = SegmentIndex(tmp_path / "recordings" / "segments.db")
    try:
        segment = reopened.all()[0]
        # Absolute tolerance: these are epoch seconds in the billions, where
        # approx's default relative tolerance would swallow a whole half-minute.
        assert segment.end == pytest.approx(start + 12.75, abs=0.01)
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# Which process is recording. Written by the process that is recording, so that
# every supervisor above it - the console, the logon task, an operator - is safe
# by construction: whatever starts a second recorder, the second one refuses.
#
# Nothing here starts a process or looks at a real one. `image_of` and `booted`
# are the two things that ask the operating system, and both are handed in.
# ---------------------------------------------------------------------------


def claim_of(tmp_path):
    return tmp_path / "recorder.pid"


def gone(_pid):
    """Nothing is running with that number."""
    return None


def a_recorder(_pid):
    return "python.exe"


def somebody_else(_pid):
    return "svchost.exe"


def never_booted():
    return None


def write_claim(pid_path, pid, identity=None):
    pid_path.write_text(str(pid), encoding="utf-8")
    if identity is not None:
        record_main_module.identity_path(pid_path).write_text(
            json.dumps(identity.as_dict(), indent=2), encoding="utf-8"
        )


def test_the_claim_is_a_bare_integer_and_nothing_else(tmp_path):
    """Two other programs already parse this file as one number.

    vmd\\desktop\\services.py does int(text.strip()) and
    scripts\\recorder_service.ps1 does [int]::TryParse over the whole file.
    Either of them failing to parse reads as "no recorder is running", and the
    answer to that is to start a second one - the exact accident this file
    exists to prevent. Anything richer lives in the companion beside it.
    """
    pid_path = claim_of(tmp_path)
    identity = record_main_module.RecorderIdentity(pid=4242, executable="py.exe")
    assert record_main_module.claim_recorder(pid_path, identity, image_of=gone) is None

    text = pid_path.read_text(encoding="utf-8")
    assert int(text.strip()) == 4242
    assert text.strip() == text.strip().strip("{}\"")  # no JSON, no decoration
    assert record_main_module.read_identity(pid_path).executable == "py.exe"


def test_a_claim_left_behind_by_a_forced_kill_is_taken(tmp_path):
    """taskkill /F gives a process no chance to tidy up after itself."""
    pid_path = claim_of(tmp_path)
    write_claim(pid_path, 4242)

    identity = record_main_module.RecorderIdentity(pid=99, executable="python.exe")
    holder = record_main_module.claim_recorder(pid_path, identity, image_of=gone)

    assert holder is None
    assert record_main_module.read_pid(pid_path) == 99


def test_a_claim_from_a_previous_boot_is_taken_even_though_the_pid_is_alive(tmp_path):
    """Windows hands the same numbers out again after a restart.

    A claim written before the machine last started cannot possibly still be
    its process, whoever is holding that number now - and believing it would
    mean the console reporting "recording" while nothing reaches the disk.
    """
    pid_path = claim_of(tmp_path)
    stale = record_main_module.RecorderIdentity(
        pid=4242, executable="python.exe", written_at=1000.0
    )
    write_claim(pid_path, 4242, stale)

    identity = record_main_module.RecorderIdentity(pid=99, executable="python.exe")
    holder = record_main_module.claim_recorder(
        pid_path,
        identity,
        image_of=a_recorder,  # something is alive with that number
        booted=lambda: 5000.0,  # but the machine started after the claim was written
    )

    assert holder is None
    assert record_main_module.read_pid(pid_path) == 99


def test_a_recycled_pid_running_something_else_is_not_a_recorder(tmp_path):
    """The check the logon wrapper already makes: is it our image, or just our number?"""
    pid_path = claim_of(tmp_path)
    write_claim(
        pid_path,
        4242,
        record_main_module.RecorderIdentity(pid=4242, executable=r"C:\VMD\.venv\Scripts\python.exe"),
    )

    identity = record_main_module.RecorderIdentity(pid=99, executable="python.exe")
    holder = record_main_module.claim_recorder(
        pid_path, identity, image_of=somebody_else, booted=never_booted
    )

    assert holder is None
    assert record_main_module.read_pid(pid_path) == 99


def test_a_claim_with_no_companion_still_rejects_an_obvious_impostor(tmp_path):
    """Claims written by the older console and by the logon task carry no companion."""
    pid_path = claim_of(tmp_path)
    write_claim(pid_path, 4242)

    identity = record_main_module.RecorderIdentity(pid=99)
    holder = record_main_module.claim_recorder(
        pid_path, identity, image_of=somebody_else, booted=never_booted
    )

    assert holder is None
    assert record_main_module.read_pid(pid_path) == 99


def test_a_recorder_that_really_is_running_keeps_the_claim(tmp_path):
    """The whole point. Two recorders on one folder fight over the segment files
    and write one SQLite index from two processes."""
    pid_path = claim_of(tmp_path)
    live = record_main_module.RecorderIdentity(
        pid=4242, executable="python.exe", written_at=9000.0
    )
    write_claim(pid_path, 4242, live)

    identity = record_main_module.RecorderIdentity(pid=99, executable="python.exe")
    holder = record_main_module.claim_recorder(
        pid_path, identity, image_of=a_recorder, booted=lambda: 1000.0
    )

    assert holder == 4242
    assert record_main_module.read_pid(pid_path) == 4242, "the live claim was trampled"


def test_two_recorders_starting_together_do_not_both_win(tmp_path):
    """The loser finds the winner's number, sees a live process, and defers."""
    pid_path = claim_of(tmp_path)
    first = record_main_module.RecorderIdentity(
        pid=101, executable="python.exe", written_at=9000.0
    )
    second = record_main_module.RecorderIdentity(
        pid=202, executable="python.exe", written_at=9000.0
    )

    assert record_main_module.claim_recorder(
        pid_path, first, image_of=gone, booted=lambda: 1000.0
    ) is None
    assert record_main_module.claim_recorder(
        pid_path, second, image_of=a_recorder, booted=lambda: 1000.0
    ) == 101
    assert record_main_module.read_pid(pid_path) == 101


def test_a_claim_that_cannot_be_written_does_not_stop_recording(tmp_path, monkeypatch):
    """Recording without a claim is worse than recording with one, and far
    better than not recording."""
    pid_path = claim_of(tmp_path)

    def refuse(*args, **kwargs):
        raise PermissionError("the folder is read-only")

    monkeypatch.setattr(record_main_module.os, "open", refuse)
    assert record_main_module.claim_recorder(pid_path, image_of=gone) is None


def test_releasing_removes_both_files(tmp_path):
    pid_path = claim_of(tmp_path)
    identity = record_main_module.RecorderIdentity(pid=99, executable="python.exe")
    record_main_module.claim_recorder(pid_path, identity, image_of=gone)

    record_main_module.release_recorder(pid_path, pid=99)
    assert not pid_path.exists()
    assert not record_main_module.identity_path(pid_path).exists()


def test_releasing_leaves_a_claim_that_somebody_else_has_taken(tmp_path):
    """The console may have started its own recorder after this one exited, and
    deleting that claim would let the next supervisor start a second one."""
    pid_path = claim_of(tmp_path)
    write_claim(pid_path, 4242)

    record_main_module.release_recorder(pid_path, pid=99)
    assert record_main_module.read_pid(pid_path) == 4242


def test_a_missing_claim_is_free(tmp_path):
    assert record_main_module.running_recorder(claim_of(tmp_path), image_of=gone) is None


def test_an_unreadable_process_list_is_not_read_as_nothing_running(tmp_path):
    """"The process list could not be read" is not "there is no such process".

    Refusing to start costs this one process; starting a second recorder costs
    the archive, so the unknown is resolved the safe way.
    """
    pid_path = claim_of(tmp_path)
    write_claim(pid_path, 4242)
    holder = record_main_module.running_recorder(
        pid_path, our_pid=99, image_of=lambda _pid: "", booted=never_booted
    )
    assert holder == 4242


def test_our_own_number_in_the_claim_is_not_somebody_else(tmp_path):
    """The logon wrapper writes the recorder's pid into this file itself, and
    may get there first."""
    pid_path = claim_of(tmp_path)
    write_claim(pid_path, 99)
    assert (
        record_main_module.running_recorder(
            pid_path, our_pid=99, image_of=a_recorder, booted=never_booted
        )
        is None
    )


def test_main_claims_the_recorder_and_gives_it_back(tmp_path):
    from vmd.settings import Settings, StreamSettings, save_settings

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"not really a video")
    settings = Settings()
    settings.camera.streams = [StreamSettings(name="thermal", url=str(source))]
    settings.storage.root = tmp_path / "recordings"
    path = tmp_path / "settings.json"
    save_settings(settings, path)

    assert main(["--settings", str(path), "--once"]) == 0
    assert not (tmp_path / "recorder.pid").exists(), "the claim outlived the recorder"


def test_main_leaves_a_running_recorder_alone(tmp_path, monkeypatch):
    """Started twice - by the console and by the logon task - is a normal
    Tuesday, and the second one must not touch the folder at all."""
    from vmd.settings import Settings, StreamSettings, save_settings

    settings = Settings()
    settings.camera.streams = [StreamSettings(name="thermal", url="rtsp://example/thermal")]
    settings.storage.root = tmp_path / "recordings"
    path = tmp_path / "settings.json"
    save_settings(settings, path)

    pid_path = tmp_path / "recorder.pid"
    write_claim(
        pid_path,
        4242,
        record_main_module.RecorderIdentity(
            pid=4242, executable="python.exe", written_at=9000.0
        ),
    )
    monkeypatch.setattr(record_main_module, "process_image", a_recorder)
    monkeypatch.setattr(record_main_module, "boot_time", lambda: 1000.0)

    def refuse(*args, **kwargs):  # pragma: no cover - reaching this is the failure
        raise AssertionError("a second recorder opened the archive")

    monkeypatch.setattr(record_main_module, "RecordingService", refuse)

    # Not 0. Standing down is a success, but a success the console has to be
    # able to tell from "it ran and finished": read as an ordinary death, the
    # console starts another recorder, which stands down as well, every two
    # seconds for as long as it is open. Not 1 either, which is what a settings
    # file it cannot read exits with and means the opposite - nothing is
    # recording.
    assert main(["--settings", str(path), "--once"]) == (
        record_main_module.ALREADY_RECORDING_EXIT
    )
    assert record_main_module.ALREADY_RECORDING_EXIT not in (0, 1)
    assert record_main_module.read_pid(pid_path) == 4242
    assert not (tmp_path / "recordings" / "segments.db").exists()


# --------------------------------------------- the last segment of a dead stream
#
# A stream that is renamed or disabled leaves its directory unowned, and
# _adopt_orphans is the only thing that ever looks at it again. It used to skip
# the newest file there on the grounds that ffmpeg might still have it open -
# which is right for a running stream, where a newer file arrives beside it a
# minute later, and wrong here: no newer file is ever written, so that segment
# occupied the drive while being invisible to the disk budget, to retention and
# to Playback, for ever.


def orphan_dir(tmp_path, name="an_old_name"):
    directory = tmp_path / "recordings" / name
    directory.mkdir(parents=True)
    return directory


def write_at(directory, name, mtime, size=2048):
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_the_last_segment_of_a_renamed_stream_is_adopted_not_lost(tmp_path):
    """The exact shape of the bug: the console restarts the recorder when the
    stream set changes, so the new service finds a directory nobody owns whose
    newest file was written seconds ago and will never get a successor."""
    settings = build_settings(tmp_path)
    directory = orphan_dir(tmp_path)
    now = time.time()
    earlier = write_at(directory, "2026-08-11_10-00-00.mp4", now - 600, size=4096)
    # What the killed ffmpeg had open. Nothing holds it now, and nothing newer
    # will ever be written beside it.
    last = write_at(directory, "2026-08-11_10-05-00.mp4", now, size=1024)

    service = RecordingService(settings, spawn=spawn_fake)
    try:
        indexed = {s.path for s in service.index.all()}
        assert str(earlier) in indexed
        assert str(last) in indexed, "the last segment of a dead stream is lost for ever"
        assert service.index.total_bytes() == 4096 + 1024
    finally:
        service.stop()


def test_a_lone_orphan_segment_is_adopted(tmp_path):
    """A stream that recorded once and was then renamed leaves exactly one file.
    Under the old rule it was always "the newest", so it was never indexed."""
    settings = build_settings(tmp_path)
    directory = orphan_dir(tmp_path)
    only = write_at(directory, "2026-08-11_10-00-00.mp4", time.time() - 30, size=3072)

    service = RecordingService(settings, spawn=spawn_fake)
    try:
        assert [s.path for s in service.index.all()] == [str(only)]
    finally:
        service.stop()


@pytest.mark.skipif(os.name != "nt", reason="the open-file probe is a Windows one")
def test_a_segment_something_still_has_open_is_never_adopted(tmp_path):
    """The guard the old rule was a blunt stand-in for. A segment indexed while
    it is still being written carries a truncated duration and is exposed to
    retention, which is worse than one indexed a minute late."""
    settings = build_settings(tmp_path)
    directory = orphan_dir(tmp_path)
    now = time.time()
    finished = write_at(directory, "2026-08-11_10-00-00.mp4", now - 600, size=4096)
    open_now = write_at(directory, "2026-08-11_10-05-00.mp4", now - 300, size=1024)

    with open_now.open("ab"):  # an ffmpeg left over from an earlier run
        service = RecordingService(settings, spawn=spawn_fake)
        try:
            indexed = {s.path for s in service.index.all()}
            assert str(finished) in indexed
            assert str(open_now) not in indexed, "a file being written was indexed"
        finally:
            service.stop()

    # Once it is closed, the next start picks it up rather than losing it.
    again = RecordingService(settings, spawn=spawn_fake)
    try:
        assert str(open_now) in {s.path for s in again.index.all()}
    finally:
        again.stop()


def test_an_orphan_directory_is_still_left_alone_while_a_recorder_owns_it(tmp_path):
    """Unchanged, and the reason the sweep skips owned directories at all: the
    live recorder's newest file is the one its ffmpeg has open."""
    settings = build_settings(tmp_path)
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True)
    write_at(directory, "2026-08-11_10-00-00.mp4", time.time() - 600)

    service = RecordingService(settings, spawn=spawn_fake)
    try:
        assert [s.path for s in service.index.all()] == []
    finally:
        service.stop()


# ------------------------------- what an ffmpeg that never started leaves behind
#
# The camera sends pcm_mulaw audio, MP4 cannot carry it, and ffmpeg exited
# before writing a header - 24 times in four minutes, leaving 24 files of zero
# bytes. Two of the three failures here are about noticing: an empty file is not
# a segment, and what ffmpeg said has to reach the one place the operator can
# read it.


def test_empty_segments_are_neither_indexed_nor_passed_over_in_silence(tmp_path, caplog):
    """24 of these appeared on the laptop in four minutes and nothing said a word.

    Not indexing them was always right - indexed, each would be offered by the
    Playback timeline as coverage of a minute that was never recorded, and
    counted by the storage budget as footage worth keeping - but it is also why
    nothing noticed. A file of zero bytes with a newer file beside it is an
    ffmpeg that opened it, wrote no header and moved on, and that is a fault to
    report rather than a file to skip.
    """
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    directory = tmp_path / "recordings" / "thermal"
    # The newest file is the one ffmpeg would have open - on Windows its size
    # reads as zero until it is closed - so it is never the evidence.
    for name, size, mtime in (
        ("2026-08-07_10-00-00.mp4", 2048, 100.0),
        ("2026-08-07_10-01-00.mp4", 2048, 150.0),
        ("2026-08-07_10-05-00.mp4", 0, 200.0),
        ("2026-08-07_10-10-00.mp4", 0, 300.0),
    ):
        path = directory / name
        path.write_bytes(b"x" * size)
        os.utime(path, (mtime, mtime))

    with caplog.at_level(logging.ERROR):
        service.run_once(now=1000.0)
    indexed = [os.path.basename(s.path) for s in service.index.all()]
    status = service.status(now=1000.0)
    service.stop()

    assert indexed == ["2026-08-07_10-00-00.mp4"], (
        "empty files were indexed as though they were recordings"
    )
    assert status["empty_segments"] == 1, "the abandoned file was not noticed"
    assert status["healthy"] is False, "empty segments are not a healthy recorder"
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "2026-08-07_10-05-00.mp4" in said and "thermal" in said, said


def test_what_ffmpeg_said_reaches_the_log(tmp_path, caplog):
    """Its stderr goes to a file, and that file reached nobody. The recorder's
    own output is pumped into the console's Logs tab, so this is where the
    camera's real complaint gets there from."""
    import logging

    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once()
    recorder = service.recorders[0]
    recorder.log_path.parent.mkdir(parents=True, exist_ok=True)
    recorder.log_path.write_bytes(
        b"[mp4] Could not find tag for codec pcm_mulaw in stream #1\n"
        b"Could not write header (incorrect codec parameters ?): Invalid argument\n"
    )

    with caplog.at_level(logging.WARNING):
        service.run_once(now=1000.0)
    service.stop()

    said = " ".join(record.getMessage() for record in caplog.records)
    assert "pcm_mulaw" in said, said
    assert "Could not write header" in said, said
    assert "thermal" in said, "the line must say which stream it is about"


# --------------------------------------------------------------------------
# Where the recorder pulls from, after start-up.
#
# The answer used to be decided once, in __init__, from a port answering - and
# never revisited. The scheduled task starts this process at logon, before any
# human has opened the console, so there is no streaming server to find and the
# camera is used directly. The operator opens the console minutes or hours
# later; go2rtc starts and opens its own connection to the camera, and from that
# moment every stream crosses the 15 km, ~5 Mb/s radio link twice, for months,
# with nothing anywhere saying so. The detector fixed exactly this
# (vmd\detect\runner.py, _try_the_other_address); the more important of the two
# processes still had it.
# --------------------------------------------------------------------------


def write_endpoint(path, port=8554, name="thermal"):
    path.write_text(
        json.dumps(
            {
                "api_port": 1984,
                "rtsp_port": port,
                "streams": {name: f"rtsp://127.0.0.1:{port}/{name}"},
            }
        ),
        encoding="utf-8",
    )
    return path


def answering(monkeypatch):
    """A stand-in for the streaming server's RTSP port, with a switch on it.

    Nothing here touches a real address: is_live is the only thing in the
    recorder that opens a socket, and this replaces it. A test that connected to
    a real port would be a test that hangs when the fix regresses.
    """
    alive = {"yes": False}
    monkeypatch.setattr(
        record_main_module, "is_live", lambda endpoint, timeout=1.5: alive["yes"]
    )
    return alive


def counting_spawn():
    started = []

    def spawn(command, log_path=None):
        started.append(command)
        return FakeProcess()

    return spawn, started


def test_the_recorder_moves_to_the_local_server_when_it_appears(tmp_path, monkeypatch):
    """The reboot case: no console at logon, a console an hour later."""
    endpoint = tmp_path / "streaming.json"  # nothing has written it yet
    alive = answering(monkeypatch)

    service = RecordingService(
        build_settings(tmp_path),
        spawn=spawn_fake,
        endpoint_path=endpoint,
        source_check_interval=0.0,
        source_settle_seconds=30.0,
    )
    service.run_once(now=1000.0)
    assert service.recorders[0].source_url == "rtsp://example/thermal"

    write_endpoint(endpoint)
    alive["yes"] = True

    service.run_once(now=1100.0)  # first seen here; not yet settled
    assert service.recorders[0].source_url == "rtsp://example/thermal"

    service.run_once(now=1200.0)  # settled, and nothing is being written
    assert service.recorders[0].source_url == "rtsp://127.0.0.1:8554/thermal", (
        "the recorder never moved to the local server, so the radio link is "
        "carrying this stream twice for the life of the process"
    )
    service.stop()


def test_a_streaming_server_that_comes_and_goes_does_not_restart_ffmpeg(
    tmp_path, monkeypatch
):
    """Restarting ffmpeg every few seconds is a cut in the footage every few
    seconds. A go2rtc that is flapping must not be followed."""
    endpoint = write_endpoint(tmp_path / "streaming.json")
    alive = answering(monkeypatch)
    alive["yes"] = True
    spawn, started = counting_spawn()

    service = RecordingService(
        build_settings(tmp_path),
        spawn=spawn,
        endpoint_path=endpoint,
        source_check_interval=0.0,
        source_settle_seconds=60.0,
    )
    service.run_once(now=1000.0)
    assert service.recorders[0].source_url == "rtsp://127.0.0.1:8554/thermal"
    spawned_once = len(started)

    # Thirty seconds down, thirty seconds up, for five minutes. Long enough
    # that a check lands twice inside each stretch and short enough that no
    # stretch outlasts the settle window - which is the whole of the rule.
    for step in range(1, 31):
        alive["yes"] = (step // 3) % 2 == 0
        service.run_once(now=1000.0 + step * 10)

    assert service.recorders[0].source_url == "rtsp://127.0.0.1:8554/thermal"
    assert len(started) == spawned_once, "ffmpeg was restarted by a flapping go2rtc"
    service.stop()


def test_it_falls_back_to_the_camera_when_the_streaming_server_dies(
    tmp_path, monkeypatch
):
    """Recording from the wrong place beats not recording."""
    endpoint = write_endpoint(tmp_path / "streaming.json")
    alive = answering(monkeypatch)
    alive["yes"] = True

    service = RecordingService(
        build_settings(tmp_path),
        spawn=spawn_fake,
        endpoint_path=endpoint,
        source_check_interval=0.0,
        source_settle_seconds=30.0,
    )
    service.run_once(now=1000.0)
    assert service.recorders[0].source_url == "rtsp://127.0.0.1:8554/thermal"

    alive["yes"] = False  # the console was closed, or go2rtc died
    service.run_once(now=1010.0)
    service.run_once(now=1100.0)

    assert service.recorders[0].source_url == "rtsp://example/thermal", (
        "with the local server gone the recorder must go back to the camera "
        "rather than pointing ffmpeg at a dead loopback port for ever"
    )
    service.stop()


def _segment_epoch(name):
    from vmd.storage.discovery import parse_segment_start

    return parse_segment_start(name)


def test_the_move_waits_for_a_segment_boundary(tmp_path, monkeypatch):
    """Closing ffmpeg mid-segment truncates the file it has open. The switch is
    deferred until ffmpeg has just opened a new one, so at most a few seconds of
    footage is at risk and every segment before it was closed by ffmpeg."""
    endpoint = tmp_path / "streaming.json"
    alive = answering(monkeypatch)
    settings = build_settings(tmp_path)
    settings.storage.segment_seconds = 300

    service = RecordingService(
        settings,
        spawn=spawn_fake,
        endpoint_path=endpoint,
        source_check_interval=0.0,
        source_settle_seconds=30.0,
    )
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    first = "2026-08-07_10-00-00.mp4"
    second = "2026-08-07_10-05-00.mp4"
    (directory / first).write_bytes(b"x" * 2048)
    base = _segment_epoch(first)

    service.run_once(now=base + 10)
    write_endpoint(endpoint)
    alive["yes"] = True

    service.run_once(now=base + 100)  # noticed
    service.run_once(now=base + 200)  # settled, but 200s into a 300s segment
    assert service.recorders[0].source_url == "rtsp://example/thermal", (
        "the switch cut into the segment ffmpeg had open"
    )

    (directory / second).write_bytes(b"")  # ffmpeg has just opened the next file
    service.run_once(now=base + 305)
    assert service.recorders[0].source_url == "rtsp://127.0.0.1:8554/thermal"
    service.stop()


def test_the_wait_for_a_boundary_is_bounded(tmp_path, monkeypatch):
    """A stream that never produces a boundary must not keep the link doubled
    for ever. The gap is then honest rather than absent."""
    endpoint = tmp_path / "streaming.json"
    alive = answering(monkeypatch)
    settings = build_settings(tmp_path)
    settings.storage.segment_seconds = 300

    service = RecordingService(
        settings,
        spawn=spawn_fake,
        endpoint_path=endpoint,
        source_check_interval=0.0,
        source_settle_seconds=30.0,
    )
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    name = "2026-08-07_10-00-00.mp4"
    (directory / name).write_bytes(b"x" * 2048)
    base = _segment_epoch(name)

    service.run_once(now=base + 10)
    write_endpoint(endpoint)
    alive["yes"] = True

    service.run_once(now=base + 50)  # noticed here
    service.run_once(now=base + 400)  # settled, never at a boundary
    assert service.recorders[0].source_url == "rtsp://example/thermal"

    service.run_once(now=base + 5000)  # long past any reasonable wait
    assert service.recorders[0].source_url == "rtsp://127.0.0.1:8554/thermal"
    service.stop()


def test_the_source_is_said_out_loud_when_it_changes(tmp_path, monkeypatch, caplog):
    """Recording that is silently costing double the link is the kind of
    invisible fault this project keeps being bitten by."""
    endpoint = tmp_path / "streaming.json"
    alive = answering(monkeypatch)

    with caplog.at_level(logging.INFO):
        service = RecordingService(
            build_settings(tmp_path),
            spawn=spawn_fake,
            endpoint_path=endpoint,
            source_check_interval=0.0,
            source_settle_seconds=30.0,
        )
        service.run_once(now=1000.0)
        said_at_first = " ".join(r.getMessage() for r in caplog.records)
        assert "thermal" in said_at_first and "camera" in said_at_first

        caplog.clear()
        write_endpoint(endpoint)
        alive["yes"] = True
        service.run_once(now=1100.0)
        service.run_once(now=1200.0)
        said = " ".join(r.getMessage() for r in caplog.records)

    assert "thermal" in said, said
    assert "streaming server" in said, said
    service.stop()


def test_status_says_where_each_stream_is_being_read_from(tmp_path, monkeypatch):
    endpoint = write_endpoint(tmp_path / "streaming.json")
    alive = answering(monkeypatch)
    alive["yes"] = True
    service = RecordingService(
        build_settings(tmp_path),
        spawn=spawn_fake,
        endpoint_path=endpoint,
        source_check_interval=0.0,
    )
    service.run_once(now=1000.0)
    status = service.status(now=1000.0)
    assert status["streams"][0]["source"] == "local"
    assert status["streams"][0]["source_url"] == "rtsp://127.0.0.1:8554/thermal"
    assert status["link_doubled"] == []
    assert status["on_camera"] == 0
    service.stop()


# --------------------------------------------------------------------------
# The clock, which is typed in by a person on a machine with no NTP.
#
# Deleting footage is irreversible and this system exists to keep it, so every
# judgement here is biased towards keeping too much rather than too little.
# --------------------------------------------------------------------------


def _archive(tmp_path, names, size=2000):
    directory = tmp_path / "recordings" / "thermal"
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for offset, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"x" * size)
        os.utime(path, (100.0 + offset, 100.0 + offset))
        written.append(path)
    return directory, written


THREE_SEGMENTS = [
    "2026-08-07_10-00-00.mp4",
    "2026-08-07_10-05-00.mp4",
    "2026-08-07_10-10-00.mp4",
]
YEAR = 365 * 86400.0


def test_a_date_set_a_year_forward_does_not_delete_the_archive(tmp_path, caplog):
    """The operator asked for thirty days and then typed the year wrong. There
    is no undo, no confirmation, and no NTP to contradict them."""
    settings = build_settings(tmp_path, retention_days=30)
    settings.storage.budget_enabled = False
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    base = _segment_epoch(THREE_SEGMENTS[0])
    _directory, files = _archive(tmp_path, THREE_SEGMENTS)
    for step in range(4):  # a few honest passes, so the clock comes to be believed
        service.run_once(now=base + 10 + step)
    assert service.index.all(), "nothing was indexed, so this would prove nothing"

    with caplog.at_level(logging.WARNING):
        service.run_once(now=base + 10 + YEAR)

    assert [p for p in files if p.exists()] == files, (
        "a mistyped year deleted every recording on the machine"
    )
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "clock" in said, said
    assert service.status()["retention_declined"], "nothing said retention had declined"
    service.stop()


def test_the_disk_budget_is_reclaimed_whatever_the_clock_says(tmp_path):
    """The budget rule is separate from the age rule and must keep working: a
    full disk still has to be reclaimed, and keeping everything until the disk
    fills is its own failure."""
    settings = build_settings(tmp_path, budget_gb=3000 / 1024**3, retention_days=30)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    base = _segment_epoch(THREE_SEGMENTS[0])
    _directory, files = _archive(tmp_path, THREE_SEGMENTS)
    service.run_once(now=base + 10)
    service.run_once(now=base + 10 - 400 * 86400.0)  # the clock has gone mad
    assert not files[0].exists(), "the budget was not enforced"
    service.stop()


def test_a_clock_that_moved_makes_the_index_check_what_is_really_on_disk(tmp_path):
    """A backwards jump is how ffmpeg comes to reopen a name it already used.
    Whatever it truncated, the catalogue must stop describing the old contents."""
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake, retention_interval=0.0)
    base = _segment_epoch(THREE_SEGMENTS[0])
    _directory, files = _archive(tmp_path, THREE_SEGMENTS[:2])
    service.run_once(now=base + 10)
    indexed = {os.path.basename(s.path): s for s in service.index.all()}
    assert indexed[THREE_SEGMENTS[0]].size_bytes == 2000

    # ffmpeg reopened the name and wrote something else into it.
    files[0].write_bytes(b"y" * 99)
    os.utime(files[0], (100.0, 100.0))
    service.run_once(now=base + 10 - 400 * 86400.0)

    corrected = {os.path.basename(s.path): s for s in service.index.all()}
    assert corrected[THREE_SEGMENTS[0]].size_bytes == 99, (
        "the index still describes contents the file no longer has"
    )
    service.stop()


# --------------------------------------------------------------------------
# One sqlite error used to end indexing and retention for the life of the
# process.
#
# The index is opened once and was never reopened. A drive that blips - a USB
# reseat, a share that drops with the radio link - kills the connection for
# good: ffmpeg goes on writing, the console goes on saying "recording" because
# it reads the folder and not the catalogue, nothing is ever deleted again, and
# the end state is a full disk on a machine that reported itself healthy the
# whole way there.
# --------------------------------------------------------------------------


def _segment(directory, name, size=2048, mtime=100.0):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_a_dead_index_connection_is_reopened_and_indexing_resumes(tmp_path):
    """The connection is gone, not the database. Nothing reopened it."""
    service = RecordingService(
        build_settings(tmp_path), spawn=spawn_fake, retention_interval=0.0
    )
    service.run_once(now=1000.0)
    directory = tmp_path / "recordings" / "thermal"
    _segment(directory, "2026-08-07_10-00-00.mp4", mtime=100.0)
    _segment(directory, "2026-08-07_10-05-00.mp4", mtime=400.0)
    service.run_once(now=1000.0)
    assert len(service.index.all()) == 1, "nothing was indexed, so this proves nothing"

    service.index._connection.close()  # the drive blipped
    _segment(directory, "2026-08-07_10-10-00.mp4", mtime=700.0)
    for step in range(8):
        service.run_once(now=2000.0 + step)

    indexed = sorted(os.path.basename(s.path) for s in service.index.all())
    assert indexed == ["2026-08-07_10-00-00.mp4", "2026-08-07_10-05-00.mp4"], (
        "one sqlite error stopped indexing for the life of the process"
    )
    assert service.status(now=2100.0)["index_broken"] is None
    service.stop()


def test_reopening_the_index_neither_loses_nor_double_counts_what_was_indexed(tmp_path):
    """A reopen that forgot what was already there would index every segment a
    second time, and the storage budget is measured from those rows."""
    service = RecordingService(
        build_settings(tmp_path), spawn=spawn_fake, retention_interval=0.0
    )
    service.run_once(now=1000.0)
    directory = tmp_path / "recordings" / "thermal"
    for offset, name in enumerate(THREE_SEGMENTS):
        _segment(directory, name, mtime=100.0 + offset)
    service.run_once(now=1000.0)
    before = {s.path: (s.start, s.end, s.size_bytes) for s in service.index.all()}
    assert len(before) == 2, "two of the three are closed; the newest is ffmpeg's"

    # One of them is no longer on disk - moved by hand, or on a stream that has
    # since been renamed. Its row is the only record that it was ever recorded,
    # and a reopen that started from an empty catalogue would lose it silently.
    (directory / THREE_SEGMENTS[0]).unlink()

    service.index._connection.close()
    for step in range(8):
        service.run_once(now=2000.0 + step)

    after = service.index.all()
    assert len(after) == len({s.path for s in after}), "a segment was indexed twice"
    assert len(after) == len(before), (
        f"the catalogue held {len(before)} segments before the reopen and "
        f"{len(after)} after it"
    )
    assert {s.path: (s.start, s.end, s.size_bytes) for s in after} == before, (
        "segments indexed before the reopen were lost or rewritten"
    )
    assert service.status(now=2100.0)["used_bytes"] == sum(s.size_bytes for s in after)
    service.stop()


def test_a_reopen_starts_from_what_the_catalogue_actually_holds(tmp_path):
    """The memo of what has been indexed is in memory; the rows are the record.

    A database file that comes back without those rows - deleted, restored from
    an older copy, replaced when a share reconnected - leaves every one of those
    segments indexed nowhere for ever, because each later pass skips a path the
    memo says it has already done.
    """
    service = RecordingService(
        build_settings(tmp_path), spawn=spawn_fake, retention_interval=0.0
    )
    service.run_once(now=1000.0)
    directory = tmp_path / "recordings" / "thermal"
    for offset, name in enumerate(THREE_SEGMENTS):
        _segment(directory, name, mtime=100.0 + offset)
    service.run_once(now=1000.0)
    assert len(service.index.all()) == 2

    service.index.close()
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / "recordings" / f"segments.db{suffix}").unlink(missing_ok=True)
    for step in range(8):
        service.run_once(now=2000.0 + step)

    indexed = sorted(os.path.basename(s.path) for s in service.index.all())
    assert indexed == THREE_SEGMENTS[:2], (
        "segments the catalogue no longer holds were never offered to it again"
    )
    service.stop()


def test_an_index_that_is_genuinely_broken_is_given_up_on_and_says_so(tmp_path, monkeypatch, caplog):
    """A corrupt file cannot be reopened, and trying every five seconds for
    months is its own fault: it is the reopen that never works, plus a line
    about it, once per pass, in a Logs tab that holds five hundred."""
    service = RecordingService(
        build_settings(tmp_path), spawn=spawn_fake, retention_interval=0.0
    )
    service.run_once(now=1000.0)
    _segment(tmp_path / "recordings" / "thermal", "2026-08-07_10-00-00.mp4")
    _segment(tmp_path / "recordings" / "thermal", "2026-08-07_10-05-00.mp4", mtime=400.0)

    opened: list = []
    real_index = record_main_module.SegmentIndex

    def counted(path):
        opened.append(str(path))
        return real_index(path)

    monkeypatch.setattr(record_main_module, "SegmentIndex", counted)
    service.index.close()
    (tmp_path / "recordings" / "segments.db").write_bytes(b"this is not a database")

    with caplog.at_level(logging.ERROR):
        for step in range(60):
            service.run_once(now=2000.0 + step)

    status = service.status(now=3000.0)
    assert status["index_broken"], "the console is not told the catalogue is gone"
    assert status["healthy"] is False
    assert status["used_bytes"] is None, "an unreadable catalogue must not read as empty"
    assert len(opened) <= record_main_module.INDEX_REOPEN_LIMIT, (
        f"a broken database was reopened {len(opened)} times in 60 passes"
    )
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "catalogue" in said or "index" in said, said
    service.stop()


def test_recording_continues_while_the_index_is_unusable(tmp_path):
    """Footage on disk with no index row can be recovered later. Footage that
    was never written cannot.

    Including the hole a broken catalogue used to cut in the footage every two
    segment lengths: the stall check reads the clock that indexing stamps, so a
    catalogue that would not take a row made every stream look as though ffmpeg
    had stopped producing, and it was killed and restarted for ever.
    """
    spawns: list = []

    def counting_spawn(command, log_path=None):
        spawns.append(command)
        return FakeProcess()

    service = RecordingService(
        build_settings(tmp_path), spawn=counting_spawn, retention_interval=0.0
    )
    service.run_once(now=1000.0)
    _segment(tmp_path / "recordings" / "thermal", "2026-08-07_10-00-00.mp4")
    _segment(tmp_path / "recordings" / "thermal", "2026-08-07_10-05-00.mp4", mtime=400.0)
    service.index.close()
    (tmp_path / "recordings" / "segments.db").write_bytes(b"this is not a database")
    started_with = len(spawns)

    for step in range(40):
        service.run_once(now=2000.0 + step)

    status = service.status(now=3000.0)
    assert status["index_broken"], "this proves nothing unless the index really broke"
    assert status["streams"][0]["running"] is True, "recording stopped with the index"
    assert len(spawns) == started_with, (
        "ffmpeg was restarted while it was recording perfectly well; a broken "
        "catalogue must not cut the footage"
    )
    assert service.status(now=3000.0)["stall_restarts"] == 0
    service.stop()


# --------------------------------------------------------------------------
# What the recorder knows, published where something can read it.
#
# `status()` was computed every pass and printed only by --once, which nothing
# runs. The console derives everything from the folder, which is the right
# primary signal and cannot say "ffmpeg is being held back on ch2", "this
# stream is crossing the radio link twice" or "retention refused to run".
# Worse, a recorder started by the logon task writes its stdout to a file under
# bin\logs and not to the Logs tab, so on the machine's ordinary boot path a
# file beside the recordings is the ONLY channel anything it learns has.
# --------------------------------------------------------------------------


def read_report(tmp_path, folder="recordings"):
    return json.loads(
        (tmp_path / folder / record_main_module.STATUS_FILENAME).read_text(
            encoding="utf-8"
        )
    )


def test_the_recorder_publishes_what_it_knows_beside_the_recordings(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=1000.0)
    service.write_status(interval=5.0)

    report = read_report(tmp_path)
    assert [s["name"] for s in report["streams"]] == ["thermal"]
    for field in ("held_back", "stalled", "running", "source"):
        assert field in report["streams"][0], field
    for field in (
        "healthy",
        "link_doubled",
        "on_camera",
        "retention_declined",
        "index_broken",
        "stuck_deletions",
        "empty_segments",
        "written_at",
        "interval",
    ):
        assert field in report, field
    assert report["interval"] == 5.0
    service.stop()


def test_a_report_from_a_recorder_that_died_an_hour_ago_reads_as_unknown(tmp_path):
    """Repeating it is worse than saying nothing, because it is the answer the
    operator would have wanted to hear."""
    fresh = {"written_at": 1000.0, "interval": 5.0}
    assert record_main_module.status_fresh(fresh, now=1002.0) is True
    assert record_main_module.status_fresh(fresh, now=1000.0 + 3600) is False
    # A laptop whose date was typed in wrong, not a recorder that is well.
    assert record_main_module.status_fresh(fresh, now=1000.0 - 3600) is False
    assert record_main_module.status_fresh({}, now=1000.0) is False
    assert record_main_module.status_fresh({"written_at": "soon"}, now=1000.0) is False


def test_how_stale_is_stale_follows_the_interval_it_was_told(tmp_path):
    """A recorder told to report every two minutes is not a wedged recorder,
    and a hard-coded window would call it one for ever."""
    slow = {"written_at": 1000.0, "interval": 120.0}
    assert record_main_module.status_fresh(slow, now=1000.0 + 200) is True
    assert record_main_module.status_fresh(slow, now=1000.0 + 2000) is False
    brisk = {"written_at": 1000.0, "interval": 1.0}
    assert record_main_module.status_fresh(brisk, now=1000.0 + 200) is False


def test_the_report_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """A console reading at the wrong moment must get the whole of the last
    report, never half of this one - which it would read as a recorder with no
    streams at all."""
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=1000.0)
    service.write_status(interval=5.0)
    good = read_report(tmp_path)

    # Something goes wrong after the writing has begun. A plain write has
    # truncated the destination by this point and leaves nothing behind it.
    def refuse(*args, **kwargs):
        raise ValueError("the report could not be turned into JSON")

    monkeypatch.setattr(record_main_module.json, "dumps", refuse)
    service.write_status(interval=5.0)  # must not raise
    assert read_report(tmp_path) == good, "a failed write damaged the last report"
    leftovers = list((tmp_path / "recordings").glob("*.tmp"))
    assert leftovers == [], f"a half-written report was left behind: {leftovers}"
    service.stop()


def test_the_recorder_takes_its_report_down_when_it_stops(tmp_path):
    """A recorder that stopped cleanly and left its last report behind would
    have anything reading it believe a dead process's words until the staleness
    window ran out."""
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=1000.0)
    service.write_status(interval=5.0)
    assert (tmp_path / "recordings" / record_main_module.STATUS_FILENAME).exists()
    service.stop()
    assert not (tmp_path / "recordings" / record_main_module.STATUS_FILENAME).exists()


def test_the_running_loop_publishes_before_it_waits(tmp_path, monkeypatch):
    """Nothing calls write_status but the loop, so a loop that did not call it
    would leave every one of these facts inside the process exactly as before."""
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    seen: list = []

    def stop_the_loop(_seconds):
        seen.append(read_report(tmp_path))
        raise KeyboardInterrupt

    monkeypatch.setattr(record_main_module.time, "sleep", stop_the_loop)
    service.run_forever(interval=5.0)

    assert seen, "the loop never slept, so this proves nothing"
    assert seen[0]["interval"] == 5.0
    assert [s["name"] for s in seen[0]["streams"]] == ["thermal"]


def test_the_report_names_the_streams_crossing_the_link_twice(tmp_path, monkeypatch):
    """The recorder learned this and it died inside the process. It is the very
    fault the whole architecture exists to prevent, and nothing said it."""
    endpoint = write_endpoint(tmp_path / "streaming.json", name="somethingelse")
    alive = answering(monkeypatch)
    alive["yes"] = True
    service = RecordingService(
        build_settings(tmp_path),
        spawn=spawn_fake,
        endpoint_path=endpoint,
        source_check_interval=0.0,
    )
    service.run_once(now=1000.0)
    service.write_status(interval=5.0)

    report = read_report(tmp_path)
    assert report["link_doubled"] == ["thermal"]
    assert report["on_camera"] == 1
    assert report["streams"][0]["source"] == "camera"
    service.stop()


def test_no_password_reaches_the_status_file(tmp_path):
    """The camera's address carries its password, and this file is read by
    anything on the machine and copied into any report of a fault."""
    settings = build_settings(tmp_path)
    settings.camera.streams[0].url = "rtsp://admin:hunter2@192.0.2.10/thermal"
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once(now=1000.0)
    service.write_status(interval=5.0)

    raw = (tmp_path / "recordings" / record_main_module.STATUS_FILENAME).read_text(
        encoding="utf-8"
    )
    assert "hunter2" not in raw, raw
    assert "admin" in raw, "which account is half the diagnosis of a 401"
    service.stop()


def test_the_report_does_not_stay_behind_in_a_folder_that_was_left(tmp_path):
    """A recording.json left in the old archive would go on being read as this
    recorder's current state by anything pointed at that folder."""
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=1000.0)
    service.write_status(interval=5.0)
    old = tmp_path / "recordings" / record_main_module.STATUS_FILENAME
    assert old.exists()

    moved = build_settings(tmp_path)
    moved.storage.root = tmp_path / "elsewhere"
    service._apply_settings(moved, now=1100.0)
    service.write_status(interval=5.0)

    assert not old.exists(), "the report was left behind in the folder that was left"
    assert (tmp_path / "elsewhere" / record_main_module.STATUS_FILENAME).exists()
    service.stop()
