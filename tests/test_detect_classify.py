"""The classifier: it names what moved, and it can never stop it being news.

Every test in this file exists to defend one sentence from the design: *a track
that the classifier cannot name is still an event*. The classifier is stubbed
throughout except for the single integration test at the bottom, because loading
YOLO weights takes seconds and proves nothing about the plumbing.

Nothing here may block on the thing under test. Every wait is bounded by a
timeout of its own, so a mutation that wedges the classifier fails the suite
instead of hanging it.
"""

import json
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from vmd.detect.classify import (
    CROP_PAD,
    MIN_BLOB_PX,
    MIN_CROP_PX,
    UNNAMED,
    BudgetedClassifier,
    NullClassifier,
    YoloClassifier,
    crop_for,
    crop_rect,
)
from vmd.detect.config import classifier_for, classify_enabled, config_from_settings
from vmd.detect.events import EventStore
from vmd.detect.motion import Box
from vmd.detect.pipeline import Detection
from vmd.detect.runner import StreamDetector
from vmd.detect.tracking import Track
from vmd.settings import DetectionSettings, Settings, StreamSettings, load_settings, save_settings

# How long any test is prepared to wait for a thread that should already have
# finished. Generous, and still finite: the point is that a wedged worker fails
# this file rather than freezing the suite.
JOIN_TIMEOUT = 5.0


# --------------------------------------------------------------------------
# stubs
# --------------------------------------------------------------------------


class StubModel:
    """Stands in for a loaded ultralytics model."""

    def __init__(self, boxes=(), names=None, raises=None, result=None):
        self.names = names or {0: "person", 2: "car"}
        self.boxes = list(boxes)
        self.raises = raises
        self.result = result
        self.calls = []

    def predict(self, crop, **kwargs):
        self.calls.append((crop, kwargs))
        if self.raises is not None:
            raise self.raises
        if self.result is not None:
            return self.result
        return [StubResult(self.boxes)]


class StubResult:
    def __init__(self, boxes):
        self.boxes = StubBoxes(boxes)


class StubBoxes:
    """What ultralytics hands back: parallel tensors of class and confidence."""

    def __init__(self, boxes):
        self.cls = np.array([float(c) for c, _ in boxes], dtype=np.float32)
        self.conf = np.array([float(p) for _, p in boxes], dtype=np.float32)

    def __len__(self):
        return len(self.cls)


class CountingLoader:
    """A stand-in for "import ultralytics and open the weights"."""

    def __init__(self, model=None, raises=None):
        self.model = model if model is not None else StubModel()
        self.raises = raises
        self.calls = 0

    def __call__(self, weights):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.model


class BlockingClassifier:
    """A classifier that does not return until the test lets it.

    `release` is always set in the test's `finally`, so no worker outlives the
    test that made it, and `entered` proves how many times it was asked.
    """

    def __init__(self, answer=("person", 0.9)):
        self.answer = answer
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def classify(self, frame, box):
        self.calls += 1
        self.entered.set()
        # Bounded even if the test forgets: a worker thread that waits forever
        # is the failure mode this file refuses to have.
        self.release.wait(JOIN_TIMEOUT)
        return self.answer


class ScriptedClassifier:
    """Answers to order, and remembers what it was shown."""

    def __init__(self, answer=("person", 0.9), raises=None):
        self.answer = answer
        self.raises = raises
        self.seen = []

    def classify(self, frame, box):
        self.seen.append((frame, box))
        if self.raises is not None:
            raise self.raises
        return self.answer


class StubPipeline:
    """A script of what each frame returns, as in test_detect_runner."""

    def __init__(self, script=None):
        self.script = script or {}
        self.fed = []

    def feed(self, image, frame_index):
        self.fed.append((image, frame_index))
        return self.script.get(frame_index, [])

    def reset(self):
        pass


class FakeCapture:
    def __init__(self, frame, frames=8):
        self.frame = frame
        self.remaining = frames
        self.released = False

    def read(self):
        if self.remaining <= 0:
            return False, None
        self.remaining -= 1
        return True, self.frame

    def release(self):
        self.released = True


