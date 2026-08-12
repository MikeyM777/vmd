"""The ignored areas, as geometry: what was drawn, and what it covers.

The rectangle could describe a road and a patch of sky, and nothing else. The
thing the setting exists for is a treeline that sways all day, and a treeline is
a ragged band across a hillside: a box around it either throws away the sky
above it or leaves half the branches watched. The operator reached that from the
other end - "yes, free hand" - and `IgnoreShape` is the model that came of it.

Four things live here, and they live together on purpose.

* `simplify` and `sparse_outline` throw away the points nobody could see. A
  freehand drag reports the mouse every few pixels, so tracing one treeline is
  six hundred points, and six hundred points is not a setting - it is a bitmap
  written into a file the operator may one day have to read.
* `contains` answers "is this dot inside that outline". The dialog asks it to
  decide which shape was clicked off; the mask below is painted to agree with
  it exactly. Two answers to that question that can differ is an operator
  deleting a shape that is not the one silencing his perimeter, and nothing
  anywhere saying so.
* `mask_from_shapes` paints the outlines into the array the pipeline already
  reads, and `mask_from_areas` paints the outlines and the old rectangles into
  one. Both, always: a settings file written before any of this exists carries
  rectangles, and the day he draws his first outline is not the day those stop
  being ignored.

No OpenCV, and no drawing library. `vmd/detect/filters.py` states the rule this
package keeps - the arithmetic is arithmetic and is tested as arithmetic - and
the same functions are wanted by the console, which has to open on a laptop
where the vision stack is missing or will not load. numpy is imported inside the
two functions that paint an array for exactly that reason: `contains` and
`simplify` are pure Python, and asking for them costs nothing.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[int, int]

# The most points a drawn area is stored with.
#
# Not a limit on how carefully he can trace: `simplify` has already thrown away
# everything within a couple of dots of the line it kept, and past that the
# outline stops being something a person could look at in a settings file
# without deciding the console has written a bitmap into it. Fifty is comfortably
# more than a ragged hillside needs and comfortably less than a page.
MAX_POINTS = 50

# How much coarser to go each time an outline still does not fit in that.
#
# Gently. It was double, and double overshoots: a hillside that came out at
# fifty-one points went straight to twenty-four, thrown away by a tolerance
# eight times the one he could see where four would have done. Every step costs
# one pass over points that are already few, and the outline that survives is
# the one he has to live with.
COARSER = 1.4


# ------------------------------------------------------------- fewer points


def simplify(points: Sequence[Point], tolerance: float) -> list[Point]:
    """The same line with the points nobody could see taken out of it.

    Ramer-Douglas-Peucker: keep the two ends, keep whichever point between them
    sits furthest from the straight line joining them, and stop when the
    furthest one is closer than the tolerance. What survives is the corners -
    which on a treeline is the treeline, and on a straight run along a road is
    the two ends of it.

    An open line, because that is what a drag is: he presses at one end of the
    treeline, tracks along it and releases at the other, and the outline is
    closed afterwards by joining the last point back to the first. Run round the
    loop instead and the join itself becomes a corner the algorithm has to keep.

    Iterative rather than recursive. Six hundred points traced in one stroke is
    a normal drag and a stack this deep is not.
    """
    if len(points) < 3:
        return [(int(x), int(y)) for x, y in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    pending = [(0, len(points) - 1)]
    while pending:
        first, last = pending.pop()
        if last <= first + 1:
            continue
        furthest, distance = first, -1.0
        for index in range(first + 1, last):
            gap = _distance_to_line(points[index], points[first], points[last])
            if gap > distance:
                furthest, distance = index, gap
        if distance <= tolerance:
            continue
        keep[furthest] = True
        pending.append((first, furthest))
        pending.append((furthest, last))
    return [
        (int(point[0]), int(point[1]))
        for point, kept in zip(points, keep)
        if kept
    ]


def sparse_outline(
    points: Sequence[Point], tolerance: float, limit: int = MAX_POINTS
) -> list[Point]:
    """A traced line, as the few points that are worth storing - or nothing.

    The tolerance is what the operator could see: below it, moving the line
    changes nothing he drew. When even that leaves more points than a settings
    file should carry - a hand that shook, a very ragged skyline - the tolerance
    grows until it fits, because a coarser outline he can still read beats an
    exact one that has turned his settings into a data file. It grows slowly, so
    what he is left with is the coarsest outline that fits rather than one far
    coarser than it needed to be.

    Returns nothing at all for anything that cannot enclose an area. Three
    points is the fewest that can, which is what `IgnoreShape` says too, and a
    shape with no area would sit in the picture looking like it was doing
    something.
    """
    if len(points) < 3:
        return []
    kept = simplify(points, tolerance)
    while len(kept) > limit and tolerance < 1e6:
        tolerance *= COARSER
        kept = simplify(points, tolerance)
    kept = _without_repeats(kept)
    if len(kept) < 3 or _area(kept) <= 0.0:
        return []
    return kept


def _distance_to_line(point: Point, start: Point, end: Point) -> float:
    """How far a point sits from the straight line between two others."""
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    # Twice the area of the triangle, over the length of its base.
    return abs(dx * (ay - py) - dy * (ax - px)) / ((dx * dx + dy * dy) ** 0.5)


def _without_repeats(points: Sequence[Point]) -> list[Point]:
    """The same outline with any dot it visited twice in a row said once.

    A slow hand reports the same pixel several times, and the closing join can
    land back on the point it started from.
    """
    kept: list[Point] = []
    for point in points:
        if not kept or kept[-1] != point:
            kept.append(point)
    if len(kept) > 1 and kept[0] == kept[-1]:
        kept.pop()
    return kept


def _area(points: Sequence[Point]) -> float:
    """How much the outline encloses, in dots. Never negative."""
    total = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


# ------------------------------------------------------- inside and outside


def contains(points: Sequence[Point], x: float, y: float) -> bool:
    """Whether a dot of the picture is inside a drawn outline.

    Even-odd: count the edges the outline crosses on a ray going right from the
    dot, and an odd number means it is inside. It is the rule that gets the
    notch between two trees right - the sky in the gap is outside an outline
    drawn round both of them, which is the entire reason a rectangle was not
    enough.

    The mask below is painted to agree with this exactly, so the shape he clicks
    off is the shape that was silencing that part of the picture.
    """
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        (x1, y1), (x2, y2) = current, previous
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
        previous = current
    return inside


# ------------------------------------------------------------ painting a mask


def mask_from_regions(regions: Iterable[Sequence[int]], width: int, height: int):
    """Paint the ignore rectangles into a mask the size of the frame.

    Regions are clipped rather than trusted. The operator painted them against
    whatever resolution the console was showing, and the stream can change
    resolution without asking - so a rectangle hanging off the edge must cover
    the part of the frame it still overlaps, not raise, and not wrap around.

    Returns None when nothing survives clipping, because None is what the
    pipeline reads as "no mask" and an all-zero array would cost a comparison
    per blob for no reason.
    """
    return mask_from_areas(regions, (), width, height)


def mask_from_shapes(shapes: Iterable[Sequence[Point]], width: int, height: int):
    """Paint the drawn outlines into a mask the size of the frame."""
    return mask_from_areas((), shapes, width, height)


def rescale(points: Sequence[Point], drawn: Sequence[int], width: int, height: int):
    """The same outline, on a picture of a different size.

    Drawn against 1920x1080 and applied to 1280x720, an unscaled point lands a
    third too far right and a third too low - so a band traced over a treeline
    covers sky instead, the treeline is watched again, and the only sign is a
    night of alarms nobody can explain. Clipping does not save it: the points
    are all inside the frame, just in the wrong part of it.

    An unknown size is left alone. That is what every shape written before this
    was recorded says, and stretching them by a guess would move areas that are
    currently right.
    """
    drawn_w, drawn_h = (int(v) for v in (drawn or (0, 0)))
    if drawn_w <= 0 or drawn_h <= 0:
        return list(points)
    if (drawn_w, drawn_h) == (int(width), int(height)):
        return list(points)
    across, down = width / drawn_w, height / drawn_h
    return [(int(round(x * across)), int(round(y * down))) for x, y in points]


def mask_from_areas(
    regions: Iterable[Sequence[int]],
    shapes: Iterable[Sequence[Point]],
    width: int,
    height: int,
    drawn_at: Iterable[Sequence[int]] | None = None,
):
    """One mask, from the boxes and the outlines together.

    Together and not either-or. Every area the operator has marked out until now
    is a rectangle; the day he traces his first treeline is not the day those
    rectangles stop being ignored, and a settings file written before any of
    this has to go on meaning what it meant.

    Both kinds are clipped to the frame for the same reason - the stream can
    change resolution underneath a setting drawn against another one - and None
    comes back when nothing at all survives, because None is what the pipeline
    reads as "no mask".

    Clipping is not enough on its own, though, and that is what `drawn_at` is
    for: an outline traced on a 1920x1080 still is entirely inside a 1280x720
    frame, just a third too far right and a third too low, so nothing is clipped
    and the mask silently covers the wrong part of the picture. Given the size
    each outline was drawn against, it is put back where he drew it. The
    rectangles have no such record and are clipped as before.
    """
    import numpy as np

    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        return None
    mask = np.zeros((height, width), dtype=np.uint8)
    painted = False
    for region in regions:
        x, y, w, h = (int(v) for v in region)
        left, top = max(x, 0), max(y, 0)
        right, bottom = min(x + w, width), min(y + h, height)
        if right <= left or bottom <= top:
            continue
        mask[top:bottom, left:right] = 255
        painted = True
    # Each outline beside the size of the picture it was drawn on, so it can be
    # put back where he drew it rather than where the numbers happen to land.
    sizes = list(drawn_at or ())
    for index, shape in enumerate(shapes):
        points = [(int(x), int(y)) for x, y in shape]
        if len(points) < 3:
            continue
        points = rescale(points, sizes[index] if index < len(sizes) else (0, 0), width, height)
        painted = _paint_shape(mask, points, width, height) or painted
    return mask if painted else None


def _paint_shape(mask, points: Sequence[Point], width: int, height: int) -> bool:
    """Fill one outline into the mask. True when any of it landed on the frame.

    Two passes, and the second one is not decoration. The fill covers every dot
    the even-odd rule calls inside, which is what `contains` answers and what
    makes the shape he clicks the shape that was silencing the picture. Then the
    outline itself is drawn, because the line he traced is part of what he
    traced: he follows the edge of the tree, not a dot inside it, and a mask
    that stops short of its own edge leaves the branch he was pointing at being
    watched. It is also what keeps an outline too thin to enclose a single dot -
    a road at 700 m, a wire - from silencing nothing at all.
    """
    painted = False
    top = max(0, min(y for _x, y in points))
    bottom = min(height - 1, max(y for _x, y in points))
    for row in range(top, bottom + 1):
        crossings: list[float] = []
        previous = points[-1]
        for current in points:
            (x1, y1), (x2, y2) = current, previous
            if (y1 > row) != (y2 > row):
                crossings.append(x1 + (row - y1) * (x2 - x1) / (y2 - y1))
            previous = current
        crossings.sort()
        for start, end in zip(crossings[0::2], crossings[1::2]):
            # A dot is inside when an odd number of edges is strictly to its
            # right, which is the pair half-open on the right - the same
            # arithmetic as `contains`, and deliberately so.
            left = max(0, math.ceil(start))
            right = min(width, math.ceil(end))
            if right > left:
                mask[row, left:right] = 255
                painted = True
    previous = points[-1]
    for current in points:
        painted = _paint_line(mask, previous, current, width, height) or painted
        previous = current
    return painted


def _paint_line(mask, start: Point, end: Point, width: int, height: int) -> bool:
    """Draw one edge of an outline into the mask.

    Cut down to the part of it that is on the frame before anything is drawn,
    rather than drawn dot by dot and each dot tested. A settings file is a text
    file on a machine nobody administers, and one edited by hand into a corner
    at ten million would otherwise be ten million steps of a loop - a detector
    that never starts, which is a perimeter nobody is watching.
    """
    (x1, y1), (x2, y2) = start, end
    dx, dy = x2 - x1, y2 - y1
    first, last = 0.0, 1.0
    for p, q in (
        (-dx, x1),
        (dx, (width - 1) - x1),
        (-dy, y1),
        (dy, (height - 1) - y1),
    ):
        if p == 0:
            if q < 0:
                return False  # parallel to this edge and outside it
            continue
        cut = q / p
        if p < 0:
            if cut > last:
                return False
            first = max(first, cut)
        else:
            if cut < first:
                return False
            last = min(last, cut)
    steps = int(math.ceil(max(abs(dx), abs(dy)) * (last - first)))
    painted = False
    for step in range(steps + 1):
        along = first + (last - first) * (step / steps if steps else 0.0)
        x = int(round(x1 + dx * along))
        y = int(round(y1 + dy * along))
        if 0 <= x < width and 0 <= y < height:
            mask[y, x] = 255
            painted = True
    return painted
