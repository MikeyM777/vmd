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
        kwargs.pop("url", "rtsp://127.0.0.1:8554/thermal"),
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
    # A steady clock of its own, so what the rate is measured against is only
    # the stepping wall clock above and not also every duration this loop reads.
    detector, store = build(
        tmp_path,
        captures=[FakeCapture(frames=40)],
        clock=clock,
        monotonic=Clock(start=0.0, step=0.0),
    )
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


def test_a_drawn_area_is_painted_from_the_first_frame(tmp_path):
    """An outline the operator traced reaches the mask the same way a box does.

    It is the same journey and the same first frame; only the shape is new.
    """
    detector, store = build(tmp_path, ignore_shapes=[[(1, 1), (5, 1), (5, 5), (1, 5)]])
    try:
        assert detector.config.ignore_mask is None
        detector.step()
        mask = detector.config.ignore_mask
        assert mask is not None
        assert mask.shape == (8, 8)
        assert mask[3, 3] != 0
        assert mask[7, 7] == 0
    finally:
        detector.close()
        store.close()


def test_a_box_and_an_outline_are_painted_into_the_same_mask(tmp_path):
    """Both at once, so a settings file written before the outlines still works.

    Everything he has already marked out is a rectangle. The day he traces his
    first treeline is not the day those rectangles stop being ignored.
    """
    detector, store = build(
        tmp_path,
        ignore_regions=[(0, 0, 2, 2)],
        ignore_shapes=[[(4, 4), (7, 4), (7, 7), (4, 7)]],
    )
    try:
        detector.step()
        mask = detector.config.ignore_mask
        assert mask is not None
        assert mask[0, 0] != 0, "the rectangle from the old settings file"
        assert mask[5, 5] != 0, "the outline he drew today"
        assert mask[0, 7] == 0
    finally:
        detector.close()
        store.close()