def detection_at(frame_index, box=(100, 100, 40, 40), first_frame=0):
    track = Track(id=7)
    track.observe(Box(box[0], box[1], box[2], box[3]), first_frame)
    track.observe(Box(box[0] + 30, box[1], box[2], box[3]), frame_index)
    return Detection(track=track, box=track.box, frame_index=frame_index)


def build_detector(tmp_path, frame, pipeline, classifier=None, **kwargs):
    store = EventStore(tmp_path / "events.db")
    capture = FakeCapture(frame)
    detector = StreamDetector(
        "rtsp://127.0.0.1:8554/thermal",
        "thermal",
        None,
        store,
        open_capture=lambda url: capture,
        pipeline=pipeline,
        clock=lambda: 1000.0,
        sleep=lambda _s: None,
        classifier=classifier,
        **kwargs,
    )
    return detector, store


def gradient_frame(width=640, height=512):
    """A frame in which every pixel is different, so a crop can be located."""
    rows = np.arange(height, dtype=np.uint16).reshape(height, 1)
    columns = np.arange(width, dtype=np.uint16).reshape(1, width)
    return ((rows * 7 + columns * 13) % 251).astype(np.uint8)


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------


def test_the_null_classifier_names_nothing():
    """The default everywhere, and the whole of the thermal answer."""
    assert NullClassifier().classify(gradient_frame(), Box(10, 10, 40, 40)) == ("", 0.0)
    assert UNNAMED == ("", 0.0)


def test_the_null_classifier_does_not_mind_a_frame_that_is_not_there():
    assert NullClassifier().classify(None, Box(0, 0, 1, 1)) == ("", 0.0)


# --------------------------------------------------------------------------
# the crop: full resolution, padded, clamped
# --------------------------------------------------------------------------


def test_the_crop_is_cut_from_the_frame_at_native_resolution():
    """Not downscaled. A 40-pixel person survives cropping and does not survive
    a whole frame being squeezed to 640 - that is the measured reason this
    pipeline exists at all."""
    frame = gradient_frame()
    box = Box(300, 200, 60, 80)
    left, top, side = crop_rect(box, 640, 512)
    crop = crop_for(frame, box)

    assert crop.shape == (side, side)
    assert np.array_equal(crop, frame[top : top + side, left : left + side])


def test_the_crop_is_padded_around_the_box():
    """A box tight to a moving blob clips the thing's extremities: a walking
    person's trailing leg is not in the foreground mask on every frame."""
    box = Box(300, 200, 100, 60)
    left, top, side = crop_rect(box, 640, 512)

    assert side == int(100 * CROP_PAD)
    assert left <= box.x and left + side >= box.right
    assert top <= box.y and top + side >= box.bottom


def test_a_small_box_still_gets_a_crop_worth_looking_at():
    """Below the minimum side the crop is grown, not the box: context around a
    small blob is what the model has to work with."""
    left, top, side = crop_rect(Box(300, 200, 24, 24), 640, 512)
    assert side == MIN_CROP_PX


def test_the_crop_never_leaves_the_frame():
    for box in (Box(0, 0, 40, 40), Box(600, 480, 40, 30), Box(0, 480, 30, 30)):
        left, top, side = crop_rect(box, 640, 512)
        assert left >= 0 and top >= 0
        assert left + side <= 640
        assert top + side <= 512


def test_a_crop_larger_than_the_frame_is_the_frame():
    """The crop can never be bigger than what there is to crop from."""
    left, top, side = crop_rect(Box(10, 10, 200, 200), 100, 80)
    assert side == 80  # the frame's shorter side
    assert left >= 0 and left + side <= 100
    assert top >= 0 and top + side <= 80


def test_a_colour_frame_is_cropped_as_a_colour_frame():
    frame = np.zeros((512, 640, 3), dtype=np.uint8)
    frame[200:280, 300:360] = (10, 20, 30)
    crop = crop_for(frame, Box(300, 200, 60, 80))
    assert crop.ndim == 3 and crop.shape[2] == 3


# --------------------------------------------------------------------------
# the size below which it does not bother asking
# --------------------------------------------------------------------------


