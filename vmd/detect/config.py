"""From what the operator typed to what the pipeline understands.

The pipeline knows nothing about pydantic, JSON or files, and must not: it is
arithmetic over arrays. This is the one place the two meet.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from vmd.detect.classify import BudgetedClassifier, NullClassifier, YoloClassifier
from vmd.detect.mask import mask_from_areas, mask_from_regions, mask_from_shapes
from vmd.detect.pipeline import PRESETS, DetectionConfig, Tuning

# Painting a mask moved to `vmd/detect/mask.py`, where the drawn outlines are,
# because the two have to agree about what "inside" means and a settings file
# carries both. Re-exported here: this is where every caller has always found
# it, and a rename is churn in six files that changes nothing.
__all__ = [
    "StreamDetectionConfig",
    "classifier_for",
    "classify_enabled",
    "config_from_settings",
    "mask_from_areas",
    "mask_from_regions",
    "mask_from_shapes",
    "regions_of",
    "shapes_of",
]


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
    """Does the classifier run on this stream? No. It never runs.

    The operator asked for it to stop: "I need movement notifications, but not
    accurate identification." He is right, and the arithmetic was always
    against it - at 700 m a person is about 13 pixels across on the thermal
    head, and a model trained on photographs has nothing to say about that.
    What it bought him was a guess he could not check, on events he was being
    told about anyway.

    So this returns False, and the two controls that used to feed it are off
    the settings form. Nothing here ever decided whether a confirmed track
    became an event; it decided only whether the event arrived with a name
    attached, and now none of them do.

    `stream.classify` and `detection.classify` stay in the model on purpose:
    every settings file in existence has them, and a field removed from the
    model is a file that stops loading on a machine with no terminal. They are
    read by nothing. Taken and ignored here rather than dropped from the
    signature, because three callers pass them and a signature change would be
    churn in every one of them for an argument that is now always ignored.
    """
    del stream, detection
    return False


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


def shapes_of(stream) -> list[list[tuple[int, int]]]:
    """The stream's drawn outlines, as plain points the detector can use.

    Beside the rectangles and not instead of them. Every area the operator has
    marked out until now is a rectangle, and both go to the detector so that a
    settings file written before he could draw one keeps meaning what it meant.
    """
    return [shape.as_tuples() for shape in stream.ignore_shapes]
