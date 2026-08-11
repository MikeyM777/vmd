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


def test_the_painted_ignore_mask_reaches_the_pipeline_that_reads_it(tmp_path):
    """Reaching the detector is not the same as reaching the thing that looks.

    The mask can only be painted once a frame has said how big a frame is, so
    the runner paints it onto its config when the first frame arrives - and the
    pipeline is the object that consults it. If the two are handed different
    config objects the operator paints out a swaying tree and the tree goes on
    alarming, with nothing anywhere saying why.
    """
    settings = build_settings(tmp_path)
    settings.camera.streams[0].ignore_regions = [IgnoreRegion(x=0, y=0, w=4, h=4)]
    # A real pipeline: the stub has no config to hand a mask to.
    service = DetectionService(settings, open_capture=lambda url: FakeCapture())
    try:
        detector = service.detectors[0]
        assert detector.step() is True  # one real frame, which paints the mask
        mask = detector.pipeline.config.ignore_mask
        assert mask is not None, "the pipeline is reading a config nobody painted"
        assert mask.shape == (8, 8)
        assert mask[0, 0] != 0
    finally:
        service.stop()


def test_a_stream_that_opened_and_went_quiet_is_reported_as_stalled(tmp_path, caplog):
    """An open capture that has stopped delivering is not "detecting".

    `read()` on a link that dropped without closing blocks inside ffmpeg, and
    while it does nothing on that thread runs - so the capture stays open, the
    reason stays empty, and the console counts the stream among the ones being
    watched. It is the one failure that looks exactly like a quiet perimeter.
    """
    from vmd.detect_main import STALLED_AFTER_SECONDS

    service = service_for(tmp_path)
    try:
        detector = service.detectors[0]
        detector.step()
        assert service.status()["stalled"] == 0

        # The read went in and did not come out.
        detector._last_frame_at = time.time() - (STALLED_AFTER_SECONDS + 5.0)
        status = service.status()
        assert status["stalled"] == 1
        assert status["detecting"] == 1, "it is still open, which is the trap"

        with caplog.at_level("WARNING", logger="vmd.detect_main"):
            service._log_state_changes()
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "sent nothing" in said, said
    finally:
        service.stop()


def test_a_stream_too_slow_to_confirm_anything_is_said_out_loud(tmp_path, caplog):
    """A frame rate is not just performance; below a point it is deafness.

    Three of the last five frames is a fifth of a second at 25 fps and fifteen
    seconds at one frame every three. Measured on the owner's own labelled
    footage: 7/8 person spans at 3 fps, 5/8 at 1 fps, 2/8 at 0.33 fps. And the
    control that puts a stream there is this app's own ONVIF re-encode, so
    nothing else would ever connect the two for the operator.
    """
    service = service_for(tmp_path)
    try:
        detector = service.detectors[0]
        detector.step()
        assert service.status()["slow"] == 0  # nothing measured yet

        # A stream arriving at one frame every two seconds.
        detector._frame_order.extend(range(20))
        detector._frame_times.update({index: 1000.0 + index * 2.0 for index in range(20)})
        assert detector.state()["fps"] < 1.0
        assert service.status()["slow"] == 1

        with caplog.at_level("WARNING", logger="vmd.detect_main"):
            service._log_state_changes()
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "frames a second" in said, said
    finally:
        service.stop()


def test_what_the_rejection_rules_threw_away_is_published(tmp_path):
    """The console is another process and cannot ask.

    Ignore mask, horizon, minimum size: each of these deletes real detections
    when it is wrong, and none of them says anything when it does. A count per
    rule, beside the stream it belongs to, is what turns "the detector has been
    quiet since Tuesday" from a mystery into a reading.
    """
    settings = build_settings(tmp_path)
    service = DetectionService(settings, open_capture=lambda url: FakeCapture())
    try:
        detector = service.detectors[0]
        detector.step()
        state = detector.state()
        assert set(state["rejected"]) == {"ignore_mask", "horizon", "too_small", "too_large"}
        assert state["blobs"] >= 0
        assert state["suppressed"] >= 0
        assert "rejected" in service.status()["streams"][0]
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
# Publishing that per-stream state to the console
#
# The console is a different process. Per-stream state that never leaves this
# one is per-stream state the operator never sees, and the console is reduced to
# "detection is running" at process level.
# --------------------------------------------------------------------------