def test_the_drawn_areas_are_repainted_when_the_frame_changes_size(tmp_path):
    """The same fault as the rectangles', and it is not fixed by them being fixed.

    A mask painted once at the first size stops lining up with the picture the
    moment the stream re-encodes underneath it, and the traced treeline comes
    back to life with nothing anywhere saying why.
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
        ignore_shapes=[[(0, 0), (20, 0), (20, 20), (0, 20)]],
    )
    try:
        detector.step()
        assert detector.config.ignore_mask.shape == (60, 80)
        detector.step()
        detector.step()  # the stream is now twice the size
        assert detector.config.ignore_mask.shape == (120, 160)
        assert detector.config.ignore_mask[0, 0] != 0
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# Frames that arrive but carry nothing
# --------------------------------------------------------------------------


def picture(height=64, width=64, seed=0):
    """A frame with something in it: a gradient, so it is never flat."""
    rows = np.arange(height, dtype=np.uint8).reshape(height, 1)
    columns = np.arange(width, dtype=np.uint8).reshape(1, width)
    return ((rows + columns + seed) % 251).astype(np.uint8)


class RepeatingCapture:
    """A capture that hands back the same picture for ever."""

    def __init__(self, image):
        self.image = image
        self.reads = 0
        self.released = False

    def read(self):
        self.reads += 1
        return True, self.image.copy()

    def release(self):
        self.released = True


def test_a_stream_delivering_a_blank_picture_is_not_counted_as_watching(tmp_path):
    """`read()` returning True is not a picture arriving.

    A decoder handed a stream it cannot make sense of - the wrong sub-stream,
    a codec it will not admit to failing on, a camera that has powered its
    sensor down - returns success and a frame of one flat value. Every guard in
    this loop passes: ok is True, the frame is not None, the frame count
    climbs, the frame rate is healthy, the read-failure counter stays at zero
    and `reason` stays empty. Background subtraction on a flat picture finds
    nothing for ever, so the stream reports exactly what a quiet perimeter
    reports, and there is no other way to tell them apart.
    """
    detector, store = build(tmp_path, captures=[RepeatingCapture(np.zeros((64, 64), np.uint8))])
    try:
        for _ in range(20):
            assert detector.step() is True
        state = detector.state()
        assert state["frames"] == 20, "the frames did arrive; that is the trap"
        assert state["opened"] is True
        assert state["blind"] is True
        assert "no picture" in state["reason"], state["reason"]
    finally:
        detector.close()
        store.close()


def test_a_stream_with_a_real_picture_in_it_is_never_called_blind(tmp_path):
    """The check has to be one no working camera can fail.

    A false "no picture" would have the operator chasing a stream that is fine,
    and an operator who has learned to disbelieve the warning is worse off than
    one who never had it.
    """
    detector, store = build(tmp_path, captures=[FakeCapture(frames=0)])
    detector._open_capture = lambda url: RepeatingCapture(picture())
    try:
        for _ in range(30):
            detector.step()
        state = detector.state()
        assert state["frames"] == 30
        assert state["blind"] is False
        assert state["reason"] == ""
    finally:
        detector.close()
        store.close()


def test_a_picture_that_never_changes_is_visible_as_a_frozen_stream(tmp_path):
    """One frame, delivered over and over, is not a stream.

    A relay that cached a keyframe, a decoder repeating its last good picture
    after the link dropped: the frames keep coming, at a healthy rate, and the
    picture in them is the same picture. Movement is found by comparing a frame
    with the ones before it, so a stream that never changes can never produce a
    detection - and it is counted among the streams being watched.
    """
    steady = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        captures=[RepeatingCapture(picture())],
        clock=Clock(start=1000.0, step=0.0),
        monotonic=steady,
    )
    try:
        detector.step()
        assert detector.state()["seconds_since_change"] == 0.0
        for _ in range(5):
            steady.now += 30.0
            detector.step()
        assert detector.state()["seconds_since_change"] == 150.0, (
            "the picture has not moved for two and a half minutes"
        )
    finally:
        detector.close()
        store.close()


def test_a_changing_picture_keeps_resetting_the_time_since_it_changed(tmp_path):
    steady = Clock(start=0.0, step=0.0)

    class MovingCapture:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            return True, picture(seed=self.reads)

        def release(self):
            pass

    detector, store = build(
        tmp_path,
        captures=[MovingCapture()],
        clock=Clock(start=1000.0, step=0.0),
        monotonic=steady,
    )
    try:
        detector.step()
        steady.now += 100.0
        detector.step()
        assert detector.state()["seconds_since_change"] == 0.0
    finally:
        detector.close()
        store.close()


def test_a_clock_set_backwards_does_not_hide_a_stream_that_went_quiet(tmp_path):
    """How long a stream has been silent is a duration, not two dates.

    This laptop is offline and its clock is set by hand. Measured against the
    wall clock, an hour's correction backwards makes the silence negative, and
    a negative silence is below every threshold there is - so the one reading
    that tells a wedged read from a quiet perimeter reports "fine" for an hour,
    which is exactly as long as the operator is least able to afford it.
    """
    wall = Clock(start=1000.0, step=0.0)
    steady = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        captures=[FakeCapture(frames=1)],
        clock=wall,
        monotonic=steady,
    )
    try:
        assert detector.step() is True
        steady.now += 300.0  # five minutes of a read that never returned
        wall.now -= 3600.0  # and the operator corrects the clock by an hour
        assert detector.state()["seconds_since_frame"] == 300.0
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# The local streaming server is not the only way to the camera
# --------------------------------------------------------------------------


def test_a_local_source_that_never_opens_falls_back_to_the_camera(tmp_path):
    """Adopting the local server is a decision made once, at start-up.

    Whether to read through go2rtc is decided from a port answering, and then
    the address is kept for the life of the process. If the thing on that port
    is a go2rtc from an older settings file, or one that restarted somewhere
    else, every open fails and the detector reports "the stream could not be
    opened" for ever - while the camera itself is reachable the whole time and
    is never tried again. Detection is then off, permanently, on a system whose
    entire purpose is detection.
    """
    local = "rtsp://127.0.0.1:8554/thermal"
    camera = "rtsp://10.0.0.2/thermal"
    opened = []

    def open_capture(url):
        opened.append(url)
        return FakeCapture(frames=50) if url == camera else None

    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=open_capture,
        url=local,
        fallback_url=camera,
    )
    try:
        for _ in range(10):
            detector.step()
            clock.now += 60.0  # past whatever the backoff has climbed to
        assert camera in opened, opened
        assert detector.opened is True
        assert detector.url == camera
    finally:
        detector.close()
        store.close()


def test_the_camera_is_only_tried_after_the_local_server_has_really_failed(tmp_path):
    """One failed open is a stream that has not come up yet, not a wrong address.

    Pulling the camera directly costs the radio link a second copy of the
    stream, which is what the local server exists to avoid.
    """
    local = "rtsp://127.0.0.1:8554/thermal"
    camera = "rtsp://10.0.0.2/thermal"
    opened = []

    def open_capture(url):
        opened.append(url)
        return None

    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=open_capture,
        url=local,
        fallback_url=camera,
    )
    try:
        detector.step()
        clock.now += 60.0
        detector.step()
        assert opened == [local, local], opened
    finally:
        detector.close()
        store.close()


def test_a_stream_with_no_fallback_keeps_trying_the_one_address_it_has(tmp_path):
    local = "rtsp://127.0.0.1:8554/thermal"
    opened = []

    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=lambda url: opened.append(url) or None,
        url=local,
    )
    try:
        for _ in range(6):
            detector.step()
            clock.now += 60.0
        assert set(opened) == {local}
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# ...and going back to it, which is the half that was missing
# --------------------------------------------------------------------------
#
# Falling back to the camera was one-way. It rotated on failure and never on
# success, so a single go2rtc restart - which the console performs on every
# material settings change - moved detection onto a second crossing of a
# >15 km, ~5 Mb/s radio link, permanently, for one warning line in a ring of
# five hundred. The link "barely carries one" copy of the stream; losing the
# live picture is the failure this system exists not to have.

LOCAL = "rtsp://127.0.0.1:8554/thermal"
CAMERA = "rtsp://admin:hunter2@10.0.0.2/thermal"


class Addresses:
    """Two ways to the same picture, each switchable on and off by the test.

    Every open attempt is recorded in order, because that is the question that
    matters here: which address is this detector pulling from, and did it ever
    ask the local server whether it had come back?
    """

    def __init__(self, up, frames=None):
        self.up = dict(up)
        self.frames = dict(frames or {})
        self.opened = []
        # For each open, which addresses had already been let go at that
        # moment. A switch that releases the working stream before the new one
        # is known good is a gap in watching the perimeter.
        self.let_go_by_then = []
        self.captures = {}

    def __call__(self, url):
        self.opened.append(url)
        self.let_go_by_then.append(
            sorted(where for where, capture in self.captures.items() if capture.released)
        )
        if not self.up.get(url):
            return None
        capture = FakeCapture(frames=self.frames.get(url, 1_000_000))
        self.captures[url] = capture
        return capture

    def tries(self, url):
        return self.opened.count(url)


def on_the_camera(tmp_path, addresses, clock, **kwargs):
    """A detector that has already fallen back to the camera and is reading it.

    This is the state a go2rtc restart leaves behind, and it is the state the
    process used to stay in until somebody noticed.
    """
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=addresses,
        url=LOCAL,
        fallback_url=CAMERA,
        **kwargs,
    )
    for _ in range(4):
        detector.step()
        # Past whatever the reopen backoff has climbed to (1, 2, 4 seconds),
        # and deliberately far short of the wait before going back, which is
        # what the tests below measure.
        clock.now += 5.0
    assert detector.url == CAMERA, detector.url
    assert detector.opened is True
    return detector, store


def test_the_detector_goes_back_to_the_local_server_when_it_comes_back(tmp_path):
    """The bug, in one test: rotating on failure and never on success.

    go2rtc restarts, the detector fails over to the camera, go2rtc comes back
    ten seconds later - and nothing ever looks. Two copies of the stream cross
    the radio link for the life of the process.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True})
    detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
    try:
        addresses.up[LOCAL] = True  # go2rtc is back
        addresses.opened.clear()
        clock.now += 121.0
        assert detector.step() is True, "the pass that switched still fed a frame"
        assert detector.url == LOCAL
        assert detector.state()["source"] == "local"
        assert addresses.opened == [LOCAL], addresses.opened
    finally:
        detector.close()
        store.close()


