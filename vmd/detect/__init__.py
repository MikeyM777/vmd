"""Movement detection: frames in, confirmed tracks out, events on disk.

The pipeline - motion, filters, tracking, pipeline - opens nothing. It is
arithmetic over numpy arrays, which is why the whole of it can be tested with
synthetic frames in milliseconds. Exactly two modules here touch the world, and
they are the two that have to: `runner` opens the stream, `events` opens the
database. Everything they talk to is injected, so they are tested without a
camera, a socket or a second of real time.

The rule the whole package exists to serve: report that *something* moved. Not
what it was - at 700 m a person is about 13 pixels and no classifier will name
it, and the operator needs to know anyway.
"""

from vmd.detect.classify import (
    BudgetedClassifier,
    Classifier,
    NullClassifier,
    YoloClassifier,
)
from vmd.detect.config import (
    StreamDetectionConfig,
    classifier_for,
    classify_enabled,
    config_from_settings,
    mask_from_regions,
    regions_of,
)
from vmd.detect.events import Event, EventStore
from vmd.detect.filters import (
    above_horizon,
    implausible_size,
    in_ignore_mask,
    is_global_motion,
)
from vmd.detect.motion import Box, MotionFinder, merge_overlapping
from vmd.detect.pipeline import Detection, DetectionConfig, DetectionPipeline
from vmd.detect.runner import StreamDetector
from vmd.detect.tracking import Track, Tracker, confirmed

__all__ = [
    "Box",
    "BudgetedClassifier",
    "Classifier",
    "Detection",
    "DetectionConfig",
    "DetectionPipeline",
    "Event",
    "EventStore",
    "MotionFinder",
    "NullClassifier",
    "StreamDetectionConfig",
    "StreamDetector",
    "Track",
    "Tracker",
    "YoloClassifier",
    "above_horizon",
    "classifier_for",
    "classify_enabled",
    "config_from_settings",
    "confirmed",
    "implausible_size",
    "in_ignore_mask",
    "is_global_motion",
    "mask_from_regions",
    "merge_overlapping",
    "regions_of",
]