def read_status_file(path, attempts=50):
    """Read the published status, retrying a rename we lost a race with.

    On Windows os.replace fails while another handle is open, so a reader and a
    writer can briefly miss each other. Bounded: this gives up rather than
    waiting for ever.
    """
    for _ in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.01)
    raise AssertionError(f"{path} was never readable")


def test_the_detector_publishes_what_each_stream_is_doing(tmp_path):
    """Beside events.db, the same seam streaming.json already uses."""
    settings = build_settings(tmp_path, thermal_detect=True, visible_detect=True)

    def open_capture(url):
        return None if url.endswith("visible") else FakeCapture()

    service = service_for(tmp_path, settings, open_capture=open_capture)
    try:
        for detector in service.detectors:
            detector.step()
        service.write_status(interval=5.0)
        payload = read_status_file(service.status_path)
    finally:
        service.stop()

    assert service.status_path == tmp_path / "recordings" / "detection.json"
    by_name = {stream["stream"]: stream for stream in payload["streams"]}
    assert by_name["thermal"]["opened"] is True
    assert by_name["visible"]["opened"] is False
    assert "could not be opened" in by_name["visible"]["reason"]
    assert payload["interval"] == 5.0
    assert payload["written_at"] > 0, "without a timestamp the console cannot tell stale from fresh"


def test_the_status_file_is_never_half_written(tmp_path, monkeypatch):
    """A console that read a spliced file would name streams that do not exist.
    Written to a temporary file in the same directory and renamed over the
    destination, so what is read is always the whole of one write."""
    import vmd.detect_main as detect_main

    service = service_for(tmp_path)
    try:
        service.write_status(interval=5.0)
        complete = service.status_path.read_text(encoding="utf-8")
        assert json.loads(complete)["streams"] is not None

        def no_space(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(detect_main.os, "replace", no_space)
        service.write_status(interval=5.0)  # must not raise, and must not damage the file

        assert service.status_path.read_text(encoding="utf-8") == complete
        leftovers = sorted(p.name for p in service.status_path.parent.glob("detection.json.*"))
        assert leftovers == [], f"a failed write must not leave scratch files: {leftovers}"
    finally:
        service.stop()


def test_a_status_file_that_cannot_be_written_does_not_stop_detection(tmp_path, monkeypatch):
    """A full disk is a console that says "unknown". It is not a reason to stop
    watching the perimeter."""
    import vmd.detect_main as detect_main

    service = service_for(tmp_path)
    try:

        def no_space(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(detect_main.tempfile, "mkstemp", no_space)
        service.write_status(interval=5.0)  # no exception escapes
        assert service.status_path.exists() is False
    finally:
        service.stop()


def test_a_running_detector_keeps_its_status_file_fresh(tmp_path):
    """Stale means unknown to the console, so a detector that publishes once and
    never again is the same as one that never published at all.

    Run on a daemon thread: a loop that never writes fails this in five seconds
    rather than hanging the suite.
    """
    import threading

    service = service_for(tmp_path)
    threading.Thread(target=lambda: service.run_forever(interval=0.02), daemon=True).start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not service.status_path.exists():
            time.sleep(0.01)
        assert service.status_path.exists(), "nothing published the detector's state"

        first = read_status_file(service.status_path)["written_at"]
        latest = first
        deadline = time.time() + 5.0
        while time.time() < deadline and latest <= first:
            latest = read_status_file(service.status_path)["written_at"]
            time.sleep(0.01)
        assert latest > first, "a file written once and never again is a file the console ignores"
    finally:
        service.stop()


def test_a_detector_that_stops_takes_its_claim_down_with_it(tmp_path):
    """Leaving the file behind would have the console reading a dead detector's
    last words as current for as long as the staleness window lasts."""
    import threading

    service = service_for(tmp_path)
    finished = threading.Event()

    def go():
        service.run_forever(interval=0.02)
        finished.set()

    threading.Thread(target=go, daemon=True).start()
    deadline = time.time() + 5.0
    while time.time() < deadline and not service.status_path.exists():
        time.sleep(0.01)
    assert service.status_path.exists()

    service.stop()
    assert finished.wait(5.0), "run_forever must return once the service is stopped"
    assert service.status_path.exists() is False


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