def test_it_does_not_go_back_the_moment_the_local_server_answers(tmp_path):
    """Rotating on every hiccup is worse than staying put.

    A settled period, the same shape as the supervisor's: the detector reads
    the camera for a couple of minutes before it asks whether the local server
    is back, so a go2rtc that is restarting - or one that answers its port
    while it is still coming up - does not get detection handed back and
    dropped again.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True})
    detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
    try:
        addresses.up[LOCAL] = True
        addresses.opened.clear()
        for _ in range(10):
            clock.now += 10.0  # a hundred seconds, short of the two minutes
            detector.step()
        assert addresses.opened == [], addresses.opened
        assert detector.url == CAMERA
    finally:
        detector.close()
        store.close()


def test_the_camera_is_let_go_only_once_the_local_server_is_known_good(tmp_path):
    """A gap in watching the perimeter is the cost this system exists to avoid.

    So the local server is opened, and read from, while the camera is still
    open and being read. Only then is the camera released - and it is
    released, because a detector holding both is the very cost this is fixing.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True})
    detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
    try:
        camera_capture = addresses.captures[CAMERA]
        addresses.up[LOCAL] = True
        addresses.opened.clear()
        addresses.let_go_by_then.clear()
        clock.now += 121.0
        assert detector.step() is True
        assert detector.opened is True, "something was open at every point"
        assert addresses.let_go_by_then == [[]], "the camera was dropped before the swap"
        assert camera_capture.released is True, "the link is still carrying two copies"
    finally:
        detector.close()
        store.close()


