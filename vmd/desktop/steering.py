"""Keys and pointer positions to camera speeds. No Qt, no camera - just maths.

Speeds are -1..1 because that is what ONVIF takes. Pan and tilt are computed on
separate axes so that holding two keys goes diagonally instead of the last key
winning, and so that opposing keys cancel the way the head physically would.
"""

from __future__ import annotations

from vmd.ptz.speed import factor, onvif_range

# The outer band of the picture that steers. Inside it, speed grows with depth,
# so a nudge and a fast slew are the same gesture rather than two modes.
EDGE_FRACTION = 0.14

NORMAL_SPEED = 0.5
FINE_SPEED = 0.08


def key_velocity(held: set[str], fine: bool, speed: str = "normal") -> tuple[float, float]:
    """(pan, tilt) for the arrow keys currently held.

    `held` contains any of "left", "right", "up", "down".

    `speed` is the operator's choice from the Settings tab; "normal" is the
    speed this console always used, so the default here changes nothing for a
    caller that has not been taught about it.
    """
    base = (FINE_SPEED if fine else NORMAL_SPEED) * factor(speed)
    pan = (-1 if "left" in held else 0) + (1 if "right" in held else 0)
    tilt = (-1 if "down" in held else 0) + (1 if "up" in held else 0)
    return (onvif_range(pan * base), onvif_range(tilt * base))


def edge_velocity(x: float, y: float, speed: str = "normal") -> tuple[float, float]:
    """(pan, tilt) for a pointer at fractional position (x, y) in the picture.

    (0, 0) is the top-left corner. Tilt is inverted because screen coordinates
    grow downwards and cameras do not.

    Scaled by the chosen speed, which it was not before, and that is a change
    to how this console has always steered rather than only a new setting. The
    band ran from 0 to a full 1.0 at the deepest point while the arrow keys
    capped at NORMAL_SPEED - so the same camera moved at two different rates
    depending on whether the operator's hand was on the mouse or the keyboard,
    and the mouse was the faster of the two by double. Now one number moves
    both, which is what makes the dropdown mean anything.
    """
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    base = NORMAL_SPEED * factor(speed)

    def component(position: float) -> float:
        if position < EDGE_FRACTION:
            return -(EDGE_FRACTION - position) / EDGE_FRACTION
        if position > 1.0 - EDGE_FRACTION:
            return (position - (1.0 - EDGE_FRACTION)) / EDGE_FRACTION
        return 0.0

    pan = component(x) * base
    tilt = -component(y) * base
    return (round(onvif_range(pan), 3), round(onvif_range(tilt), 3))
