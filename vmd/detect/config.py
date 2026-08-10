"""From what the operator typed to what the pipeline understands.

The pipeline knows nothing about pydantic, JSON or files, and must not: it is
arithmetic over arrays. This is the one place the two meet.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

import numpy as np

from vmd.detect.classify import BudgetedClassifier, NullClassifier, YoloClassifier
from vmd.detect.pipeline import PRESETS, DetectionConfig, Tuning


@dataclass
class StreamDetectionConfig(DetectionConfig):
    """A stream's config, with the operator's one permitted override.

    Sensitivity is a preset because the numbers inside it are not independent.
    Minimum travel is the single exception: it is the wind rule, it is the thing
    an operator can actually observe going wrong ("that bush keeps setting it
    off"), and it can be changed without making any other number a lie.
    """

    min_travel_px: float | None = None

    # Whether this stream's confirmed tracks are shown to the classifier. It
    # changes nothing about which tracks become events; see classify_enabled.
    classify: bool = False

    @property
    def tuning(self) -> Tuning:
        preset = PRESETS[self.sensitivity]
        if self.min_travel_px is None:
            return preset
        return replace(preset, min_travel_px=self.min_travel_px)


def classify_enabled(stream, detection) -> bool:
    """Does the classifier run on this stream?

    Two questions, in order:

    1. Is it on at all? `detection.classify` is the master switch, off by
       default, because the weights are an optional install and a labelled
       event is a convenience where an event is the point.
    2. Then: what did the operator say about *this* stream? `stream.classify`
       is the answer when it is set. When it is None - which is what every
       existing settings file says - the answer is drawn from the one fact the
       operator was asked for: a thermal stream is not classified, because at
       700 m a person is 13 pixels and a model trained on photographs has
       nothing to say about that.

    Nothing here can stop a confirmed track becoming an event. This decides
    only whether the event arrives with a name attached.
    """
    if not detection.classify:
        return False
    if stream.classify is not None:
        return bool(stream.classify)
    return not stream.thermal


def classifier_for(stream, detection):
    """The classifier this stream's detector should be given.

    Returns a `NullClassifier` when classification is off - not None - so the
    runner has one code path and no `if` around the thing that must never stop
    an event being written.

    The YOLO classifier is constructed but loads nothing: importing ultralytics
    is deferred to the first crop worth naming, so the detection process starts
    on a laptop where torch is not installed and the weights are not on disk.
    It is wrapped in a budget, because the detection loop must never wait on a
    model.
    """
    if not classify_enabled(stream, detection):
        return NullClassifier()
    return BudgetedClassifier(YoloClassifier())


def config_from_settings(stream, detection) -> StreamDetectionConfig:
    """Build one stream's detection config.

    The ignore mask is deliberately left unbuilt. A mask is an array the size of
    a frame, and nothing here knows how big a frame is until one has arrived -
    guessing would produce a mask that silently misses, or silently covers, the
    wrong part of the picture. The runner paints it when it sees the first
    frame, from `stream.ignore_regions`.
    """
    return StreamDetectionConfig(
        sensitivity=stream.sensitivity,
        horizon_y=stream.horizon_y,
        min_travel_px=detection.min_travel_px,
        classify=classify_enabled(stream, detection),
    )


def regions_of(stream) -> list[tuple[int, int, int, int]]:
    """The stream's ignore rectangles, as plain tuples the detector can use."""
    return [region.as_tuple() for region in stream.ignore_regions]


def mask_from_regions(
    regions: Iterable[Sequence[int]], width: int, height: int
) -> np.ndarray | None:
    """Paint the ignore rectangles into a mask the size of the frame.

    Regions are clipped rather than trusted. The operator painted them against
    whatever resolution the console was showing, and the stream can change
    resolution without asking - so a rectangle hanging off the edge must cover
    the part of the frame it still overlaps, not raise, and not wrap around.

    Returns None when nothing survives clipping, because None is what the
    pipeline reads as "no mask" and an all-zero array would cost a comparison
    per blob for no reason.
    """
    mask = np.zeros((int(height), int(width)), dtype=np.uint8)
    painted = False
    for region in regions:
        x, y, w, h = (int(v) for v in region)
        left = max(x, 0)
        top = max(y, 0)
        right = min(x + w, int(width))
        bottom = min(y + h, int(height))
        if right <= left or bottom <= top:
            continue
        mask[top:bottom, left:right] = 255
        painted = True
    return mask if painted else None