def test_a_blob_too_small_to_name_is_never_asked_about():
    """At 700 m a person is about 13 pixels on the thermal sensor. Asking the
    model costs 40 ms and cannot answer."""
    loader = CountingLoader()
    classifier = YoloClassifier(load=loader)

    assert classifier.classify(gradient_frame(), Box(300, 200, 13, 13)) == UNNAMED
    assert loader.calls == 0


def test_a_blob_at_the_measured_working_size_is_asked_about():
    """93% recall at 35-78 px is the measurement this floor must not cross."""
    loader = CountingLoader(StubModel(boxes=[(0, 0.8)]))
    classifier = YoloClassifier(load=loader)

    label, confidence = classifier.classify(gradient_frame(), Box(300, 200, 35, 35))
    assert label == "person"
    assert confidence == pytest.approx(0.8, abs=1e-6)
    assert loader.calls == 1
    assert MIN_BLOB_PX < 35


# --------------------------------------------------------------------------
# lazily loaded, and never fatal
# --------------------------------------------------------------------------


def test_the_module_imports_on_a_laptop_with_no_torch_installed():
    """The console imports this module. It must not drag torch in, and building
    a YoloClassifier must not open any weights."""
    code = (
        "import sys\n"
        "class Blocked:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in ('ultralytics', 'torch'):\n"
        "            raise ImportError('not installed')\n"
        "        return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('ultralytics', 'torch'):\n"
        "            raise ImportError('not installed')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocked())\n"
        "from vmd.detect.classify import NullClassifier, YoloClassifier\n"
        "YoloClassifier()\n"
        "assert NullClassifier().classify(None, None) == ('', 0.0)\n"
        "assert 'ultralytics' not in sys.modules and 'torch' not in sys.modules\n"
        "print('ok')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,  # bounded: a hung import fails this test, it does not wedge the run
    )
    assert done.returncode == 0, done.stderr
    assert "ok" in done.stdout


def test_the_weights_are_opened_once_and_only_when_something_needs_naming():
    loader = CountingLoader(StubModel(boxes=[(0, 0.7)]))
    classifier = YoloClassifier(load=loader)
    assert loader.calls == 0

    classifier.classify(gradient_frame(), Box(300, 200, 40, 40))
    classifier.classify(gradient_frame(), Box(300, 200, 40, 40))
    assert loader.calls == 1


@pytest.mark.parametrize(
    "failure",
    [
        ImportError("No module named 'ultralytics'"),
        FileNotFoundError("yolo11n.pt"),
        RuntimeError("CUDA driver initialisation failed"),
    ],
)
def test_a_model_that_cannot_be_loaded_names_nothing(failure):
    classifier = YoloClassifier(load=CountingLoader(raises=failure))
    assert classifier.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED


def test_a_failed_load_is_not_retried_on_every_event():
    """Re-importing torch once per event on a machine that has no torch would
    cost more than the classification it is failing to do."""
    loader = CountingLoader(raises=ImportError("no torch"))
    classifier = YoloClassifier(load=loader)
    for _ in range(4):
        assert classifier.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED
    assert loader.calls == 1


def test_an_exception_inside_ultralytics_names_nothing():
    classifier = YoloClassifier(load=CountingLoader(StubModel(raises=RuntimeError("boom"))))
    assert classifier.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED


@pytest.mark.parametrize(
    "frame",
    [None, np.zeros((0, 0), dtype=np.uint8), np.zeros((4,), dtype=np.uint8), "not a frame"],
)
def test_a_corrupt_frame_names_nothing(frame):
    classifier = YoloClassifier(load=CountingLoader(StubModel(boxes=[(0, 0.9)])))
    assert classifier.classify(frame, Box(300, 200, 40, 40)) == UNNAMED


def test_nonsense_from_the_model_names_nothing():
    classifier = YoloClassifier(load=CountingLoader(StubModel(result=["not a result"])))
    assert classifier.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED


def test_a_class_the_model_cannot_name_is_not_named():
    """An index outside the model's own names table is a broken model, not a
    label to put in front of an operator."""
    model = StubModel(boxes=[(99, 0.9)], names={0: "person"})
    classifier = YoloClassifier(load=CountingLoader(model))
    assert classifier.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED


