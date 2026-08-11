"""One stream, decoded and fed. Nothing here touches OpenCV or a real socket."""

import numpy as np
import pytest

from vmd.detect.events import EventStore
from vmd.detect.motion import Box
from vmd.detect.pipeline import Detection
from vmd.detect.runner import StreamDetector
from vmd.detect.tracking import Track


def frame():
    return np.zeros((8, 8), dtype=np.uint8)


def detection_at(frame_index, boxes=((10, 20, 6, 7), (40, 20, 6, 7)), first_frame=0):
    """A confirmed track that started at `first_frame` and has moved since."""
    track = Track(id=7)
    for offset, box in enumerate(boxes):
        track.observe(Box(*box), first_frame + offset)
    track.last_frame = frame_index
    return Detection(track=track, box=track.box, frame_index=frame_index)


class StubPipeline:
    """Stands in for DetectionPipeline: a script of what each frame returns."""

    def __init__(self, script=None):
        self.script = script or {}
        self.fed = []
        self.resets = 0
        self.frames_suppressed = 0

    def feed(self, image, frame_index):
        self.fed.append(frame_index)
        result = self.script.get(frame_index, [])
        if isinstance(result, Exception):
            raise result
        return result

    def reset(self):
        self.resets += 1


class FakeCapture:
    """A capture that yields `frames` frames, then whatever `then` says."""

    def __init__(self, frames=3, then=(False, None), on_read=None):
        self.remaining = frames
        self.then = then
        self.released = False
        self.reads = 0
        self.on_read = on_read

    def read(self):
        self.reads += 1
        if self.on_read:
            self.on_read(self)
        if self.remaining > 0:
            self.remaining -= 1
            return True, frame()
        return self.then

    def release(self):
        self.released = True


class ScriptedCapture:
    """A capture that succeeds or fails read by read, to order."""

    def __init__(self, script):
        self.script = list(script)
        self.released = False
        self.reads = 0

    def read(self):
        self.reads += 1
        ok = self.script.pop(0) if self.script else False
        return (True, frame()) if ok else (False, None)

    def release(self):
        self.released = True


class Clock:
    def __init__(self, start=1000.0, step=0.04):
        self.now = start
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


def build(tmp_path, pipeline=None, captures=None, **kwargs):
    store = EventStore(tmp_path / "events.db")
    clock = kwargs.pop("clock", None) or Clock()
    opened = []

    def open_capture(url):
        if captures is None:
            capture = FakeCapture()
        else:
            capture = captures.pop(0) if captures else None
            if isinstance(capture, Exception):
                raise capture
        opened.append(capture)
        return capture

    detector = StreamDetector(
        "rtsp://127.0.0.1:8554/thermal",
        "thermal",
        None,
        store,
        open_capture=kwargs.pop("open_capture", open_capture),
        pipeline=pipeline or StubPipeline(),
        clock=clock,
        # Most tests here drive the reopen schedule through the same object
        # they drive event timestamps with, which is what a machine whose clock
        # is behaving looks like. The tests that separate them do so on purpose.
        monotonic=kwargs.pop("monotonic", None) or clock,
        sleep=kwargs.pop("sleep", lambda _s: None),
        **kwargs,
    )
    detector.opened_captures = opened
    return detector, store


# --------------------------------------------------------------------------
# The happy path: a confirmed track becomes a row
# --------------------------------------------------------------------------


def test_a_confirmed_track_is_written_to_the_store(tmp_path):
    pipeline = StubPipeline({2: [detection_at(2)]})
    detector, store = build(tmp_path, pipeline=pipeline)
    try:
        for _ in range(3):
            detector.step()
        events = store.recent()
        assert len(events) == 1
        assert events[0].stream == "thermal"
        assert events[0].box == (40, 20, 6, 7)
        assert events[0].travelled_px == pytest.approx(30.0)
    finally:
        detector.close()
        store.close()


def test_an_unnamed_track_is_still_an_event(tmp_path):
    """The classifier does not run in this process yet, and must not gate."""
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build(tmp_path, pipeline=pipeline)
    try:
        detector.step()
        event = store.recent()[0]
        assert event.label == ""
        assert event.confidence == 0.0
        assert event.clip_path == ""
    finally:
        detector.close()
        store.close()


