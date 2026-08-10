"""Keys and pointer positions to camera speeds. No Qt, no camera - just maths.

Speeds are -1..1 because that is what ONVIF takes. Pan and tilt are computed on
separate axes so that holding two keys goes diagonally instead of the last key
winning, and so that opposing keys cancel the way the head physically would.
"""

from __future__ import annotations

# The outer band of the picture that steers. Inside it, speed grows with depth,
# so a nudge and a fast slew are the same gesture rather than two modes.
EDGE_FRACTION = 0.14

NORMAL_SPEED = 0.5
FINE_SPEED = 0.08


def key_velocity(held: set[str], fine: bool) -> tuple[float, float]:
    """(pan, tilt) for the arrow keys currently held.

    `held` contains any of "left", "right", "up", "down".
    """
    speed = FINE_SPEED if fine else NORMAL_SPEED
    pan = (-1 if "left" in held else 0) + (1 if "right" in held else 0)
    tilt = (-1 if "down" in held else 0) + (1 if "up" in held else 0)
    return (pan * speed, tilt * speed)


def edge_velocity(x: float, y: float) -> tuple[float, float]:
    """(pan, tilt) for a pointer at fractional position (x, y) in the picture.

    (0, 0) is the top-left corner. Tilt is inverted because screen coordinates
    grow downwards and cameras do not.
    """
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)

    def component(position: float) -> float:
        if position < EDGE_FRACTION:
            return -(EDGE_FRACTION - position) / EDGE_FRACTION
        if position > 1.0 - EDGE_FRACTION:
            return (position - (1.0 - EDGE_FRACTION)) / EDGE_FRACTION
        return 0.0

    pan = component(x)
    tilt = -component(y)
    return (round(pan, 3), round(tilt, 3))