def test_nothing_recognised_is_not_a_failure():
    classifier = YoloClassifier(load=CountingLoader(StubModel(boxes=[])))
    assert classifier.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED


def test_the_most_confident_box_in_the_crop_wins():
    model = StubModel(boxes=[(0, 0.3), (2, 0.75)], names={0: "person", 2: "car"})
    classifier = YoloClassifier(load=CountingLoader(model))
    label, confidence = classifier.classify(gradient_frame(), Box(300, 200, 60, 60))
    assert label == "car"
    assert confidence == pytest.approx(0.75)


def test_the_model_is_given_the_crop_and_not_the_frame():
    model = StubModel(boxes=[(0, 0.5)])
    classifier = YoloClassifier(load=CountingLoader(model))
    frame = gradient_frame()
    classifier.classify(frame, Box(300, 200, 60, 60))

    crop, kwargs = model.calls[0]
    assert crop.shape[0] < frame.shape[0]
    assert crop.shape[0] == crop_rect(Box(300, 200, 60, 60), 640, 512)[2]


# --------------------------------------------------------------------------
# the budget: classification is never on the critical path
# --------------------------------------------------------------------------


def test_a_slow_classifier_is_abandoned_at_the_budget():
    inner = BlockingClassifier()
    budgeted = BudgetedClassifier(inner, budget_s=0.05)
    try:
        started = time.perf_counter()
        answer = budgeted.classify(gradient_frame(), Box(300, 200, 40, 40))
        elapsed = time.perf_counter() - started
        assert answer == UNNAMED
        # Well under the 5 s the inner classifier is prepared to block for, so
        # this fails rather than hangs if the budget stops being honoured.
        assert elapsed < 2.0
    finally:
        inner.release.set()


def test_a_classifier_still_busy_is_skipped_rather_than_queued():
    """A queue would mean the label attached to an event described a different
    event, minutes later. Skipping is the honest answer."""
    inner = BlockingClassifier()
    budgeted = BudgetedClassifier(inner, budget_s=0.05)
    try:
        budgeted.classify(gradient_frame(), Box(300, 200, 40, 40))
        assert inner.entered.wait(JOIN_TIMEOUT)
        started = time.perf_counter()
        assert budgeted.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED
        assert time.perf_counter() - started < 0.5
        assert inner.calls == 1
        assert budgeted.skipped == 1
    finally:
        inner.release.set()


def test_a_fast_classifier_delivers_its_answer():
    budgeted = BudgetedClassifier(ScriptedClassifier(("dog", 0.42)), budget_s=JOIN_TIMEOUT)
    assert budgeted.classify(gradient_frame(), Box(300, 200, 40, 40)) == ("dog", 0.42)


def test_a_classifier_that_raises_inside_the_worker_names_nothing():
    budgeted = BudgetedClassifier(
        ScriptedClassifier(raises=RuntimeError("boom")), budget_s=JOIN_TIMEOUT
    )
    assert budgeted.classify(gradient_frame(), Box(300, 200, 40, 40)) == UNNAMED


def test_a_classifier_that_answers_nonsense_names_nothing():
    for answer in (None, "person", ("person",), ("person", "very"), 7):
        budgeted = BudgetedClassifier(ScriptedClassifier(answer), budget_s=JOIN_TIMEOUT)
        assert budgeted.classify(gradient_frame(), Box(1, 1, 40, 40)) == UNNAMED


def test_the_worker_does_not_hold_the_process_open():
    """Daemon threads, deliberately: a classification still running at shutdown
    must not be able to stop the process exiting."""
    inner = BlockingClassifier()
    budgeted = BudgetedClassifier(inner, budget_s=0.05)
    try:
        budgeted.classify(gradient_frame(), Box(300, 200, 40, 40))
        assert inner.entered.wait(JOIN_TIMEOUT)
        assert all(thread.daemon for thread in threading.enumerate() if "classify" in thread.name)
    finally:
        inner.release.set()


# --------------------------------------------------------------------------
# the runner: the label is attached, and never gets a veto
# --------------------------------------------------------------------------