def test_the_event_starts_when_the_track_did_not_when_it_was_confirmed(tmp_path):
    """A track confirmed on its fourth frame began three frames earlier, and the
    operator seeking to it wants the beginning of the movement."""
    clock = Clock(start=1000.0, step=0.0)
    # One second per frame, stamped by the capture so the frame number and the
    # wall clock cannot drift apart in the test itself.
    capture = FakeCapture(frames=6, on_read=lambda cap: setattr(clock, "now", 1000.0 + cap.reads - 1))
    pipeline = StubPipeline({3: [detection_at(3, first_frame=1)]})
    detector, store = build(tmp_path, pipeline=pipeline, clock=clock, captures=[capture])
    try:
        for _ in range(4):
            detector.step()
        event = store.recent()[0]
        assert event.started == 1001.0  # the wall time of frame 1
        assert event.ended == 1003.0  # the wall time of frame 3
    finally:
        detector.close()
        store.close()


def test_nothing_is_written_when_nothing_is_confirmed(tmp_path):
    detector, store = build(tmp_path, pipeline=StubPipeline())
    try:
        for _ in range(3):
            detector.step()
        assert store.recent() == []
    finally:
        detector.close()
        store.close()


def test_state_reports_what_the_console_needs(tmp_path):
    pipeline = StubPipeline({1: [detection_at(1)]})
    detector, store = build(tmp_path, pipeline=pipeline)
    try:
        for _ in range(3):
            detector.step()
        state = detector.state()
        assert state["stream"] == "thermal"
        assert state["opened"] is True
        assert state["frames"] == 3
        assert state["events"] == 1
        assert state["reason"] == ""
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# A stream that will not open. Per stream, in a sentence.
# --------------------------------------------------------------------------


def test_a_stream_that_cannot_be_opened_is_reported_and_not_raised(tmp_path):
    detector, store = build(tmp_path, captures=[None])
    try:
        assert detector.step() is False
        state = detector.state()
        assert state["opened"] is False
        assert state["frames"] == 0
        assert "could not be opened" in state["reason"]
        assert "Traceback" not in state["reason"]
    finally:
        detector.close()
        store.close()


def test_an_exception_from_opening_is_a_reason_not_a_crash(tmp_path):
    detector, store = build(tmp_path, captures=[OSError("connection refused")])
    try:
        assert detector.step() is False
        assert "could not be opened" in detector.state()["reason"]
        assert detector.state()["opened"] is False
    finally:
        detector.close()
        store.close()


def test_reopening_backs_off_instead_of_hammering_the_stream(tmp_path):
    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        captures=[None, None, None],
        clock=clock,
        reopen_delay=1.0,
        max_reopen_delay=4.0,
    )
    try:
        detector.step()
        assert len(detector.opened_captures) == 1
        detector.step()  # no time has passed: must not try again
        assert len(detector.opened_captures) == 1
        clock.now = 1.5
        detector.step()
        assert len(detector.opened_captures) == 2
        clock.now = 3.0  # the delay has doubled to 2s, so 1.5s later is too soon
        detector.step()
        assert len(detector.opened_captures) == 2
        clock.now = 3.6
        detector.step()
        assert len(detector.opened_captures) == 3
    finally:
        detector.close()
        store.close()


def test_a_clock_set_backwards_does_not_stop_the_stream_being_reopened(tmp_path):
    """The reopen schedule is a duration, and durations do not come off a clock
    somebody sets by hand.

    This laptop is offline, so its clock is the operator's to correct. Measured
    against the wall clock, a correction of an hour backwards leaves the retry
    an hour in the future, and the stream is simply not tried again for that
    hour - with the reason still reading "trying again in 1 second".
    """
    wall = Clock(start=1_000_000.0, step=0.0)
    steady = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        captures=[None, FakeCapture(frames=2)],
        clock=wall,
        monotonic=steady,
        reopen_delay=1.0,
    )
    try:
        detector.step()
        assert len(detector.opened_captures) == 1
        wall.now -= 3600.0  # the operator corrects the clock by an hour
        steady.now += 2.0  # and two real seconds pass
        assert detector.step() is True
        assert len(detector.opened_captures) == 2
    finally:
        detector.close()
        store.close()


