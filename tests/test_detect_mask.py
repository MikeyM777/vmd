"""The drawn areas, as the detector has to understand them.

The rectangle could describe a road and a patch of sky and nothing else. The
thing the setting exists for - a treeline that sways all day - is a ragged band
across a hillside, and a box around it either throws away the sky above it or
leaves half the branches watched.

So these are the tests of the shape that replaces the box: that what was drawn
is painted into the mask the pipeline already reads, that the rectangles an
older settings file carries are still painted alongside it, and - the one that
matters - that movement inside a drawn outline is not reported while the same
movement a few pixels outside it still is.

No camera, no Qt. This is geometry, and it is tested as geometry.
"""

from __future__ import annotations

import math

import numpy as np

from vmd.detect.mask import (
    MAX_POINTS,
    contains,
    mask_from_areas,
    mask_from_regions,
    mask_from_shapes,
    simplify,
    sparse_outline,
)

# A square in the middle of a small frame, in the order a mouse would have gone
# round it.
SQUARE = [(10, 10), (30, 10), (30, 30), (10, 30)]

# Two trees with a gap between them, drawn as one outline: the notch is the
# reason a concave shape has to work at all.
NOTCHED = [(0, 0), (40, 0), (40, 40), (30, 40), (30, 10), (10, 10), (10, 40), (0, 40)]


def a_traced_treeline(width: int = 640, step: int = 2) -> list[tuple[int, int]]:
    """What a slow drag round a treeline actually hands the dialog.

    A mouse event every couple of pixels, and a ragged upper edge: three sine
    terms that never line up, which is a fair stand-in for branches and is the
    same every run. About six hundred points, which is the size of the problem -
    nobody wants that in a settings file they may have to read.
    """
    points: list[tuple[int, int]] = []
    for x in range(0, width, step):
        ragged = (
            18 * math.sin(x / 37.0)
            + 9 * math.sin(x / 11.0)
            + 4 * math.sin(x / 3.3)
        )
        points.append((x, int(200 + ragged)))
    # Back along the bottom of the band, which is a straight line and should
    # survive as two points.
    for x in range(width - 1, -1, -step):
        points.append((x, 260))
    return points


# --------------------------------------------------------------- the outline


def test_a_straight_run_of_points_becomes_its_two_ends() -> None:
    """The whole of the compression, in the smallest case there is."""
    straight = [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)]
    assert simplify(straight, 1.0) == [(0, 0), (40, 0)]


def test_the_corners_of_a_traced_box_all_survive() -> None:
    traced = []
    for x in range(0, 101, 2):
        traced.append((x, 0))
    for y in range(0, 101, 2):
        traced.append((100, y))
    kept = simplify(traced, 2.0)
    assert (0, 0) in kept
    assert (100, 0) in kept
    assert (100, 100) in kept
    assert len(kept) == 3


def test_a_traced_treeline_becomes_a_few_dozen_points() -> None:
    """The number that decides whether the settings file stays readable.

    A freehand drag produces a mouse event every few pixels. Six hundred points
    is not a setting, it is a bitmap written in JSON.
    """
    traced = a_traced_treeline()
    assert len(traced) > 500, "the fixture is meant to be the raw drag"
    kept = simplify(traced, 2.0)
    assert 10 < len(kept) < 100, len(kept)


def test_the_simplified_treeline_still_follows_the_treeline() -> None:
    """Compression that moves the line is not compression, it is a wrong answer.

    Every point that was thrown away has to be within the tolerance of the line
    that was kept - otherwise the ragged edge has been straightened and the
    branches above it are being watched again.
    """
    traced = a_traced_treeline()
    kept = simplify(traced, 2.0)
    for point in traced:
        assert _distance_to_path(point, kept) <= 2.0 + 1e-6


def test_an_outline_is_coarsened_until_it_fits_the_limit() -> None:
    """A very ragged trace must not be allowed to fill the settings file.

    Simplifying at the tolerance the operator can see is the first answer; when
    even that leaves hundreds of points, the tolerance grows until it does not.
    """
    traced = a_traced_treeline()
    kept = sparse_outline(traced, 0.05, limit=40)
    assert 3 <= len(kept) <= 40, len(kept)


def test_an_outline_coarsened_to_fit_is_not_coarsened_far_past_it() -> None:
    """The limit is a ceiling, not a target to overshoot.

    Coarsening by doubling took a hillside that came out at fifty-one points
    down to twenty-four - a tolerance eight times the one he could see, where
    four would have fitted. What he is left with is the outline he has to live
    with, so it goes coarse in small steps.
    """
    kept = sparse_outline(a_traced_treeline(), 2.0, limit=MAX_POINTS)
    assert MAX_POINTS // 2 < len(kept) <= MAX_POINTS, len(kept)


