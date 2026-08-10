"""Background subtraction and blob extraction, on synthetic frames.

Every frame here is drawn with numpy: a flat grey background with rectangles on
it. No video file, no camera - the whole point of the detect package is that it
is testable in milliseconds.
"""

import numpy as np

from vmd.detect.motion import Box, MotionFinder, merge_overlapping

# Synthetic scene constants. Grey enough that a rectangle can be drawn brighter
# or fainter than the background without clipping at 0 or 255.
BACKGROUND_GREY = 100
MOVER_GREY = 220
FRAME_W, FRAME_H = 320, 240


def grey_frame(width: int = FRAME_W, height: int = FRAME_H) -> np.ndarray:
    """A flat grey single-channel frame - the empty scene."""
    return np.full((height, width), BACKGROUND_GREY, dtype=np.uint8)


def draw_rect(frame: np.ndarray, x: int, y: int, w: int, h: int, value: int = MOVER_GREY) -> None:
    """Fill a rectangle in place. Clipped to the frame, so callers can walk off the edge."""
    height, width = frame.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, width), min(y + h, height)
    if x1 > x0 and y1 > y0:
        frame[y0:y1, x0:x1] = value


def warm(finder: MotionFinder, frames: int = 8, width: int = FRAME_W, height: int = FRAME_H) -> None:
    """Let the subtractor learn an empty scene before anything moves."""
    for _ in range(frames):
        finder.blobs(grey_frame(width, height))


def test_box_geometry():
    box = Box(10, 20, 30, 40)
    assert box.area == 1200
    assert box.centre == (25.0, 40.0)


def test_boxes_overlap_or_do_not():
    a = Box(0, 0, 10, 10)
    assert a.overlaps(Box(5, 5, 10, 10))
    assert not a.overlaps(Box(20, 0, 10, 10))
    # Edge-touching is not overlapping: two boxes sharing a border are two things.
    assert not a.overlaps(Box(10, 0, 10, 10))


def test_overlapping_contours_become_one_blob():
    """Two overlapping shapes are one moving thing, not two.

    A person straddling two contours must not be reported twice, and must not
    be reported as two objects that then fail the travel rule separately.
    """
    finder = MotionFinder()
    warm(finder)
    frame = grey_frame()
    draw_rect(frame, 100, 100, 40, 20)
    draw_rect(frame, 120, 110, 40, 20)  # overlaps the first
    boxes = finder.blobs(frame)
    assert len(boxes) == 1
    assert boxes[0].w >= 55  # spans both rectangles


def test_merge_overlapping_is_transitive():
    boxes = [Box(0, 0, 10, 10), Box(8, 0, 10, 10), Box(16, 0, 10, 10), Box(100, 100, 5, 5)]
    merged = merge_overlapping(boxes)
    assert len(merged) == 2
    big = max(merged, key=lambda b: b.area)
    assert (big.x, big.w) == (0, 26)


def test_a_moving_rectangle_produces_a_blob_near_it():
    finder = MotionFinder()
    warm(finder)
    boxes = []
    for step in range(4):
        frame = grey_frame()
        draw_rect(frame, 40 + step * 10, 120, 24, 24)
        boxes = finder.blobs(frame)
    assert len(boxes) == 1
    cx, cy = boxes[0].centre
    assert abs(cx - (40 + 3 * 10 + 12)) < 12
    assert abs(cy - 132) < 12


def test_an_empty_scene_produces_nothing():
    finder = MotionFinder()
    warm(finder, frames=12)
    assert finder.blobs(grey_frame()) == []


def test_colour_frames_are_accepted():
    """Real streams arrive as BGR; the finder must not care."""
    finder = MotionFinder()
    for _ in range(8):
        finder.blobs(np.full((FRAME_H, FRAME_W, 3), BACKGROUND_GREY, dtype=np.uint8))
    frame = np.full((FRAME_H, FRAME_W, 3), BACKGROUND_GREY, dtype=np.uint8)
    frame[100:140, 100:140] = MOVER_GREY
    assert len(finder.blobs(frame)) == 1


def test_min_area_rejects_specks():
    finder = MotionFinder(min_area=2000)
    warm(finder)
    frame = grey_frame()
    draw_rect(frame, 100, 100, 6, 6)
    assert finder.blobs(frame) == []