def test_an_event_never_ends_before_it_started(tmp_path):
    """A clock corrected mid-track must not produce an event with no duration
    it can be found by.

    `between()` asks for events overlapping a window - `ended >= start AND
    started <= end`. An event whose `started` is after its `ended` overlaps no
    window at all, so its mark is missing from every part of the timeline.
    """

    class SteppingClock:
        def __init__(self, readings):
            self.readings = list(readings)

        def __call__(self):
            return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

    # Frames 0 and 1 on the old clock; by frame 2 the operator has wound it back.
    clock = SteppingClock([1000.0, 1001.0, 900.0, 900.0])
    pipeline = StubPipeline({2: [detection_at(2, first_frame=0)]})
    detector, store = build(
        tmp_path, captures=[FakeCapture(frames=4)], pipeline=pipeline, clock=clock
    )
    try:
        for _ in range(3):
            detector.step()
        events = store.recent()
        assert len(events) == 1
        event = events[0]
        assert event.started <= event.ended, f"{event.started} is after {event.ended}"
        found = store.between(event.started - 5.0, event.ended + 5.0)
        assert [e.id for e in found] == [event.id], "the event overlaps no window at all"
    finally:
        detector.close()
        store.close()


def test_the_backoff_is_capped(tmp_path):
    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        captures=[None] * 20,
        clock=clock,
        reopen_delay=1.0,
        max_reopen_delay=4.0,
    )
    try:
        for _ in range(20):
            clock.now += 100.0
            detector.step()
        assert detector.reopen_delay <= 4.0
    finally:
        detector.close()
        store.close()


def test_a_stream_that_comes_back_clears_the_reason(tmp_path):
    clock = Clock(start=0.0, step=0.0)
    detector, store = build(tmp_path, captures=[None, FakeCapture(frames=2)], clock=clock)
    try:
        detector.step()
        assert detector.state()["opened"] is False
        clock.now = 100.0
        assert detector.step() is True
        assert detector.state()["opened"] is True
        assert detector.state()["reason"] == ""
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# Frames that do not arrive
# --------------------------------------------------------------------------


def test_a_single_missing_frame_is_not_fatal(tmp_path):
    capture = FakeCapture(frames=0)
    detector, store = build(tmp_path, captures=[capture, FakeCapture()], max_read_failures=3)
    try:
        detector.step()
        assert capture.released is False
        assert len(detector.opened_captures) == 1
        assert detector.state()["opened"] is True
    finally:
        detector.close()
        store.close()


def test_a_run_of_missing_frames_reopens_the_capture(tmp_path):
    """On a long radio link the socket dies without closing: reads return
    nothing forever and the process looks perfectly healthy."""
    dead = FakeCapture(frames=0)
    fresh = FakeCapture(frames=5)
    pipeline = StubPipeline()
    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        pipeline=pipeline,
        captures=[dead, fresh],
        clock=clock,
        max_read_failures=3,
    )
    try:
        for _ in range(3):
            detector.step()
        assert dead.released is True
        assert "no frames" in detector.state()["reason"]
        clock.now = 100.0
        assert detector.step() is True
        assert len(detector.opened_captures) == 2
        # A reopened stream may be pointing somewhere else entirely, so the
        # background model has to be thrown away with the socket.
        assert pipeline.resets == 1
    finally:
        detector.close()
        store.close()


def test_a_frame_after_failures_clears_the_count(tmp_path):
    """Only a *run* of empty reads means the socket is dead. Dropped frames
    scattered over a bad afternoon are just a bad afternoon, and reopening the
    stream every few minutes because of them would cost more than it saves."""
    capture = ScriptedCapture([False, False, True, False, False])
    detector, store = build(tmp_path, captures=[capture], max_read_failures=3)
    try:
        for _ in range(5):
            detector.step()
        assert capture.released is False, "two separate failures must not add up to a run"
        assert detector.state()["reopens"] == 0
    finally:
        detector.close()
        store.close()