def test_an_unclassifiable_track_is_still_an_event(tmp_path):
    """THE test in this file. At 700 m a person is 13 pixels: unnameable, and
    still exactly what the operator needs to know about. The classifier says
    nothing and the event exists anyway, with an empty label rather than a
    guess."""
    pipeline = StubPipeline({0: [detection_at(0, box=(100, 100, 13, 13))]})
    detector, store = build_detector(
        tmp_path, gradient_frame(), pipeline, classifier=NullClassifier()
    )
    try:
        detector.step()
        events = store.recent()
        assert len(events) == 1
        assert events[0].label == ""
        assert events[0].confidence == 0.0
        assert events[0].box == (130, 100, 13, 13)
    finally:
        detector.close()
        store.close()


def test_the_runner_names_nothing_by_default(tmp_path):
    """No classifier injected means no classifier, not a model loaded behind
    the operator's back."""
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build_detector(tmp_path, gradient_frame(), pipeline)
    try:
        assert isinstance(detector.classifier, NullClassifier)
        detector.step()
        assert store.recent()[0].label == ""
    finally:
        detector.close()
        store.close()


def test_a_label_reaches_the_event(tmp_path):
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build_detector(
        tmp_path, gradient_frame(), pipeline, classifier=ScriptedClassifier(("person", 0.61))
    )
    try:
        detector.step()
        event = store.recent()[0]
        assert event.label == "person"
        assert event.confidence == pytest.approx(0.61)
    finally:
        detector.close()
        store.close()


def test_a_classifier_that_raises_does_not_lose_the_event(tmp_path):
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build_detector(
        tmp_path,
        gradient_frame(),
        pipeline,
        classifier=ScriptedClassifier(raises=RuntimeError("the model exploded")),
    )
    try:
        detector.step()
        events = store.recent()
        assert len(events) == 1
        assert events[0].label == ""
        assert events[0].confidence == 0.0
    finally:
        detector.close()
        store.close()


def test_a_slow_classifier_does_not_stall_detection(tmp_path):
    """Two frames, one event each, against a classifier that never returns.
    Detection carries on and both events are written, unnamed."""
    pipeline = StubPipeline(
        {0: [detection_at(0)], 1: [detection_at(1, box=(300, 300, 40, 40))]}
    )
    inner = BlockingClassifier()
    detector, store = build_detector(
        tmp_path,
        gradient_frame(),
        pipeline,
        classifier=BudgetedClassifier(inner, budget_s=0.05),
    )
    try:
        started = time.perf_counter()
        detector.step()
        detector.step()
        elapsed = time.perf_counter() - started
        # The inner classifier blocks for up to 5 s; two frames must cost about
        # two budgets, not two blocks.
        assert elapsed < 2.0
        events = store.recent()
        assert len(events) == 2
        assert {event.label for event in events} == {""}
        # The second event was not queued behind the first: skipped, not stored
        # up to be answered minutes later against the wrong frame.
        assert inner.calls == 1
    finally:
        inner.release.set()
        detector.close()
        store.close()


def test_the_classifier_never_suppresses(tmp_path):
    """There is no confidence below which an event is dropped. One hundredth is
    still an event, carrying the number it was given."""
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build_detector(
        tmp_path, gradient_frame(), pipeline, classifier=ScriptedClassifier(("bird", 0.01))
    )
    try:
        detector.step()
        events = store.recent()
        assert len(events) == 1
        assert events[0].label == "bird"
        assert events[0].confidence == pytest.approx(0.01)
    finally:
        detector.close()
        store.close()


def test_nonsense_from_a_classifier_does_not_reach_the_database(tmp_path):
    pipeline = StubPipeline({0: [detection_at(0)]})
    detector, store = build_detector(
        tmp_path, gradient_frame(), pipeline, classifier=ScriptedClassifier(("person", None))
    )
    try:
        detector.step()
        event = store.recent()[0]
        assert event.label == ""
        assert event.confidence == 0.0
    finally:
        detector.close()
        store.close()


def test_the_classifier_is_shown_the_frame_and_the_track_box(tmp_path):
    pipeline = StubPipeline({0: [detection_at(0)]})
    classifier = ScriptedClassifier()
    frame = gradient_frame()
    detector, store = build_detector(tmp_path, frame, pipeline, classifier=classifier)
    try:
        detector.step()
        assert len(classifier.seen) == 1
        seen_frame, seen_box = classifier.seen[0]
        assert seen_frame is frame
        assert seen_box == Box(130, 100, 40, 40)
    finally:
        detector.close()
        store.close()


