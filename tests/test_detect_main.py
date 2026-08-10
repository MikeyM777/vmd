"""The detector process: one thread per detected stream, and a clean exit."""

import json
import socket
import time

import numpy as np

from vmd.detect.events import EventStore
from vmd.detect.motion import Box
from vmd.detect.pipeline import Detection
from vmd.detect.tracking import Track
from vmd.detect_main import DetectionService, detected_streams, main, parse_args
from vmd.settings import (
    CameraSettings,
    IgnoreRegion,
    DetectionSettings,
    Settings,
    StorageSettings,
    StreamSettings,
    save_settings,
)


def build_settings(tmp_path, thermal_detect=True, visible_detect=False, detection=None):
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(
                    name="thermal",
                    url="rtsp://10.0.0.2/thermal",
                    detect=thermal_detect,
                ),
                StreamSettings(
                    name="visible",
                    url="rtsp://10.0.0.2/visible",
                    detect=visible_detect,
                ),
            ],
        ),
        storage=StorageSettings(root=tmp_path / "recordings"),
        detection=detection or DetectionSettings(),
    )


def frame():
    return np.zeros((8, 8), dtype=np.uint8)


class FakeCapture:
    def __init__(self, frames=4, stop_after=None):
        self.remaining = frames
        self.released = False
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.remaining > 0:
            self.remaining -= 1
            return True, frame()
        time.sleep(0.001)  # a stream that has run dry must not spin the test
        return False, None

    def release(self):
        self.released = True


class StubPipeline:
    """Emits one confirmed track on frame 1 and nothing else."""

    def __init__(self, emit_on=1):
        self.emit_on = emit_on
        self.frames_suppressed = 0

    def feed(self, image, frame_index):
        if frame_index != self.emit_on:
            return []
        track = Track(id=1)
        track.observe(Box(10, 10, 5, 5), 0)
        track.observe(Box(40, 10, 5, 5), frame_index)
        return [Detection(track=track, box=track.box, frame_index=frame_index)]

    def reset(self):
        pass


def service_for(tmp_path, settings=None, **kwargs):
    kwargs.setdefault("open_capture", lambda url: FakeCapture())
    kwargs.setdefault("pipeline_factory", lambda config: StubPipeline())
    return DetectionService(settings or build_settings(tmp_path), **kwargs)


# --------------------------------------------------------------------------
# Which streams are detected
# --------------------------------------------------------------------------


def test_only_streams_with_detection_enabled_get_a_detector(tmp_path):
    service = service_for(tmp_path)
    try:
        assert [d.stream for d in service.detectors] == ["thermal"]
    finally:
        service.stop()


def test_a_disabled_stream_is_never_detected(tmp_path):
    """A stream the operator turned off is off, whatever the detection tick says."""
    settings = build_settings(tmp_path, thermal_detect=True)
    settings.camera.streams[0].enabled = False
    assert detected_streams(settings) == []


def test_the_master_switch_turns_everything_off(tmp_path):
    settings = build_settings(tmp_path, thermal_detect=True, visible_detect=True)
    settings.detection = DetectionSettings(enabled=False)
    assert detected_streams(settings) == []


def test_both_streams_can_be_detected(tmp_path):
    settings = build_settings(tmp_path, thermal_detect=True, visible_detect=True)
    service = service_for(tmp_path, settings)
    try:
        assert [d.stream for d in service.detectors] == ["thermal", "visible"]
    finally:
        service.stop()


def test_the_painted_ignore_regions_reach_the_detector(tmp_path):
    """The answer to one specific swaying tree has to survive the journey from
    the settings file to the thing looking at the tree."""
    settings = build_settings(tmp_path)
    settings.camera.streams[0].ignore_regions = [IgnoreRegion(x=1, y=2, w=3, h=4)]
    service = service_for(tmp_path, settings)
    try:
        assert service.detectors[0].ignore_regions == [(1, 2, 3, 4)]
    finally:
        service.stop()


def test_each_detector_gets_its_stream_s_own_settings(tmp_path):
    settings = build_settings(tmp_path, thermal_detect=True, visible_detect=True)
    settings.camera.streams[0].sensitivity = "high"
    settings.camera.streams[1].sensitivity = "low"
    settings.camera.streams[1].horizon_y = 120
    service = service_for(tmp_path, settings)
    try:
        assert service.detectors[0].config.sensitivity == "high"
        assert service.detectors[1].config.sensitivity == "low"
        assert service.detectors[1].config.horizon_y == 120
    finally:
        service.stop()


# --------------------------------------------------------------------------
# Where the frames come from
# --------------------------------------------------------------------------


def test_it_reads_from_the_local_streaming_server_when_it_is_running(tmp_path):
    """The camera is already being pulled once. One more local consumer of
    go2rtc costs the radio link nothing; a second pull across the link costs it
    everything."""
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
    try:
        service = service_for(tmp_path, endpoint_path=endpoint)
        try:
            assert service.detectors[0].url == f"rtsp://127.0.0.1:{port}/thermal"
        finally:
            service.stop()
    finally:
        listener.close()


def test_it_falls_back_to_the_camera_when_the_streaming_server_is_gone(tmp_path):
    endpoint = tmp_path / "streaming.json"
    endpoint.write_text(
        json.dumps({"api_port": 1984, "rtsp_port": 59999, "streams": {"thermal": "rtsp://x"}}),
        encoding="utf-8",
    )
    service = service_for(tmp_path, endpoint_path=endpoint)
    try:
        assert service.detectors[0].url == "rtsp://10.0.0.2/thermal"
    finally:
        service.stop()


