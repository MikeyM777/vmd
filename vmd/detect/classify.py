"""Naming what moved - optionally, cheaply, and with no power to stop it.

The rule this file exists to keep, and the reason every path in it ends in
`("", 0.0)`: **a track the classifier cannot name is still an event.** At 700 m
a person is about 13 pixels on the thermal sensor - unnameable by a model
trained on photographs, and still exactly what the operator needs to know
about. Missing weights, missing torch, no CUDA, a corrupt frame, an exception
inside ultralytics: each of those is an unnamed event, never a lost one.

Three things live here:

* `NullClassifier` - the default, and the whole of the thermal answer.
* `YoloClassifier` - crops the box out of the full-resolution frame and asks
  YOLO11n what it is. Imports nothing until the first thing worth naming
  arrives, so the console can import this module on a laptop with no torch
  installed and get a working detector out of it.
* `BudgetedClassifier` - a wrapper that puts the whole of the above on a
  daemon thread with a deadline, so the detection loop can never wait on it.

The geometry constants below were measured in `spike/alarm_demo.py`, which is
where motion-gated crop detection was shown to beat full-frame YOLO on every
axis: 46x fewer detections of parked cars, 14x faster, and better recall on
small distant figures, because a 40-pixel person survives cropping and does not
survive a whole frame being squeezed to 640.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import numpy as np

from vmd import app_folder
from vmd.detect.motion import Box

logger = logging.getLogger(__name__)

# What every path that cannot say anything returns. Never None: a caller that
# has to defend against None is a caller that will one day forget to.
UNNAMED: tuple[str, float] = ("", 0.0)

# The crop is a square `CROP_PAD` times the box's longer side, centred on the
# box. Measured in spike/alarm_demo.py and unchanged here. Padding matters
# because background subtraction bounds the *moving* pixels, not the object: a
# walking person's trailing leg drops out of the mask on half the frames, and a
# box tight to the mask hands the model a cropped photograph of a torso.
CROP_PAD = 1.8

# ...and never smaller than this, whatever the box says. A 40-pixel crop
# contains 40 pixels of information however it is resized; the surrounding
# ground, sky and treeline are what let the model place the thing at all.
MIN_CROP_PX = 112

# The longer side a blob must have, in native frame pixels, before it is worth
# asking at all. The measured recall curve starts at 35 px (93% on MEVA at
# 35-78 px); at 13 px - a person at 700 m on the thermal - nothing was ever
# named. 20 px sits below anything ever measured to work and above the size
# that only ever costs 40 ms to be told nothing, so it can skip hopeless calls
# without being able to skip useful ones. It decides whether to *ask*. It does
# not decide whether there is an event: nothing here does.
MIN_BLOB_PX = 20

# What the crop is resized to inside the model. 320 is what the measured alarm
# pipeline used; on this machine it costs ~39 ms against ~54 ms at 448.
CROP_IMGSZ = 320

# Below this the model is not confident enough to have said anything. It is a
# floor on *what the model reports*, never a floor on whether an event exists.
MIN_CONFIDENCE = 0.25

# The weights file, by name. Resolved to an absolute path beside the
# application rather than trusted as a relative name: a bare name is resolved
# against the working directory, and the working directory is only the app
# folder because both launchers happen to set it.
DEFAULT_WEIGHTS = "yolo11n.pt"

# CPU by default. The laptop this runs on has no usable GPU, and asking for one
# that is not there is an exception inside ultralytics on the first event.
DEFAULT_DEVICE = "cpu"

# How long the detection loop will wait for a name before writing the event
# without one. Measured cost of one warm classification on this machine: 39 ms
# median, 111 ms worst seen. 200 ms is a few times the measurement - so a
# healthy classifier nearly always delivers - and still only about five frames
# at 25 fps, which is the whole cost of the worst case. The first call also
# pays for loading the model (133 ms warm, seconds if the weights must be
# fetched), so the first event after a start is usually unnamed. That is the
# right trade: the alternative is the detection loop stopping for seconds.
DEFAULT_BUDGET_S = 0.2


@runtime_checkable
class Classifier(Protocol):
    """Given a frame and a box, say what is in it - or say nothing.

    `("", 0.0)` means "no name", and it is not an error. It is the expected
    answer for most of what this system sees.
    """

    def classify(self, frame, box: Box) -> tuple[str, float]: ...


def named(value: Any) -> tuple[str, float]:
    """Coerce whatever a classifier returned into a label and a confidence.

    Anything that is not a (str, number) pair is treated as no answer. A
    classifier that returns nonsense must produce an unnamed event, not a
    traceback in the middle of writing one.
    """
    try:
        label, confidence = value
        if not isinstance(label, str):
            return UNNAMED
        confidence = float(confidence)
    except (TypeError, ValueError):
        return UNNAMED
    if not label or confidence != confidence:  # NaN
        return UNNAMED
    return (label, confidence)


class NullClassifier:
    """Names nothing, ever. The default, and the default for thermal.

    Not a placeholder: on the thermal stream at 700 m this is the correct
    classifier, and the events it produces are complete.
    """

    def classify(self, frame, box: Box) -> tuple[str, float]:
        return UNNAMED


# -- the crop ---------------------------------------------------------------


def crop_rect(
    box: Box,
    width: int,
    height: int,
    pad: float = CROP_PAD,
    min_side: int = MIN_CROP_PX,
) -> tuple[int, int, int]:
    """Where to cut: `(left, top, side)` of a square crop inside the frame.

    Square because the model letterboxes anyway, and a square crop of a walking
    person keeps the ground under the feet. Clamped rather than trusted: a box
    at the edge of the frame must produce a crop that is inside the frame, not
    a negative index that silently wraps round to the other side of the image.
    """
    side = int(max(box.w, box.h) * pad)
    side = max(side, min_side)
    side = min(side, int(width), int(height))
    centre_x, centre_y = box.centre
    left = int(centre_x - side / 2)
    top = int(centre_y - side / 2)
    left = min(max(left, 0), int(width) - side)
    top = min(max(top, 0), int(height) - side)
    return left, top, side


def crop_for(frame, box: Box, pad: float = CROP_PAD, min_side: int = MIN_CROP_PX):
    """The crop itself, at the frame's own resolution. None if there is none.

    Nothing here resizes. That is the measured point of the whole approach: a
    40-pixel person survives being cropped out of a 4K frame and does not
    survive that frame being scaled to 640.
    """
    if frame is None or not hasattr(frame, "shape") or frame.ndim < 2:
        return None
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return None
    left, top, side = crop_rect(box, width, height, pad, min_side)
    if side <= 0:
        return None
    return frame[top : top + side, left : left + side]


# -- the real thing ---------------------------------------------------------


def weights_path(weights: str | Path = DEFAULT_WEIGHTS) -> Path:
    """Where the weights are, as an absolute path.

    A name with no directory in it is looked for beside the application, not in
    whatever directory the process happens to have been started in.
    """
    candidate = Path(weights)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate
    return app_folder() / candidate


def load_yolo(weights: str | Path = DEFAULT_WEIGHTS):
    """Import ultralytics and open the weights. Called once, on demand.

    Deliberately a module-level function rather than an import at the top of
    the file: importing ultralytics imports torch, which costs seconds and is
    not installed on a machine that only records.

    The file is checked before ultralytics is imported, and that order is the
    point. Handed a name it cannot find, ultralytics recognises `yolo11n.pt` as
    one of its own published assets and fetches it from github.com, three times
    over. This machine is offline by design and has no business trying: a
    missing weights file has to be a sentence the operator can read, and
    unlabelled events, not a download that cannot finish.
    """
    path = weights_path(weights)
    if not path.exists():
        raise FileNotFoundError(
            f"there are no classifier weights at {path}, so movement will be "
            f"reported without labels. Nothing will be downloaded - this "
            f"machine is offline on purpose. Copy {Path(weights).name} to that "
            f"path to have events named."
        )

    from ultralytics import YOLO

    return YOLO(str(path))


class YoloClassifier:
    """Crops the box out of the frame and asks YOLO11n what it is.

    Every failure is an unnamed event. That is not defensiveness for its own
    sake: this object sits between a confirmed track and the row that tells the
    operator something crossed the perimeter, and it must be incapable of
    stopping that row being written.
    """

    def __init__(
        self,
        weights: str = DEFAULT_WEIGHTS,
        imgsz: int = CROP_IMGSZ,
        confidence: float = MIN_CONFIDENCE,
        device: str = DEFAULT_DEVICE,
        pad: float = CROP_PAD,
        min_crop_px: int = MIN_CROP_PX,
        min_blob_px: int = MIN_BLOB_PX,
        load: Callable[[str], Any] = load_yolo,
    ) -> None:
        self.weights = weights
        self.imgsz = imgsz
        self.confidence = confidence
        self.device = device
        self.pad = pad
        self.min_crop_px = min_crop_px
        self.min_blob_px = min_blob_px
        self._load = load
        self._model = None
        # Set once the load has failed, so a laptop with no torch is not made
        # to import torch again on every event for the rest of the night.
        self._unavailable = False

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def model(self):
        """The loaded model, or None if it cannot be had. Never raises."""
        if self._model is not None or self._unavailable:
            return self._model
        try:
            self._model = self._load(self.weights)
        except FileNotFoundError as exc:
            # Not a fault to be diagnosed from a stack trace. The weights are an
            # optional install, and their absence has one plain consequence the
            # operator can act on, so it is said once as a sentence.
            self._unavailable = True
            logger.warning("%s", exc)
            return None
        except BaseException:  # noqa: BLE001 - including SystemExit from a broken install
            self._unavailable = True
            logger.exception(
                "the classifier could not be loaded from %s; movement will be "
                "reported without labels",
                self.weights,
            )
            return None
        logger.info("classifier loaded from %s", self.weights)
        return self._model

    def classify(self, frame, box: Box) -> tuple[str, float]:
        try:
            if max(box.w, box.h) < self.min_blob_px:
                # Too small to be a photograph of anything. Asking costs 40 ms
                # and cannot answer.
                return UNNAMED
            crop = crop_for(frame, box, self.pad, self.min_crop_px)
            if crop is None or crop.size == 0:
                return UNNAMED
            model = self.model()
            if model is None:
                return UNNAMED
            results = model.predict(
                crop,
                imgsz=self.imgsz,
                conf=self.confidence,
                verbose=False,
                device=self.device,
            )
            return self._best(model, results)
        except BaseException:  # noqa: BLE001 - a name is never worth an event
            logger.exception("classifying a %dx%d crop failed; the event is unnamed", box.w, box.h)
            return UNNAMED

    def _best(self, model, results) -> tuple[str, float]:
        """The most confident thing in the crop, or nothing.

        Most confident rather than largest or nearest: with one moving blob per
        crop there is usually one candidate, and when there are two the model's
        own ranking is the only information available.
        """
        best_label, best_confidence = UNNAMED
        names = getattr(model, "names", {}) or {}
        for result in results or ():
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue
            classes = np.asarray(boxes.cls).reshape(-1)
            confidences = np.asarray(boxes.conf).reshape(-1)
            for index, confidence in zip(classes, confidences):
                confidence = float(confidence)
                if confidence <= best_confidence:
                    continue
                label = names.get(int(index)) if hasattr(names, "get") else None
                if not label:
                    # A class index the model has no name for is a broken
                    # model, not a label to put in front of an operator.
                    continue
                best_label, best_confidence = str(label), confidence
        return named((best_label, best_confidence))


# -- keeping it off the critical path ---------------------------------------


class _Job:
    """One classification in flight, and somewhere to put its answer."""

    __slots__ = ("done", "result")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result = UNNAMED


class BudgetedClassifier:
    """Any classifier, on a daemon thread, with a deadline and no queue.

    Two rules, and the second is the one that matters:

    * **Deadline.** The caller waits `budget_s` for an answer and then writes
      the event without one. The work carries on; its answer is discarded.
    * **Skip if busy.** While a classification is still running, the next one
      is not queued - it is skipped. A queue would attach a label produced from
      one frame to an event that happened minutes later, which is worse than no
      label, and it would grow without bound behind a wedged model.

    The worker is a daemon thread rather than a pool, so a classification still
    running at shutdown cannot hold the process open - `concurrent.futures`
    joins its threads at interpreter exit, which is exactly the hang this
    process must not have.

    One instance per stream: a detector's calls are serialised by its own loop.
    """

    def __init__(self, inner: Classifier, budget_s: float = DEFAULT_BUDGET_S) -> None:
        self.inner = inner
        self.budget_s = budget_s
        self.skipped = 0
        self.timed_out = 0
        self._busy = threading.Event()
        self._lock = threading.Lock()

    def classify(self, frame, box: Box) -> tuple[str, float]:
        with self._lock:
            if self._busy.is_set():
                self.skipped += 1
                return UNNAMED
            self._busy.set()
            job = _Job()
            threading.Thread(
                target=self._work,
                args=(job, frame, box),
                name=f"classify-{id(job):x}",
                daemon=True,
            ).start()

        if not job.done.wait(self.budget_s):
            self.timed_out += 1
            logger.debug("the classifier did not answer within %.2f s; unnamed", self.budget_s)
            return UNNAMED
        return job.result

    def _work(self, job: _Job, frame, box: Box) -> None:
        try:
            job.result = named(self.inner.classify(frame, box))
        except BaseException:  # noqa: BLE001 - the worker is the last line of defence
            logger.exception("the classifier failed; the event is unnamed")
            job.result = UNNAMED
        finally:
            # Cleared before the answer is published, so the caller that
            # collects it can immediately ask another question.
            self._busy.clear()
            job.done.set()
