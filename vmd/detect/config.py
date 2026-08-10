"""From what the operator typed to what the pipeline understands.

The pipeline knows nothing about pydantic, JSON or files, and must not: it is
arithmetic over arrays. This is the one place the two meet.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

import numpy as np

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

    @property
    def tuning(self) -> Tuning:
        preset = PRESETS[self.sensitivity]
        if self.min_travel_px is None:
            return preset
        return replace(preset, min_travel_px=self.min_travel_px)


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