def test_a_stray_click_is_not_an_area() -> None:
    """Fewer than three points cannot enclose anything, so nothing is returned."""
    assert sparse_outline([(5, 5), (5, 6)], 2.0) == []
    assert sparse_outline([(5, 5), (5, 6), (5, 7)], 2.0) == []


def test_the_default_limit_is_a_number_a_person_could_read() -> None:
    assert 10 <= MAX_POINTS <= 100


def _distance_to_path(point, path) -> float:
    """How far a thrown-away point sits from the line that was kept."""
    return min(
        _distance_to_segment(point, path[i], path[i + 1]) for i in range(len(path) - 1)
    )


def _distance_to_segment(point, start, end) -> float:
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ------------------------------------------------------ inside and outside it


def test_a_point_inside_a_drawn_square_is_inside_it() -> None:
    assert contains(SQUARE, 20, 20) is True
    assert contains(SQUARE, 5, 20) is False
    assert contains(SQUARE, 20, 50) is False


def test_the_gap_between_two_trees_is_not_inside_the_outline() -> None:
    """The reason a box was never enough, stated as a test."""
    assert contains(NOTCHED, 5, 20) is True    # the left tree
    assert contains(NOTCHED, 35, 20) is True   # the right tree
    assert contains(NOTCHED, 20, 30) is False  # the sky between them


def test_a_shape_with_no_points_contains_nothing() -> None:
    assert contains([], 0, 0) is False
    assert contains([(1, 1), (2, 2)], 1, 1) is False


# ------------------------------------------------------------ painting a mask


def test_a_drawn_square_is_painted_into_the_mask() -> None:
    mask = mask_from_shapes([SQUARE], 60, 60)
    assert mask is not None
    assert mask.shape == (60, 60)
    assert mask[20, 20] != 0
    assert mask[50, 50] == 0


def test_the_outline_itself_is_inside_the_area_it_encloses() -> None:
    """The line the operator drew is part of what he drew.

    He traces the edge of a tree, not a millimetre inside it, and a mask that
    stops one pixel short of its own outline leaves the branch he was pointing
    at being watched.
    """
    mask = mask_from_shapes([SQUARE], 60, 60)
    for x, y in SQUARE:
        assert mask[y, x] != 0, (x, y)


def test_the_notch_between_two_trees_stays_watched() -> None:
    mask = mask_from_shapes([NOTCHED], 60, 60)
    assert mask[20, 5] != 0
    assert mask[20, 35] != 0
    assert mask[30, 20] == 0, "the sky between two masked trees was masked too"


def test_what_the_mask_covers_is_what_a_click_would_call_inside() -> None:
    """One geometry, not two.

    The dialog decides which shape was clicked and the detector decides which
    blobs are ignored. If those two answers can differ, an operator deletes a
    shape that is not the one that is silencing his perimeter, and nothing
    anywhere says so.
    """
    mask = mask_from_shapes([NOTCHED], 60, 60)
    for y in range(60):
        for x in range(60):
            if contains(NOTCHED, x, y):
                assert mask[y, x] != 0, (x, y)


def test_a_shape_drawn_off_the_edge_is_clipped_and_not_wrapped() -> None:
    """The stream can change resolution without anyone asking it to."""
    hanging = [(-20, -20), (20, -20), (20, 20), (-20, 20)]
    mask = mask_from_shapes([hanging], 40, 40)
    assert mask is not None
    assert mask[0, 0] != 0
    assert mask[19, 19] != 0
    assert mask[39, 39] == 0


def test_a_shape_entirely_off_the_picture_paints_nothing() -> None:
    assert mask_from_shapes([[(80, 80), (100, 80), (100, 100)]], 40, 40) is None


def test_no_shapes_means_no_mask() -> None:
    """None, not an all-zero array: None is what the pipeline reads as no mask."""
    assert mask_from_shapes([], 40, 40) is None


# --------------------------------------------------- both kinds, at once


def test_a_rectangle_and_a_drawn_shape_are_both_honoured() -> None:
    """A settings file written before this keeps working.

    Everything the operator has already marked out is a rectangle, and the day
    he draws his first outline is not the day those stop being ignored.
    """
    mask = mask_from_areas([(0, 0, 5, 5)], [SQUARE], 60, 60)
    assert mask is not None
    assert mask[2, 2] != 0, "the rectangle from the old settings file"
    assert mask[20, 20] != 0, "the outline he drew today"
    assert mask[50, 50] == 0


