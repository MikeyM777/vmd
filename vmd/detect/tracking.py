"""Associating blobs across frames, and deciding when a track is real.

The confirmation rule here is the answer to "not wind, not natural movement
like trees moving": a branch in wind produces blobs that appear, vanish and
reappear in the same place for as long as the wind blows. A person, a dog or a
vehicle travels. So a track becomes news only when it has been seen in N of the
last M frames *and* has moved further than a minimum distance.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from vmd.detect.motion import Box

# How many frames a track may go unseen before it is closed. At 25 fps this is
# under half a second - long enough to survive a blob dropping out for a frame
# or two, short enough that a person who left and came back a minute later is
# reported again rather than silently continuing an old track.
DEFAULT_MAX_GAP_FRAMES = 8

# Furthest a blob centre may move between frames and still be the same thing.
# At 700 m nothing crosses 60 px in one frame; anything that does is a second
# object, not the first one teleporting.
DEFAULT_MAX_JUMP_PX = 60

# Confirmation defaults: seen in 3 of the last 5 frames, having travelled 12 px.
DEFAULT_NEED = 3
DEFAULT_WINDOW = 5
DEFAULT_MIN_TRAVEL_PX = 12

# How many recent observations a track keeps.
#
# Some tracks never end. A track stays alive while a blob within reach turns up
# at least every `max_gap_frames`, which is exactly what foliage in wind does -
# blobs that appear, vanish and reappear in the same place for as long as the
# wind blows. It never travels far enough to be confirmed, so nothing ever
# closes it, and this process runs for months. Measured unbounded: one such
# track held 360,000 boxes after four hours at 25 fps - 31.7 MB, about 190 MB a
# day and 5.6 GB a month, per bush.
#
# Nothing downstream looks further back than the confirmation window, which is
# six frames at its widest, so 128 is two orders of magnitude of headroom over
# anything that is read - and where the track began is kept separately, because
# the travel rule needs it and it is the one observation that must never be
# thrown away.
TRACK_HISTORY = 128


@dataclass
class Track:
    """One moving thing, followed across frames."""

    id: int
    boxes: deque[Box] = field(default_factory=lambda: deque(maxlen=TRACK_HISTORY))
    seen_frames: deque[int] = field(default_factory=lambda: deque(maxlen=TRACK_HISTORY))
    first_frame: int = -1
    last_frame: int = -1
    # Where it began. Held apart from `boxes` because that is a window onto the
    # recent past and this is not allowed to fall out of it.
    first_box: Box | None = None

    def observe(self, box: Box, frame_index: int) -> None:
        if self.first_box is None:
            self.first_frame = frame_index
            self.first_box = box
        self.boxes.append(box)
        self.seen_frames.append(frame_index)
        self.last_frame = frame_index

    @property
    def box(self) -> Box:
        """Where the thing was the last time it was seen."""
        return self.boxes[-1]

    @property
    def travelled(self) -> float:
        """Distance from the first centre to the last, not the length of the path.

        Deliberately not path length. Path length accumulates from a blob that
        shivers in place, which is exactly the thing this rule exists to reject.

        Measured from `first_box` rather than from the oldest box still held: a
        long slow walk whose beginning has aged out of the window would
        otherwise read as a short one, and stop being confirmed.
        """
        if self.first_box is None or len(self.boxes) < 2:
            return 0.0
        (x0, y0), (x1, y1) = self.first_box.centre, self.boxes[-1].centre
        return math.hypot(x1 - x0, y1 - y0)


def confirmed(
    track: Track,
    need: int = DEFAULT_NEED,
    window: int = DEFAULT_WINDOW,
    min_travel_px: float = DEFAULT_MIN_TRAVEL_PX,
) -> bool:
    """True when the track has earned an event: N of the last M frames, and travel.

    The window is counted back from the last frame the track was seen on, so
    this is a property of the track and not of the clock.
    """
    if track.travelled < min_travel_px:
        return False
    recent = sum(1 for frame in track.seen_frames if frame > track.last_frame - window)
    return recent >= need


class Tracker:
    """Follows blobs from frame to frame by nearest centre.

    No motion model and no appearance model on purpose. Both are ways of being
    confidently wrong about a 13-pixel blob, and neither is needed to answer
    "did something cross the field".
    """

    def __init__(
        self,
        max_gap_frames: int = DEFAULT_MAX_GAP_FRAMES,
        max_jump_px: float = DEFAULT_MAX_JUMP_PX,
    ) -> None:
        self.max_gap_frames = max_gap_frames
        self.max_jump_px = max_jump_px
        self._tracks: list[Track] = []
        self._next_id = 1

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def update(self, boxes: list[Box], frame_index: int) -> list[Track]:
        """Match this frame's boxes to live tracks. Returns the tracks still alive."""
        # Close anything that has been missing too long, before matching, so a
        # stale track cannot claim a new blob.
        self._tracks = [
            track for track in self._tracks if frame_index - track.last_frame <= self.max_gap_frames
        ]

        # Greedy nearest-centre association: consider every pair, closest first,
        # and take each match that is close enough. Cheap, and with a handful of
        # blobs per frame the difference from an optimal assignment is nil.
        pairs = sorted(
            (
                (track.box.distance_to(box), track_index, box_index)
                for track_index, track in enumerate(self._tracks)
                for box_index, box in enumerate(boxes)
            ),
            key=lambda pair: pair[0],
        )
        taken_tracks: set[int] = set()
        taken_boxes: set[int] = set()
        for distance, track_index, box_index in pairs:
            if distance > self.max_jump_px:
                break
            if track_index in taken_tracks or box_index in taken_boxes:
                continue
            self._tracks[track_index].observe(boxes[box_index], frame_index)
            taken_tracks.add(track_index)
            taken_boxes.add(box_index)

        for box_index, box in enumerate(boxes):
            if box_index in taken_boxes:
                continue
            track = Track(id=self._next_id)
            self._next_id += 1
            track.observe(box, frame_index)
            self._tracks.append(track)

        return list(self._tracks)
