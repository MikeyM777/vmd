"""The rules that decide what is not worth reporting.

Pure functions over boxes and frame dimensions. No OpenCV, no state, no I/O -
these are geometry, and they are tested as geometry.

They are applied cheapest first, and each one answers a specific complaint the
owner made about the alarms he does not want: not wind, not trees, not birds.
"""

from __future__ import annotations

import numpy as np

from vmd.detect.motion import Box

# Fraction of the frame that has to be moving before we conclude the camera
# moved rather than the world. Measured in spike/motion_crop_detect.py: real
# subjects at this range never approach it, and a PTZ pan blows straight past it.
GLOBAL_MOTION_FRACTION = 0.35

# A blob shorter than this fraction of the frame height is noise; taller than
# the other is a lighting change, a wiper, or a lens flare. A person at 700 m on
# a 512-line thermal sensor is about 13 px - 0.025 of the frame - so the small
# end has to stay genuinely small.
MIN_HEIGHT_FRACTION = 0.015
MAX_HEIGHT_FRACTION = 0.6


def in_ignore_mask(box: Box, mask: np.ndarray | None) -> bool:
    """True when the box's centre falls in an operator-painted region.

    The mask is any 2-D array the size of the frame; non-zero means ignore.
    Judged by the centre rather than by overlap, so a box that merely brushes
    the edge of a masked tree is still reported - the mask is meant to silence
    a specific thing, not everything near it.
    """
    if mask is None:
        return False
    cx, cy = box.centre
    row, column = int(cy), int(cx)
    height, width = mask.shape[:2]
    if not (0 <= row < height and 0 <= column < width):
        return False
    return bool(mask[row, column])


def above_horizon(box: Box, horizon_y: int | None) -> bool:
    """The bird rule, stated honestly.

    Above the horizon line nothing is ground traffic. A box whose lowest point
    is still above the line cannot be standing on the ground, so it is a bird,
    an insect near the lens, or a cloud. A box that straddles the line has its
    feet below it and is kept.
    """
    if horizon_y is None:
        return False
    return box.bottom <= horizon_y


def implausible_size(
    box: Box,
    frame_height: int,
    min_fraction: float = MIN_HEIGHT_FRACTION,
    max_fraction: float = MAX_HEIGHT_FRACTION,
) -> bool:
    """True when the blob is far too large or far too small for the frame.

    Height rather than area, because height is what scales with distance and
    area punishes a wide, low thing (a vehicle) for being wide.
    """
    return box.h < min_fraction * frame_height or box.h > max_fraction * frame_height


def is_global_motion(
    boxes: list[Box],
    frame_size: tuple[int, int],
    fraction: float = GLOBAL_MOTION_FRACTION,
) -> bool:
    """True when so much of the frame is moving that the camera must have moved.

    A PTZ slew, or wind shaking the mast. When this fires, *every* blob in the
    frame is discarded: none of them can be trusted, and reporting them all is
    the fastest way to teach an operator to ignore the system.

    The moving area is the sum of the box areas, which is the union because the
    finder has already merged everything that overlaps.
    """
    width, height = frame_size
    if not boxes or width <= 0 or height <= 0:
        return False
    moving = sum(box.area for box in boxes)
    return moving > fraction * width * height
