"""How fast the operator asked the camera to move. One table, three readers.

`vmd/desktop/steering.py` scales the pan and the tilt with it, `vmd/ptz/lenses.py`
scales the zoom creep with it, and `vmd/desktop/live.py` scales the head zoom.

It lives here, in the camera package, rather than beside the steering maths it
was first written for, because the camera package may not import the desktop
one - `vmd/desktop/` already imports `vmd/ptz/`, and a module in `vmd/ptz/`
reaching back into `vmd/desktop/` would make the two mutually dependent and
point the camera layer at the GUI.
"""

from __future__ import annotations

# Multipliers on the speeds this console already used, rather than three tables
# of absolute speeds. "normal" is 1.0, so the arrow keys and the zoom are
# arithmetically identical to what every install did before this choice existed.
#
# The mouse edge band is the one exception, and it is a deliberate change rather
# than an oversight: it used to run unscaled to a full 1.0 while the arrow keys
# capped at 0.5, so the same camera steered at two different rates depending on
# which hand was on it. It is now multiplied by NORMAL_SPEED like everything
# else, which means a console that upgrades and never opens the dropdown will
# find the mouse edge steering at half the speed it used to. That is the fix;
# see `edge_velocity`.
#
# 2.0 is the ceiling, not a step on a ladder. Every base here is 0.5 -
# NORMAL_SPEED for the keys and the edge, ZOOM_SPEED for the head zoom - so
# "fast" lands on exactly 1.0, the top of the range ONVIF accepts. A larger
# factor would not go faster; it would only be clamped, and would teach the
# operator that the top of the list does nothing.
FACTOR = {"slow": 0.5, "normal": 1.0, "fast": 2.0}


def factor(speed: str) -> float:
    """The multiplier for a chosen speed. Anything unrecognised is normal.

    Never raises, and that is the whole reason it is a function rather than a
    dictionary lookup at each call site. The value arrives from a settings file
    a person can edit by hand, and every caller is inside either a Qt key
    handler or the camera command thread - the two places in this program where
    an exception is worst. An unknown word steers at the speed the console
    always used, which is wrong in the mildest possible direction.
    """
    return FACTOR.get(speed, 1.0)


def onvif_range(value: float) -> float:
    """Clamp to the range ONVIF accepts.

    Nothing reaches it today, and that is worth stating rather than implying
    otherwise: the largest any caller can ask for is a base of 0.5 times
    FACTOR["fast"] of 2.0, which is exactly 1.0 - the top of the range, not past
    it. So this is a guard on the arithmetic above it, not a correction anything
    currently needs.

    It is kept because the thing it guards against is silent. A camera refuses a
    velocity outside -1..1 outright rather than capping it, so the head does not
    move at all - and the gesture that would produce an out-of-range value is
    the one the operator makes when he wants it to move most. A future factor,
    a raised NORMAL_SPEED or a fourth entry in the table would each reach that
    on their own, and none of them would look wrong in review.
    """
    return max(-1.0, min(1.0, value))