def test_a_run_broken_only_by_silence_still_reopens(tmp_path):
    """The counterpart: without this the test above would pass on a detector
    that had simply stopped counting."""
    capture = ScriptedCapture([False, False, False])
    detector, store = build(tmp_path, captures=[capture], max_read_failures=3)
    try:
        for _ in range(3):
            detector.step()
        assert capture.released is True
        assert detector.state()["reopens"] == 1
    finally:
        detector.close()
        store.close()


def test_a_stream_that_opens_but_stutters_stops_claiming_it_could_not_be_opened(tmp_path):
    """The reason has to describe the state the detector is actually in.
    "could not be opened" next to opened=True is a sentence that sends somebody
    up the hill to check a cable that is fine."""
    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path, captures=[None, FakeCapture(frames=0)], clock=clock, max_read_failures=10
    )
    try:
        detector.step()
        assert "could not be opened" in detector.state()["reason"]
        clock.now = 100.0
        detector.step()  # opens, but this read brings back nothing
        assert detector.state()["opened"] is True
        assert "could not be opened" not in detector.state()["reason"]
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# The rule that keeps the process alive
# --------------------------------------------------------------------------


def test_the_detector_never_dies_on_a_bad_frame(tmp_path):
    """Any exception out of the pipeline is logged and the loop continues.
    Detection stopping is bad; detection stopping silently is unforgivable."""
    pipeline = StubPipeline({1: ValueError("something in the pipeline broke"), 3: [detection_at(3)]})
    detector, store = build(tmp_path, captures=[FakeCapture(frames=6)], pipeline=pipeline)
    try:
        for _ in range(5):
            detector.step()
        assert pipeline.fed == [0, 1, 2, 3, 4]
        assert detector.state()["frames"] == 5
        assert detector.state()["errors"] == 1
        assert len(store.recent()) == 1, "the frame after the failure still produced an event"
    finally:
        detector.close()
        store.close()


def test_a_store_that_refuses_a_write_does_not_stop_the_detector(tmp_path):
    class RefusingStore:
        def add(self, *args, **kwargs):
            raise RuntimeError("database is locked")

    pipeline = StubPipeline({0: [detection_at(0)], 1: [detection_at(1)]})
    detector, _store = build(tmp_path, pipeline=pipeline)
    detector.store = RefusingStore()
    try:
        detector.step()
        detector.step()
        assert detector.state()["frames"] == 2
        assert detector.state()["errors"] == 2
    finally:
        detector.close()


def test_a_capture_that_wedged_mid_read_is_visible_as_a_stall(tmp_path):
    """The one failure this loop cannot catch by itself.

    `VideoCapture.read()` on a socket that stopped talking can block inside
    ffmpeg for a long time and, on a link that dropped without closing, for
    ever. Nothing in this thread runs while that is happening: the read-failure
    counter never advances, the capture is still "open", and `reason` is still
    the empty string it was set to on the last frame that did arrive. From the
    console it is indistinguishable from a quiet perimeter.

    So the age of the last frame is published, and something outside the wedged
    thread can read it. This test never blocks on a real read - it drives the
    clock.
    """
    clock = Clock(start=1000.0, step=0.0)
    detector, store = build(tmp_path, captures=[FakeCapture(frames=1)], clock=clock)
    try:
        assert detector.state()["seconds_since_frame"] is None  # nothing has arrived
        assert detector.step() is True
        assert detector.state()["seconds_since_frame"] == 0.0
        clock.now += 300.0  # five minutes of a read that never returned
        assert detector.state()["seconds_since_frame"] == 300.0
        assert detector.state()["opened"] is True  # which is exactly the trap
    finally:
        detector.close()
        store.close()