def test_a_local_server_that_answers_with_nothing_does_not_get_detection_back(tmp_path):
    """Answering the port is not serving the stream.

    go2rtc listening on 127.0.0.1 proves something is listening; it proves
    nothing about this stream. So the way back is not "it opened" but "it
    opened and handed over a frame", checked before the camera is let go.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True}, frames={LOCAL: 0})
    detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
    try:
        addresses.up[LOCAL] = True  # it opens, and has no picture behind it
        clock.now += 121.0
        detector.step()
        assert detector.url == CAMERA
        assert detector.state()["source"] == "camera"
        assert detector.opened is True, "detection carried on from the camera"
        assert addresses.captures[LOCAL].released is True, "the probe was let go"
    finally:
        detector.close()
        store.close()


def test_a_return_that_did_not_last_is_tried_less_often(tmp_path):
    """The flap guard: a return that did not stick doubles the wait.

    A go2rtc that opens, hands over a frame and then goes quiet would
    otherwise have the detector crossing back and forth for months, each
    crossing costing a reset background model and a few seconds of nothing
    being watched.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True}, frames={LOCAL: 1})
    detector, store = on_the_camera(
        tmp_path,
        addresses,
        clock,
        return_after=120.0,
        settled_after=300.0,
        max_read_failures=1,
    )
    try:
        addresses.up[LOCAL] = True  # a picture, and then nothing behind it
        clock.now += 121.0
        detector.step()
        assert detector.url == LOCAL, "the probe found a picture and took it"

        # It delivers nothing more, so the detector gives up on it and goes
        # back to the camera the way it always did.
        addresses.up[LOCAL] = False
        for _ in range(6):
            detector.step()
            clock.now += 5.0
        assert detector.url == CAMERA, "detection is back on the camera"

        # That return lasted seconds, not the five minutes it has to. So the
        # next one is not two minutes away, it is four.
        addresses.opened.clear()
        clock.now += 130.0
        detector.step()
        assert addresses.tries(LOCAL) == 0, "it went straight back into the flap"
        clock.now += 130.0
        detector.step()
        assert addresses.tries(LOCAL) == 1, "and it never asked again"
    finally:
        detector.close()
        store.close()


