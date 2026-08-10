"""Background subtraction and blob extraction.

Everything upstream of this is a decoder; everything downstream is arithmetic.
This is the only module in the package that touches OpenCV.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

# --- tuning constants -------------------------------------------------------
#
# Every one of these was a magic number in spike/motion_crop_detect.py, which is
# where they were measured.

# How many frames MOG2 averages into its idea of "background". 500 frames is
# ~20 s at 25 fps: long enough that a person standing still for a moment does
# not become part of the scenery, short enough to follow the sun moving.
DEFAULT_HISTORY = 500

# Squared Mahalanobis distance at which a pixel stops being background. Lower
# sees fainter movement and more sensor noise.
DEFAULT_VAR_THRESHOLD = 16

# Smallest contour, in pixels of the foreground mask, worth calling a blob.
# Below this it is sensor noise, not a dog.
DEFAULT_MIN_AREA = 40

# MOG2 marks shadows 127 and real foreground 255. A shadow is not movement -
# it is the same scene with the sun behind a cloud - so only 255 survives.
FOREGROUND_VALUE = 255

# Morphology. The open removes single-pixel noise; the dilate closes the gaps
# inside one object, so a person split by a fence post is one contour.
MORPH_KERNEL_SIZE = (3, 3)
DILATE_ITERATIONS = 1


@dataclass(frozen=True)
class Box:
    """An axis-aligned box in frame coordinates. Immutable: it is a measurement."""

    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def overlaps(self, other: Box) -> bool:
        """True when the two boxes share area.

        Touching edges do not count: two boxes meeting at a border are two
        things standing next to each other.
        """
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )

    def union(self, other: Box) -> Box:
        left = min(self.x, other.x)
        top = min(self.y, other.y)
        return Box(left, top, max(self.right, other.right) - left, max(self.bottom, other.bottom) - top)

    def distance_to(self, other: Box) -> float:
        """Distance between the two centres."""
        (ax, ay), (bx, by) = self.centre, other.centre
        return math.hypot(ax - bx, ay - by)


def merge_overlapping(boxes: list[Box]) -> list[Box]:
    """Fold every group of overlapping boxes into one box.

    One person straddling two contours is one thing. Reporting it as two means
    two tracks, each of which travels and each of which alarms.

    Merging is repeated until nothing changes, because absorbing A into B can
    make B reach C.
    """
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        result: list[Box] = []
        for box in merged:
            for index, existing in enumerate(result):
                if box.overlaps(existing):
                    result[index] = existing.union(box)
                    changed = True
                    break
            else:
                result.append(box)
        merged = result
    return merged


class MotionFinder:
    """Turns frames into the boxes of whatever moved in them.

    Stateful by nature: the background model is the state. One instance per
    stream, fed every frame in order.
    """

    def __init__(
        self,
        history: int = DEFAULT_HISTORY,
        var_threshold: int = DEFAULT_VAR_THRESHOLD,
        min_area: int = DEFAULT_MIN_AREA,
    ) -> None:
        self.history = history
        self.var_threshold = var_threshold
        self.min_area = min_area
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, MORPH_KERNEL_SIZE)
        self._subtractor = self._new_subtractor()

    def _new_subtractor(self):
        return cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=True,
        )

    def reset(self) -> None:
        """Throw the background model away.

        Called when the scene is known to have changed underneath us - after a
        PTZ move, for instance. Without it MOG2 spends `history` frames
        unlearning a view the camera is no longer pointing at.
        """
        self._subtractor = self._new_subtractor()

    def blobs(self, frame: np.ndarray) -> list[Box]:
        """The boxes of everything that moved in this frame, overlaps merged."""
        grey = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        mask = self._subtractor.apply(grey)
        mask[mask < FOREGROUND_VALUE] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=DILATE_ITERATIONS)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = [
            Box(*(int(v) for v in cv2.boundingRect(contour)))
            for contour in contours
            if cv2.contourArea(contour) >= self.min_area
        ]
        return merge_overlapping(boxes)