def test_the_delivered_frame_rate_is_measured_and_published(tmp_path):
    """The confirmation rule counts frames, so the frame rate decides recall.

    A track becomes an event after three of the last five frames and twelve
    pixels of travel. At 25 fps that is a fifth of a second; at a third of a
    frame per second it is fifteen, which is longer than most people take to
    cross anything. Measured on the owner's own labelled footage, decimated:
    7/8 person spans at 30 fps, 7/8 at 3 fps, 5/8 at 1 fps, 2/8 at 0.33 fps.

    And the frame rate is not a fixed property of the camera - this app
    re-encodes the stream over ONVIF while it is running - so it is measured
    from the frames that actually arrive rather than from a setting.
    """
    clock = Clock(start=1000.0, step=2.0)  # a frame every two seconds
    detector, store = build(tmp_path, captures=[FakeCapture(frames=40)], clock=clock)
    try:
        assert detector.state()["fps"] is None  # nothing to measure yet
        for _ in range(20):
            detector.step()
        assert detector.state()["fps"] == pytest.approx(0.5, rel=0.2)
    finally:
        detector.close()
        store.close()


def test_a_capture_that_never_delivered_a_first_frame_is_a_stall_too(tmp_path):
    """The wedge can happen on the first read as easily as the thousandth.

    A capture that opened and then blocked before producing anything would have
    no last-frame time at all, and "no frames yet" must not read as "fine".
    The clock starts when the capture opens.
    """
    clock = Clock(start=1000.0, step=0.0)

    class SilentCapture:
        def read(self):
            return False, None

        def release(self):
            pass

    detector, store = build(tmp_path, captures=[SilentCapture()], clock=clock)
    try:
        detector.step()  # opens, reads nothing
        assert detector.state()["opened"] is True
        assert detector.state()["seconds_since_frame"] == 0.0
        clock.now += 300.0
        assert detector.state()["seconds_since_frame"] == 300.0
    finally:
        detector.close()
        store.close()


def test_the_service_says_so_when_a_stream_stops_delivering(tmp_path):
    """Published, and also said - the Logs tab is where an operator looks."""
    from vmd.detect_main import STALLED_AFTER_SECONDS

    clock = Clock(start=1000.0, step=0.0)
    detector, store = build(tmp_path, captures=[FakeCapture(frames=1)], clock=clock)
    try:
        detector.step()
        clock.now += STALLED_AFTER_SECONDS + 1.0
        state = detector.state()
        assert state["seconds_since_frame"] > STALLED_AFTER_SECONDS
    finally:
        detector.close()
        store.close()


def test_the_ignore_mask_is_repainted_when_the_frame_changes_size(tmp_path):
    """The stream can change resolution without anyone asking it to.

    This app re-encodes the camera over ONVIF while it is running, so a frame
    is not a fixed size. A mask painted once at the first size stops lining up
    with the picture the moment it changes: the tree the operator painted out
    comes back, and some part of the ground he never painted goes quiet.
    """

    class ResizingCapture:
        def __init__(self):
            self.sizes = [(60, 80), (60, 80), (120, 160), (120, 160)]

        def read(self):
            if not self.sizes:
                return False, None
            height, width = self.sizes.pop(0)
            return True, np.zeros((height, width), dtype=np.uint8)

        def release(self):
            pass

    detector, store = build(
        tmp_path,
        captures=[ResizingCapture()],
        ignore_regions=[(0, 0, 20, 20)],
    )
    try:
        detector.step()
        assert detector.config.ignore_mask.shape == (60, 80)
        detector.step()
        detector.step()  # the stream is now twice the size
        assert detector.config.ignore_mask.shape == (120, 160), (
            "the mask still describes a picture the camera stopped sending"
        )
        assert detector.config.ignore_mask[0, 0] != 0
    finally:
        detector.close()
        store.close()


def test_movement_is_still_announced_when_there_is_no_store(tmp_path, caplog):
    """A detector with no database still has to say what it saw.

    `detect_main` opens the store on the detector's own thread and, when that
    fails, logs "movement will be logged only" and carries on. It was not
    logged only: the recording path returned before it said anything, so a
    person crossing the perimeter produced a row in no database, a line in no
    log and no count anywhere. That is the exact failure this system exists to
    prevent, arrived at through a disk that was full.
    """
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build(tmp_path, pipeline=pipeline)
    store.close()
    detector.store = None
    try:
        with caplog.at_level("WARNING", logger="vmd.detect.runner"):
            detector.step()
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "movement" in said.lower(), f"nothing was said about the movement: {said!r}"
        assert "40" in said and "20" in said, "the movement was announced without saying where"
        # And it is visible as a number, not only as a line in a log nobody
        # scrolls back through.
        assert detector.state()["unrecorded"] == 1
    finally:
        detector.close()