def test_the_classifier_runs_once_per_event_not_once_per_frame(tmp_path):
    """The 20 ms frame budget is safe because nothing is classified on a frame
    that confirmed nothing."""
    pipeline = StubPipeline({2: [detection_at(2)]})
    classifier = ScriptedClassifier()
    detector, store = build_detector(tmp_path, gradient_frame(), pipeline, classifier=classifier)
    try:
        for _ in range(5):
            detector.step()
        assert len(pipeline.fed) == 5
        assert len(classifier.seen) == 1
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# settings: which stream, and the file that was written yesterday
# --------------------------------------------------------------------------


SETTINGS_BEFORE_THE_CLASSIFIER = """{
  "video_mode": "auto",
  "video_buffer_ms": 500,
  "camera": {
    "host": "10.0.0.2",
    "username": "",
    "password": "",
    "streams": [
      {
        "name": "ch1",
        "url": "rtsp://10.0.0.2/ch1",
        "enabled": true,
        "detect": true,
        "sensitivity": "normal",
        "ignore_regions": [],
        "horizon_y": null,
        "reader": "auto"
      }
    ]
  },
  "radio": {"host": "", "username": "", "password": "", "enabled": false},
  "storage": {
    "root": "recordings",
    "budget_gb": 100.0,
    "budget_enabled": true,
    "retention_days": null,
    "warn_at_fraction": 0.9,
    "segment_seconds": 300
  },
  "bitrate": {"mode": "auto", "floor_kbps": 1000, "ceiling_kbps": 5000, "manual_kbps": 3000},
  "detection": {"enabled": true, "classify": false, "min_travel_px": null},
  "target_distance_m": 700.0
}"""


