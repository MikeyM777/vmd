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

**Nothing is imported here until it is asked for.** This file used to import
`motion`, and `motion` imports cv2 - so `from vmd.detect.events import
EventStore`, which is all the console and the recorder ever want from this
package and is sqlite3 and a dataclass, dragged OpenCV, numpy and eventually
the classifier into two processes that have no use for any of it. The console
has to open on a laptop where the vision stack is missing or will not load, and
what it did instead was catch the ImportError, lose the movement list and every
mark on the timeline, and say so only in the Logs tab. A console that opens
with no movement in it is worse than one that will not open, because it looks
like a quiet perimeter.

So the re-exports below are resolved on first use, through the module
`__getattr__` of PEP 562. `from vmd.detect import StreamDetector` costs exactly
what it always did; `from vmd.detect.events import EventStore` now costs
sqlite3. Both spellings go on working, and which modules a process pays for is
decided by what that process actually names.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# Name -> the module that defines it. This is the whole of the laziness: the
# module is imported the first time somebody asks for one of its names, and the
# answer is written into this module's globals so the second ask is a dict
# lookup.
_EXPORTS = {
    "BudgetedClassifier": "vmd.detect.classify",
    "Classifier": "vmd.detect.classify",
    "NullClassifier": "vmd.detect.classify",
    "YoloClassifier": "vmd.detect.classify",
    "StreamDetectionConfig": "vmd.detect.config",
    "classifier_for": "vmd.detect.config",
    "classify_enabled": "vmd.detect.config",
    "config_from_settings": "vmd.detect.config",
    "regions_of": "vmd.detect.config",
    "shapes_of": "vmd.detect.config",
    "contains": "vmd.detect.mask",
    "mask_from_areas": "vmd.detect.mask",
    "mask_from_regions": "vmd.detect.mask",
    "mask_from_shapes": "vmd.detect.mask",
    "simplify": "vmd.detect.mask",
    "sparse_outline": "vmd.detect.mask",
    "Event": "vmd.detect.events",
    "EventStore": "vmd.detect.events",
    "above_horizon": "vmd.detect.filters",
    "implausible_size": "vmd.detect.filters",
    "in_ignore_mask": "vmd.detect.filters",
    "is_global_motion": "vmd.detect.filters",
    "Box": "vmd.detect.motion",
    "MotionFinder": "vmd.detect.motion",
    "merge_overlapping": "vmd.detect.motion",
    "Detection": "vmd.detect.pipeline",
    "DetectionConfig": "vmd.detect.pipeline",
    "DetectionPipeline": "vmd.detect.pipeline",
    "StreamDetector": "vmd.detect.runner",
    "Track": "vmd.detect.tracking",
    "Tracker": "vmd.detect.tracking",
    "confirmed": "vmd.detect.tracking",
}

if TYPE_CHECKING:  # pragma: no cover - for readers and type checkers only
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
        regions_of,
        shapes_of,
    )
    from vmd.detect.events import Event, EventStore
    from vmd.detect.mask import (
        contains,
        mask_from_areas,
        mask_from_regions,
        mask_from_shapes,
        simplify,
        sparse_outline,
    )
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


def __getattr__(name: str):
    """Import the module that defines `name`, the first time it is asked for."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """What this package publishes, whether or not it has been imported yet."""
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