def test_a_store_that_refuses_a_write_still_says_what_moved(tmp_path, caplog):
    """A locked database loses the row. It must not also lose the sentence."""

    class RefusingStore:
        def add(self, *args, **kwargs):
            raise RuntimeError("database is locked")

    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build(tmp_path, pipeline=pipeline)
    store.close()
    detector.store = RefusingStore()
    try:
        with caplog.at_level("WARNING", logger="vmd.detect.runner"):
            detector.step()
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "movement" in said.lower(), f"nothing was said about the movement: {said!r}"
        assert detector.state()["unrecorded"] == 1
    finally:
        detector.close()


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def test_run_returns_when_it_is_told_to_stop(tmp_path):
    detector = None

    def stop_after_five(capture):
        if capture.reads >= 5:
            detector.stop()

    def stop_if_this_is_going_nowhere(_seconds):
        # A safety net, not the thing under test: without it a detector that
        # never reads a frame would hang this test rather than fail it.
        detector.stop()

    capture = FakeCapture(frames=100, on_read=stop_after_five)
    detector, store = build(tmp_path, captures=[capture], sleep=stop_if_this_is_going_nowhere)
    try:
        detector.run()  # must return rather than hang
        assert detector.state()["frames"] == 5
        assert detector.stopped is True
    finally:
        detector.close()
        store.close()


def test_run_returns_immediately_when_already_stopped(tmp_path):
    detector, store = build(tmp_path)
    try:
        detector.stop()
        detector.run()
        assert detector.state()["frames"] == 0
    finally:
        detector.close()
        store.close()


def test_run_waits_between_failed_passes(tmp_path):
    """A stream that is down must not become a busy loop.

    The loop is ended by counting attempts rather than by counting sleeps, so a
    detector that never sleeps fails this test instead of hanging it.
    """
    slept = []
    attempts = []
    detector = None

    def open_capture(url):
        attempts.append(url)
        if len(attempts) >= 3:
            detector.stop()
        return None

    detector, store = build(
        tmp_path,
        open_capture=open_capture,
        sleep=slept.append,
        idle_sleep=0.25,
        reopen_delay=0.0,
        max_reopen_delay=0.0,
    )
    try:
        detector.run()
        assert len(attempts) == 3
        # One wait per failed pass, and none after the stop was asked for.
        assert slept == [0.25, 0.25]
    finally:
        detector.close()
        store.close()


def test_closing_releases_the_capture(tmp_path):
    capture = FakeCapture(frames=2)
    detector, store = build(tmp_path, captures=[capture])
    try:
        detector.step()
        detector.close()
        assert capture.released is True
    finally:
        store.close()


# --------------------------------------------------------------------------
# The ignore mask, which cannot be built until a frame exists
# --------------------------------------------------------------------------


def test_the_ignore_mask_is_painted_from_the_first_frame(tmp_path):
    detector, store = build(tmp_path, ignore_regions=[(1, 1, 3, 3)])
    try:
        assert detector.config.ignore_mask is None
        detector.step()
        mask = detector.config.ignore_mask
        assert mask is not None
        assert mask.shape == (8, 8)
        assert mask[2, 2] != 0
        assert mask[6, 6] == 0
    finally:
        detector.close()
        store.close()


def test_the_mask_is_painted_once(tmp_path):
    detector, store = build(tmp_path, captures=[FakeCapture(frames=4)], ignore_regions=[(1, 1, 3, 3)])
    try:
        detector.step()
        first = detector.config.ignore_mask
        detector.step()
        assert detector.config.ignore_mask is first
    finally:
        detector.close()
        store.close()


def test_no_regions_means_no_mask(tmp_path):
    detector, store = build(tmp_path)
    try:
        detector.step()
        assert detector.config.ignore_mask is None
    finally:
        detector.close()
        store.close()