def test_a_settings_file_written_before_the_classifier_existed_still_loads(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(SETTINGS_BEFORE_THE_CLASSIFIER, encoding="utf-8")

    settings = load_settings(path)
    stream = settings.camera.streams[0]

    assert stream.name == "ch1"
    assert stream.detect is True
    # Both new fields answered from a default, not from the file.
    assert stream.thermal is False
    assert stream.classify is None


def test_the_classifier_is_off_for_a_thermal_stream_by_default():
    """13 pixels is not identifiable, so the default is not to ask - even with
    the master switch on."""
    thermal = StreamSettings(name="ch1", url="rtsp://x", thermal=True)
    assert classify_enabled(thermal, DetectionSettings(classify=True)) is False


def test_the_classifier_is_on_for_a_visible_stream_when_the_master_switch_is_on():
    visible = StreamSettings(name="ch2", url="rtsp://x", thermal=False)
    assert classify_enabled(visible, DetectionSettings(classify=True)) is True


def test_the_master_switch_wins():
    visible = StreamSettings(name="ch2", url="rtsp://x", classify=True)
    assert classify_enabled(visible, DetectionSettings(classify=False)) is False


def test_the_operator_can_force_the_classifier_on_for_a_thermal_stream():
    """Nothing in the file says which stream is thermal except the operator, so
    the operator can also overrule the conclusion drawn from it."""
    thermal = StreamSettings(name="ch1", url="rtsp://x", thermal=True, classify=True)
    assert classify_enabled(thermal, DetectionSettings(classify=True)) is True


def test_the_operator_can_force_the_classifier_off_for_a_visible_stream():
    visible = StreamSettings(name="ch2", url="rtsp://x", classify=False)
    assert classify_enabled(visible, DetectionSettings(classify=True)) is False


def test_a_stream_named_thermal_is_not_assumed_to_be_thermal():
    """The user's camera names its streams ch1 and ch2. Guessing from a name
    would be wrong on the only camera this system has."""
    named = StreamSettings(name="thermal", url="rtsp://x")
    assert named.thermal is False
    assert classify_enabled(named, DetectionSettings(classify=True)) is True


def test_the_new_stream_fields_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.camera.streams = [
        StreamSettings(name="ch1", url="rtsp://x/ch1", detect=True, thermal=True, classify=False),
        StreamSettings(name="ch2", url="rtsp://x/ch2", detect=True),
    ]
    save_settings(settings, path)

    loaded = load_settings(path)
    assert loaded.camera.streams[0].thermal is True
    assert loaded.camera.streams[0].classify is False
    assert loaded.camera.streams[1].thermal is False
    assert loaded.camera.streams[1].classify is None
    # And the file itself says so, for an operator reading it.
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["camera"]["streams"][0]["thermal"] is True


def test_the_stream_config_carries_the_decision():
    stream = StreamSettings(name="ch1", url="rtsp://x", thermal=True)
    config = config_from_settings(stream, DetectionSettings(classify=True))
    assert config.classify is False


def test_a_stream_that_is_not_classified_gets_the_null_classifier():
    stream = StreamSettings(name="ch1", url="rtsp://x", thermal=True)
    classifier = classifier_for(stream, DetectionSettings(classify=True))
    assert isinstance(classifier, NullClassifier)


def test_a_stream_that_is_classified_gets_a_budgeted_one_that_has_loaded_nothing():
    """Building the detector must not load YOLO: the process has to start on a
    laptop where the weights are missing."""
    stream = StreamSettings(name="ch2", url="rtsp://x")
    classifier = classifier_for(stream, DetectionSettings(classify=True))
    assert isinstance(classifier, BudgetedClassifier)
    assert isinstance(classifier.inner, YoloClassifier)
    assert classifier.inner.loaded is False


def test_the_detector_process_gives_each_stream_its_own_decision(tmp_path):
    """End of the seam: what the operator typed reaches the detector that runs."""
    from vmd.detect_main import DetectionService
    from vmd.settings import CameraSettings, StorageSettings

    settings = Settings(
        camera=CameraSettings(
            streams=[
                StreamSettings(name="ch1", url="rtsp://x/ch1", detect=True, thermal=True),
                StreamSettings(name="ch2", url="rtsp://x/ch2", detect=True),
            ]
        ),
        storage=StorageSettings(root=tmp_path / "recordings"),
        detection=DetectionSettings(classify=True),
    )
    service = DetectionService(settings, endpoint_path=tmp_path / "streaming.json")
    try:
        by_stream = {d.stream: d.classifier for d in service.detectors}
        assert isinstance(by_stream["ch1"], NullClassifier)
        assert isinstance(by_stream["ch2"], BudgetedClassifier)
        # Building the service must not have loaded a model.
        assert by_stream["ch2"].inner.loaded is False
    finally:
        service.stop()


# --------------------------------------------------------------------------
# the one integration test: real weights, real label, real row
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_a_real_model_carries_a_real_label_into_a_real_event(tmp_path):
    """Proves the plumbing, not the model: a frame with something recognisable
    in it, through the real classifier, into a real events.db row."""
    pytest.importorskip("ultralytics", reason="the detect extra is not installed")
    cv2 = pytest.importorskip("cv2")
    from ultralytics.utils import ASSETS

    picture = cv2.imread(str(ASSETS / "bus.jpg"))
    if picture is None:
        pytest.skip("ultralytics' own sample images are not on disk")

    height, width = picture.shape[:2]
    frame = np.zeros((height + 200, width + 400, 3), dtype=np.uint8)
    frame[100 : 100 + height, 200 : 200 + width] = picture
    box = Box(200, 100, width, height)

    pipeline = StubPipeline({0: [Detection(track=_track_for(box), box=box, frame_index=0)]})
    classifier = BudgetedClassifier(YoloClassifier(), budget_s=120.0)
    detector, store = build_detector(tmp_path, frame, pipeline, classifier=classifier)
    try:
        detector.step()
        event = store.recent()[0]
        assert event.label != "", "the real model named nothing in its own sample image"
        assert 0.0 < event.confidence <= 1.0
        assert event.box == (box.x, box.y, box.w, box.h)
    finally:
        detector.close()
        store.close()


def _track_for(box):
    track = Track(id=1)
    track.observe(Box(box.x - 30, box.y, box.w, box.h), 0)
    track.observe(box, 0)
    return track
