import json
import logging
import os
import subprocess
import time

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
        assert [(e.stream, e.started) for e in kept] == [
            ("thermal", oldest + 600),
            ("visible", oldest - 60),
        ], "only the thermal events under the deleted footage should have gone"
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
