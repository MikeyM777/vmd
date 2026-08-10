"""Movement detection: frames in, confirmed tracks out.

Nothing in this package opens a camera, a file, a socket or a database. It is
arithmetic over numpy arrays, which is why the whole of it can be tested with
synthetic frames in milliseconds. The process that reads RTSP and writes events
lives elsewhere and imports this.

The rule the whole package exists to serve: report that *something* moved. Not
what it was - at 700 m a person is about 13 pixels and no classifier will name
it, and the operator needs to know anyway.
"""

from vmd.detect.filters import (
    above_horizon,
    implausible_size,
    in_ignore_mask,
    is_global_motion,
)
from vmd.detect.motion import Box, MotionFinder, merge_overlapping
from vmd.detect.pipeline import Detection, DetectionConfig, DetectionPipeline
from vmd.detect.tracking import Track, Tracker, confirmed

__all__ = [
    "Box",
    "Detection",
    "DetectionConfig",
    "DetectionPipeline",
    "MotionFinder",
    "Track",
    "Tracker",
    "above_horizon",
    "confirmed",
    "implausible_size",
    "in_ignore_mask",
    "is_global_motion",
    "merge_overlapping",
]
