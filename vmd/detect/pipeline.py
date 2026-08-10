"""The whole movement pipeline, wired together and still without any I/O.

    frame -> blobs -> global-motion check -> per-box filters -> tracker -> confirmation

Feed it frames, get back the tracks that became real on this frame. What reads
the frames from a camera, and what writes the events to a database, live
elsewhere; this file must stay testable with numpy in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from vmd.detect.filters import (
    GLOBAL_MOTION_FRACTION,
    MAX_HEIGHT_FRACTION,
    above_horizon,
    implausible_size,
    in_ignore_mask,
    is_global_motion,
)
from vmd.detect.motion import Box, MotionFinder
from vmd.detect.tracking import (
    DEFAULT_MAX_GAP_FRAMES,
    DEFAULT_MAX_JUMP_PX,
    Track,
    Tracker,
    confirmed,
)

Sensitivity = Literal["low", "normal", "high"]


@dataclass(frozen=True)
class Tuning:
    """The numbers behind one sensitivity setting.

    They exist as a group because they are not independent: seeing fainter
    movement means smaller blobs, which means shorter travel before a track is
    believable. Exposing them as five sliders would mean five ways to make the
    detector useless, and no way to explain any of them to an operator.
    """

    var_threshold: int  # MOG2: how different a pixel must be to be foreground
    min_area: int  # smallest contour worth calling a blob, in mask pixels
    need: int  # frames seen, out of `window`, before a track is real
    window: int  # how far back the N-of-M count looks
    min_travel_px: float  # how far it must have moved. The wind rule.
    min_height_fraction: float  # smallest believable blob, as a share of frame height


# One control, three positions. High is for a quiet thermal view of open ground
# where the operator would rather see a rabbit than miss a person; low is for a
# view with a road, a treeline or a flag in it.
PRESETS: dict[str, Tuning] = {
    "low": Tuning(
        var_threshold=40,
        min_area=150,
        need=4,
        window=6,
        min_travel_px=24,
        min_height_fraction=0.03,
    ),
    "normal": Tuning(
        var_threshold=16,
        min_area=40,
        need=3,
        window=5,
        min_travel_px=12,
        min_height_fraction=0.015,
    ),
    "high": Tuning(
        var_threshold=8,
        min_area=10,
        need=3,
        window=6,
        min_travel_px=8,
        min_height_fraction=0.005,
    ),
}


@dataclass
class DetectionConfig:
    """What the operator configures, per stream.

    Everything here is either a single choice (sensitivity) or a fact about the
    view (where the ground is, which patch to ignore). Whether a confirmed track
    raises an event is not configurable: it always does.
    """

    sensitivity: Sensitivity = "normal"

    # Operator-painted region, non-zero where movement is to be ignored. The
    # only reliable answer to one specific swaying tree.
    ignore_mask: np.ndarray | None = None

    # Where the ground stops in this view. None disables the bird rule, which is
    # the right default: a wrong horizon silently deletes real detections.
    horizon_y: int | None = None

    max_height_fraction: float = MAX_HEIGHT_FRACTION
    global_motion_fraction: float = GLOBAL_MOTION_FRACTION
    max_gap_frames: int = DEFAULT_MAX_GAP_FRAMES
    max_jump_px: float = DEFAULT_MAX_JUMP_PX

    @property
    def tuning(self) -> Tuning:
        return PRESETS[self.sensitivity]


@dataclass(frozen=True)
class Detection:
    """A track that just became real, and where it was when it did."""

    track: Track
    box: Box
    frame_index: int


class DetectionPipeline:
    """Frames in, confirmed tracks out. One instance per stream."""

    def __init__(self, config: DetectionConfig | None = None) -> None:
        self.config = config or DetectionConfig()
        tuning = self.config.tuning
        self.motion = MotionFinder(
            var_threshold=tuning.var_threshold,
            min_area=tuning.min_area,
        )
        self.tracker = Tracker(
            max_gap_frames=self.config.max_gap_frames,
            max_jump_px=self.config.max_jump_px,
        )
        # Ids already reported, so an alarm fires when the track becomes real
        # and not once per frame for as long as it walks. Pruned to the live
        # tracks each frame, and ids are never reused, so it cannot grow.
        self._reported: set[int] = set()
        self.frames_suppressed = 0  # frames thrown away as camera movement

    def reset(self) -> None:
        """Forget the scene. For after a PTZ move, when the view is a new one."""
        self.motion.reset()

    def feed(self, frame: np.ndarray, frame_index: int) -> list[Detection]:
        """Process one frame. Returns the tracks confirmed on *this* frame."""
        height, width = frame.shape[:2]
        boxes = self.motion.blobs(frame)

        # Cheapest first, and the cheapest of all is deciding the whole frame is
        # untrustworthy: when the camera moved, no blob in it means anything.
        if is_global_motion(boxes, (width, height), self.config.global_motion_fraction):
            self.frames_suppressed += 1
            boxes = []
        else:
            boxes = [box for box in boxes if self._keep(box, height)]

        live = self.tracker.update(boxes, frame_index)
        live_ids = {track.id for track in live}
        self._reported &= live_ids

        tuning = self.config.tuning
        detections: list[Detection] = []
        for track in live:
            if track.last_frame != frame_index or track.id in self._reported:
                continue
            if confirmed(track, tuning.need, tuning.window, tuning.min_travel_px):
                self._reported.add(track.id)
                detections.append(Detection(track=track, box=track.box, frame_index=frame_index))
        return detections

    def _keep(self, box: Box, frame_height: int) -> bool:
        tuning = self.config.tuning
        if in_ignore_mask(box, self.config.ignore_mask):
            return False
        if above_horizon(box, self.config.horizon_y):
            return False
        if implausible_size(
            box,
            frame_height,
            tuning.min_height_fraction,
            self.config.max_height_fraction,
        ):
            return False
        return True