def test_a_go2rtc_that_is_really_gone_leaves_detection_on_the_camera(tmp_path):
    """Detecting from the wrong place beats not detecting.

    Every probe fails, and every one of them costs a refused connection on
    127.0.0.1. What must not happen is the detector dropping the camera to go
    and look.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True})
    detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
    try:
        before = detector.frames
        for _ in range(20):
            clock.now += 121.0
            assert detector.step() is True
        assert detector.url == CAMERA
        assert detector.state()["source"] == "camera"
        assert detector.frames > before, "frames kept arriving throughout"
        assert addresses.tries(LOCAL) >= 2, "and it kept asking"
    finally:
        detector.close()
        store.close()


def test_the_way_back_follows_a_local_server_that_moved(tmp_path):
    """go2rtc is started on a free port, so a restart can move it.

    Without this the detector would offer to come back to an address nothing
    has answered on since the restart, every two minutes, for ever - which is
    the fault this whole section is about, one level down, and it would have
    made the fix look like it worked.
    """
    moved = "rtsp://127.0.0.1:8555/thermal"
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True, moved: True})
    detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
    try:
        detector.point_at_local(moved)
        addresses.opened.clear()
        clock.now += 121.0
        assert detector.step() is True
        assert detector.url == moved
        assert detector.state()["source"] == "local"
        assert addresses.tries(LOCAL) == 0, "it went back to the old port"
        # And the camera is still the address to fall back to.
        assert CAMERA in detector.sources
    finally:
        detector.close()
        store.close()


def test_a_local_server_that_moved_underneath_an_open_stream_is_taken_up(tmp_path):
    """The address being read has just been declared the wrong one.

    Acted on by the detector's own thread and nowhere else: a capture released
    while the thread that owns it is inside `read()` is a crash in C, not an
    exception.
    """
    moved = "rtsp://127.0.0.1:8555/thermal"
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: True, CAMERA: True, moved: True})
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=addresses,
        url=LOCAL,
        fallback_url=CAMERA,
    )
    try:
        detector.step()
        was_open = addresses.captures[LOCAL]
        detector.point_at_local(moved)
        assert was_open.released is False, "another thread let go of a live capture"
        detector.step()
        assert detector.url == moved
        assert was_open.released is True
        assert detector.state()["source"] == "local"
        assert detector.sources == [moved, CAMERA], detector.sources
    finally:
        detector.close()
        store.close()


def test_the_state_says_which_way_to_the_picture_is_in_use(tmp_path):
    """A detector silently costing double the link is the invisible fault.

    The console is another process and cannot ask, so this goes in the state
    it publishes - with the password taken out of it, because that state is
    read on screen and photographed.
    """
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: True, CAMERA: True})
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=addresses,
        url=LOCAL,
        fallback_url=CAMERA,
        max_read_failures=1,
    )
    try:
        detector.step()
        state = detector.state()
        assert state["source"] == "local"
        assert state["source_url"] == LOCAL

        # go2rtc goes away underneath an open capture, which is what a restart
        # looks like from here.
        addresses.up[LOCAL] = False
        addresses.captures[LOCAL].remaining = 0
        for _ in range(6):
            detector.step()
            clock.now += 5.0
        state = detector.state()
        assert state["source"] == "camera"
        assert "hunter2" not in state["source_url"]
        assert "10.0.0.2" in state["source_url"]
    finally:
        detector.close()
        store.close()


def test_a_stream_read_straight_from_the_camera_says_so(tmp_path):
    """No local server was ever chosen for this stream, so nothing is being
    doubled - but the operator is still told where the picture comes from."""
    clock = Clock(start=0.0, step=0.0)
    detector, store = build(
        tmp_path,
        clock=clock,
        open_capture=Addresses({CAMERA: True}),
        url=CAMERA,
        primary_source="camera",
    )
    try:
        detector.step()
        assert detector.state()["source"] == "camera"
    finally:
        detector.close()
        store.close()


def test_both_directions_are_said_out_loud(tmp_path, caplog):
    """The detector's output is the Logs tab. A source change is news in both
    directions: one of them is the link cost doubling, the other is it ending."""
    clock = Clock(start=0.0, step=0.0)
    addresses = Addresses({LOCAL: False, CAMERA: True})
    with caplog.at_level("INFO", logger="vmd.detect.runner"):
        detector, store = on_the_camera(tmp_path, addresses, clock, return_after=120.0)
        try:
            said = " | ".join(r.getMessage() for r in caplog.records)
            assert "camera" in said.lower()
            assert "hunter2" not in said

            caplog.clear()
            addresses.up[LOCAL] = True
            clock.now += 121.0
            detector.step()
            said = " | ".join(r.getMessage() for r in caplog.records)
            assert LOCAL in said, said
            assert "local streaming server" in said, said
        finally:
            detector.close()
            store.close()


# --------------------------------------------------------------------------
# A classifier that is on and has never answered
# --------------------------------------------------------------------------


class SilentClassifier:
    """Switched on, and never able to say anything - a wedged model, a budget
    that is always missed, a weights file that would not load."""

    def classify(self, frame, box):
        return ("", 0.0)


def test_a_classifier_that_has_never_named_anything_is_visible(tmp_path):
    """"Unnamed" is the normal, correct answer here, which is what hides this.

    At 700 m a person is 13 pixels and nothing can name it, so the operator
    who switched the classifier on sees exactly what they would see if it were
    working: events with no label. A classifier that has been asked a hundred
    times and answered nothing is a broken install, and the only thing that
    can tell it from a quiet correct one is the count.
    """
    pipeline = StubPipeline({index: [detection_at(index)] for index in range(4)})
    detector, store = build(
        tmp_path,
        pipeline=pipeline,
        captures=[FakeCapture(frames=4)],
        classifier=SilentClassifier(),
    )
    try:
        for _ in range(4):
            detector.step()
        state = detector.state()
        assert state["classifying"] is True
        assert state["named_asked"] == 4
        assert state["named"] == 0
    finally:
        detector.close()
        store.close()


def test_a_classifier_that_is_off_is_not_reported_as_one_that_has_failed(tmp_path):
    """The default classifier names nothing on purpose. It is not a fault."""
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build(tmp_path, pipeline=pipeline)
    try:
        detector.step()
        state = detector.state()
        assert state["classifying"] is False
        assert state["named_asked"] == 0, "nothing was asked, because nothing was on"
    finally:
        detector.close()
        store.close()


def test_the_names_a_working_classifier_produces_are_counted(tmp_path):
    class Naming:
        def classify(self, frame, box):
            return ("person", 0.8)

    pipeline = StubPipeline({0: [detection_at(0)], 1: [detection_at(1)]})
    detector, store = build(
        tmp_path,
        pipeline=pipeline,
        captures=[FakeCapture(frames=2)],
        classifier=Naming(),
    )
    try:
        detector.step()
        detector.step()
        state = detector.state()
        assert state["named_asked"] == 2
        assert state["named"] == 2
    finally:
        detector.close()
        store.close()
