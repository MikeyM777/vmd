import os
import time

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
    assert len(restarted.index.all()) == 1
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

    def refusing_apply_plan(plan, index, unlink=None):
        return real_apply_plan(plan, index, unlink=refuse)

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
        lambda plan, index, unlink=None: real_apply_plan(plan, index, unlink=refuse),
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