def test_either_kind_alone_still_paints() -> None:
    assert mask_from_areas([(0, 0, 5, 5)], [], 60, 60) is not None
    assert mask_from_areas([], [SQUARE], 60, 60) is not None
    assert mask_from_areas([], [], 60, 60) is None


def test_the_rectangles_are_painted_exactly_as_they_always_were() -> None:
    """`mask_from_regions` moved house; it did not change."""
    old = mask_from_regions([(1, 1, 3, 3)], 8, 8)
    both = mask_from_areas([(1, 1, 3, 3)], [], 8, 8)
    assert np.array_equal(old, both)


# ------------------------------------------ what it is for: the tree goes quiet


def test_movement_inside_a_drawn_area_is_not_reported_and_outside_it_is():
    """The whole feature, in one test.

    A mask that is not honoured is decoration. The same walk is fed twice - once
    through the middle of the drawn outline, once well outside it - and only one
    of them is news.
    """
    from vmd.detect.pipeline import DetectionConfig, DetectionPipeline

    from tests.test_detect_motion import FRAME_H, FRAME_W, draw_rect, grey_frame
    from tests.test_detect_pipeline import WARMUP_FRAMES, feed_empty

    # A ragged band across the middle of the frame, of the shape a treeline is.
    band = (
        [(x, 90 + (12 if (x // 40) % 2 else 0)) for x in range(0, FRAME_W, 40)]
        + [(FRAME_W - 1, 150), (0, 150)]
    )

    def walk(at_y: int) -> list:
        config = DetectionConfig()
        config.ignore_mask = mask_from_shapes([band], FRAME_W, FRAME_H)
        pipeline = DetectionPipeline(config)
        index = feed_empty(pipeline, WARMUP_FRAMES)
        found = []
        for step in range(15):
            frame = grey_frame()
            draw_rect(frame, 30 + step * 10, at_y, 24, 24)
            found.extend(pipeline.feed(frame, index))
            index += 1
        return found

    # Centred at y = 120 + 12, which is inside the band everywhere it walks.
    assert walk(120) == [], "a walk through the drawn area was reported anyway"
    # Centred at y = 200 + 12, well below the band.
    assert walk(200), "a walk outside the drawn area was silenced by it"


# ------------------------------------- an outline drawn against another picture
#
# The exposure the drawn areas arrived with, and the one clipping cannot save
# them from. The stream's size is an ONVIF setting on the camera and this console
# has a button that changes it.


def test_an_outline_is_put_back_where_he_drew_it_on_a_smaller_picture() -> None:
    """Traced on a 1920x1080 still, applied to a 1280x720 stream. Every point is
    comfortably inside the frame, so nothing is clipped and nothing complains -
    the band simply covers a different third of the picture, the treeline is
    watched again, and the first anybody knows is a night of alarms."""
    from vmd.detect.mask import mask_from_areas

    # A band across the middle of the big picture.
    band = [(0, 540), (1920, 540), (1920, 700), (0, 700)]
    mask = mask_from_areas((), [band], 1280, 720, drawn_at=[(1920, 1080)])
    assert mask is not None
    # 540/1080 of the way down the drawn picture is 360/720 of the way down this
    # one. Unscaled it would have landed at 540, which is most of the way to the
    # bottom of a 720-high frame.
    assert mask[365, 640] == 255, "the band is not where it was drawn"
    assert mask[540, 640] == 0, "the band is still where the numbers put it"


def test_an_outline_with_no_recorded_size_is_left_exactly_as_it_is() -> None:
    """What every shape written before the size was recorded says. Stretching
    those by a guess would move areas that are currently right."""
    from vmd.detect.mask import mask_from_areas

    band = [(0, 300), (640, 300), (640, 400), (0, 400)]
    mask = mask_from_areas((), [band], 1280, 720, drawn_at=[(0, 0)])
    assert mask is not None and mask[350, 320] == 255

    same = mask_from_areas((), [band], 1280, 720)
    assert same is not None and same[350, 320] == 255


def test_an_outline_drawn_at_the_size_it_is_shown_at_is_not_touched() -> None:
    from vmd.detect.mask import rescale

    points = [(10, 20), (30, 40), (50, 60)]
    assert rescale(points, (1280, 720), 1280, 720) == points
