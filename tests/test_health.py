import os

from vmd.record_main import RecordingService
from vmd.settings import Settings, StreamSettings


class FakeProcess:
    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def spawn_fake(command, log_path=None):
    return FakeProcess()


def build_settings(tmp_path):
    settings = Settings()
    settings.camera.streams = [StreamSettings(name="thermal", url="rtsp://example/thermal")]
    settings.storage.root = tmp_path / "recordings"
    settings.storage.segment_seconds = 10
    return settings


def write_segment(directory, name, mtime):
    path = directory / name
    path.write_bytes(b"x" * 2048)
    os.utime(path, (mtime, mtime))
    return path


def test_orphaned_segments_on_disk_are_adopted(tmp_path):
    # A stream that was renamed or disabled leaves recordings behind. They must still
    # be counted and eventually deleted, or they occupy the budget forever.
    settings = build_settings(tmp_path)
    root = tmp_path / "recordings"
    orphan_dir = root / "an_old_stream_name"
    orphan_dir.mkdir(parents=True)
    orphan = orphan_dir / "2026-08-07_09-00-00.mp4"
    orphan.write_bytes(b"x" * 4096)

    service = RecordingService(settings, spawn=spawn_fake)

    indexed = [s.path for s in service.index.all()]
    assert str(orphan) in indexed
    assert service.index.total_bytes() == 4096
    service.stop()


def test_adoption_ignores_directories_a_recorder_owns(tmp_path):
    # The active stream's newest file is the one ffmpeg still has open. Adopting it
    # would put a live recording into the index, where retention could delete it.
    settings = build_settings(tmp_path)
    root = tmp_path / "recordings"
    active = root / "thermal"
    active.mkdir(parents=True)
    in_progress = active / "2026-08-07_09-00-00.mp4"
    in_progress.write_bytes(b"x" * 4096)

    service = RecordingService(settings, spawn=spawn_fake)

    assert [s.path for s in service.index.all()] == []
    assert str(in_progress) not in service._seen
    service.stop()


def test_a_stream_producing_segments_is_not_stalled(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    assert service.stalled_streams(now=115.0) == []
    service.stop()


def test_a_stream_with_no_new_segment_is_stalled(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    # Twice the 10-second segment length has passed with nothing new.
    assert service.stalled_streams(now=140.0) == ["thermal"]
    service.stop()


def test_a_stream_is_not_stalled_before_it_has_had_time(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    # No segments yet, but it has only just started - not a stall.
    assert service.stalled_streams(now=105.0) == []
    service.stop()


def test_a_service_with_no_streams_is_not_healthy(tmp_path):
    # `all([])` is True, so an empty stream list would otherwise report healthy while
    # recording nothing at all.
    settings = build_settings(tmp_path)
    settings.camera.streams = []
    service = RecordingService(settings, spawn=spawn_fake)
    assert service.status()["healthy"] is False
    service.stop()


def test_status_reports_a_stalled_stream_as_unhealthy(tmp_path):
    service = RecordingService(build_settings(tmp_path), spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    status = service.status(now=140.0)
    assert status["streams"][0]["stalled"] is True
    assert status["healthy"] is False
    service.stop()


def test_repeated_identical_start_failures_are_logged_once(caplog):
    from vmd.supervisor import Managed, Supervisor

    class Broken:
        running = False

        def start(self):
            raise RuntimeError("cannot start")

        def stop(self):
            pass

    clock = {"now": 0.0}
    supervisor = Supervisor(
        [Managed(name="broken", service=Broken())],
        clock=lambda: clock["now"],
        restart_delay=1.0,
    )
    with caplog.at_level("WARNING"):
        for _ in range(50):
            supervisor.tick()
            clock["now"] += 2.0

    tracebacks = [r for r in caplog.records if r.exc_info]
    # A stream that is broken for a month must not write a traceback every two
    # seconds; that alone would fill the disk this system exists to manage.
    assert len(tracebacks) <= 2
    assert supervisor.failures["broken"] == 50


def test_a_stalled_stream_is_restarted(tmp_path):
    # Detection alone leaves the stream dead. The recorder must actually be stopped
    # so the supervisor's normal restart path can bring it back.
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)

    assert service.status(now=115.0)["stall_restarts"] == 0
    service.run_once(now=200.0)  # long past 2x segment_seconds with nothing new
    assert service.status(now=200.0)["stall_restarts"] == 1
    service.stop()


def test_a_restarted_stall_gets_a_fresh_grace_period(tmp_path):
    settings = build_settings(tmp_path)
    service = RecordingService(settings, spawn=spawn_fake)
    service.run_once(now=100.0)
    directory = tmp_path / "recordings" / "thermal"
    write_segment(directory, "2026-08-07_10-00-00.mp4", 100.0)
    write_segment(directory, "2026-08-07_10-00-10.mp4", 110.0)
    service.run_once(now=115.0)
    service.run_once(now=200.0)  # restarted here
    # Immediately after the restart it must not be judged stalled again.
    assert service.stalled_streams(now=205.0) == []
    service.stop()


def test_a_flapping_service_still_reaches_the_log_throttle():
    # Succeeding briefly then failing must not reset the throttle, or a stream that
    # flaps writes a full traceback forever.
    from vmd.supervisor import Managed, Supervisor

    class Flapping:
        def __init__(self):
            self.running = False
            self.attempts = 0

        def start(self):
            self.attempts += 1
            if self.attempts % 2 == 0:
                self.running = True  # comes up briefly
                return
            raise RuntimeError("cannot start")

        def stop(self):
            self.running = False

    service = Flapping()
    clock = {"now": 0.0}
    supervisor = Supervisor(
        [Managed(name="flappy", service=service)],
        clock=lambda: clock["now"],
        restart_delay=1.0,
        stable_after=60.0,
    )
    for _ in range(40):
        service.running = False  # dies again immediately after each success
        supervisor.tick()
        clock["now"] += 2.0

    # It never stays up for stable_after, so failures must accumulate rather than reset.
    assert supervisor.failures["flappy"] > 2