# --------------------------------------------------------------------------
# Where the events go
# --------------------------------------------------------------------------


def test_events_go_beside_the_segments(tmp_path):
    service = service_for(tmp_path)
    try:
        assert service.events_path == tmp_path / "recordings" / "events.db"
    finally:
        service.stop()


def test_an_event_found_on_a_detector_thread_reaches_the_file(tmp_path):
    """Each thread opens its own connection to events.db: a sqlite connection
    belongs to the thread that made it, and this is the test that proves the
    threads are not sharing one."""
    service = service_for(tmp_path)
    service.start()
    deadline = time.time() + 5.0
    try:
        while time.time() < deadline and service.status()["streams"][0]["events"] < 1:
            time.sleep(0.01)
    finally:
        service.stop()

    store = EventStore(service.events_path)
    try:
        events = store.recent()
        assert len(events) == 1
        assert events[0].stream == "thermal"
        assert events[0].label == "", "an unnamed track is still an event"
    finally:
        store.close()


def test_the_service_starts_a_thread_for_each_stream(tmp_path):
    settings = build_settings(tmp_path, thermal_detect=True, visible_detect=True)
    service = service_for(tmp_path, settings)
    service.start()
    try:
        assert len(service.threads) == 2
        assert all(thread.is_alive() for thread in service.threads)
    finally:
        service.stop()
    assert not any(thread.is_alive() for thread in service.threads)


def test_stopping_twice_is_harmless(tmp_path):
    service = service_for(tmp_path)
    service.start()
    service.stop()
    service.stop()


# --------------------------------------------------------------------------
# Reporting, per stream
# --------------------------------------------------------------------------


def test_one_unreachable_stream_does_not_read_as_total_failure(tmp_path):
    """Detection continuing on the thermal while the visible is unreachable is
    normal, and must not look like the detector is down."""
    settings = build_settings(tmp_path, thermal_detect=True, visible_detect=True)

    def open_capture(url):
        return None if url.endswith("visible") else FakeCapture()

    service = service_for(tmp_path, settings, open_capture=open_capture)
    try:
        for detector in service.detectors:
            detector.step()
        status = service.status()
        by_name = {s["stream"]: s for s in status["streams"]}
        assert by_name["thermal"]["opened"] is True
        assert by_name["visible"]["opened"] is False
        assert "could not be opened" in by_name["visible"]["reason"]
        assert status["detecting"] == 1
    finally:
        service.stop()


def test_status_says_where_the_events_are(tmp_path):
    service = service_for(tmp_path)
    try:
        assert service.status()["events_db"] == str(service.events_path)
    finally:
        service.stop()


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_parse_args_defaults():
    args = parse_args([])
    assert args.settings == "settings.json"
    assert args.once is False


def test_main_says_so_and_exits_cleanly_when_nothing_is_detected(tmp_path, capsys, monkeypatch):
    """Nothing to do is not a failure, and it must not spin.

    "Must not spin" is asserted by proving the service was never started, not
    by waiting to see whether it comes back - a test that hangs when it fails
    is not a test.
    """
    started = []
    monkeypatch.setattr(
        DetectionService, "run_forever", lambda self, interval=5.0: started.append(interval)
    )
    path = tmp_path / "settings.json"
    save_settings(build_settings(tmp_path, thermal_detect=False), path)

    assert main(["--settings", str(path)]) == 0
    assert started == [], "with nothing to detect, nothing may be started"
    assert "detection" in capsys.readouterr().out.lower()


def test_main_reports_a_broken_settings_file_without_crashing(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert main(["--settings", str(path)]) == 1


def test_main_runs_a_single_pass(tmp_path, capsys):
    """--once must complete and clean up even though the source is unusable.

    The source is a local file rather than an address, so this exercises the
    real OpenCV opener without waiting out an RTSP timeout.
    """
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"not really a video")
    settings = build_settings(tmp_path)
    settings.camera.streams[0].url = str(source)
    path = tmp_path / "settings.json"
    save_settings(settings, path)

    assert main(["--settings", str(path), "--once"]) == 0
    assert "thermal" in capsys.readouterr().out
    assert (tmp_path / "recordings" / "events.db").exists()


# --------------------------------------------------------------------------
# Shutting down
# --------------------------------------------------------------------------


def test_the_termination_handler_stops_the_service(tmp_path):
    """taskkill and Ctrl-C both arrive here, and both must end the process
    rather than leave sqlite connections open behind a dead console."""
    service = service_for(tmp_path)
    service.start()
    try:
        from vmd.detect_main import install_signal_handlers

        handler = install_signal_handlers(service)
        handler(15, None)  # SIGTERM
        service.wait(timeout=5.0)
        assert service.stopping is True
        assert all(detector.stopped for detector in service.detectors)
    finally:
        service.stop()


def test_run_forever_returns_once_it_is_stopped(tmp_path):
    """Run on a daemon thread, so a loop that never ends fails this test in
    five seconds instead of hanging the suite for ever."""
    import threading

    service = service_for(tmp_path)
    returned = threading.Event()

    def go():
        service.run_forever(interval=0.02)
        returned.set()

    threading.Thread(target=go, daemon=True).start()
    threading.Timer(0.2, service.stop).start()

    assert returned.wait(5.0), "run_forever must return once the service is stopped"
    assert service.stopping is True
