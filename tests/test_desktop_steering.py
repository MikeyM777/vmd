"""Turning keys and pointer positions into camera speeds."""

from __future__ import annotations

import pytest

from vmd.desktop.steering import EDGE_FRACTION, edge_velocity, key_velocity


def test_no_keys_is_no_movement() -> None:
    assert key_velocity(set(), fine=False) == (0.0, 0.0)


def test_one_key_moves_one_axis() -> None:
    assert key_velocity({"right"}, fine=False) == (0.5, 0.0)
    assert key_velocity({"up"}, fine=False) == (0.0, 0.5)


def test_two_keys_move_both_axes_at_once() -> None:
    """Holding up and right must go diagonally, not pick a winner."""
    assert key_velocity({"up", "right"}, fine=False) == (0.5, 0.5)


def test_opposing_keys_cancel() -> None:
    assert key_velocity({"up", "down"}, fine=False) == (0.0, 0.0)
    assert key_velocity({"left", "right"}, fine=False) == (0.0, 0.0)


def test_fine_movement_is_slower_on_every_axis() -> None:
    fast = key_velocity({"up", "right"}, fine=False)
    slow = key_velocity({"up", "right"}, fine=True)
    assert 0 < slow[0] < fast[0]
    assert 0 < slow[1] < fast[1]


def test_the_middle_of_the_picture_does_not_steer() -> None:
    assert edge_velocity(0.5, 0.5) == (0.0, 0.0)


def test_the_edge_steers_and_the_speed_grows_with_depth() -> None:
    shallow = edge_velocity(1.0 - EDGE_FRACTION + 0.001, 0.5)
    deep = edge_velocity(0.999, 0.5)
    assert shallow[0] > 0 and deep[0] > shallow[0]
    assert deep[0] <= 1.0


def test_up_is_positive_tilt_wherever_it_comes_from() -> None:
    """Screen coordinates grow downwards; the camera does not."""
    pan, tilt = edge_velocity(0.5, 0.001)
    assert tilt > 0
    pan, tilt = edge_velocity(0.5, 0.999)
    assert tilt < 0


def test_a_corner_steers_both_axes() -> None:
    pan, tilt = edge_velocity(0.999, 0.001)
    assert pan > 0 and tilt > 0


@pytest.mark.parametrize("x,y", [(-0.5, 0.5), (1.5, 0.5), (0.5, -2.0), (0.5, 9.9)])
def test_a_pointer_outside_the_picture_is_clamped_not_amplified(x: float, y: float) -> None:
    pan, tilt = edge_velocity(x, y)
    assert -1.0 <= pan <= 1.0
    assert -1.0 <= tilt <= 1.0


def test_every_velocity_is_inside_onvif_range() -> None:
    """Sweep the whole picture; nothing should ever leave -1..1."""
    x = 0.0
    while x <= 1.0:
        y = 0.0
        while y <= 1.0:
            pan, tilt = edge_velocity(x, y)
            assert -1.0 <= pan <= 1.0
            assert -1.0 <= tilt <= 1.0
            y += 0.01
        x += 0.01


def test_the_band_is_symmetric() -> None:
    left = edge_velocity(EDGE_FRACTION / 2, 0.5)
    right = edge_velocity(1 - EDGE_FRACTION / 2, 0.5)
    assert left[0] == -right[0]
    assert left[0] != 0.0


def test_speed_is_monotonic_with_depth() -> None:
    """Walking further into the edge band should never reduce pan speed."""
    previous = 0.0
    x = 1.0 - EDGE_FRACTION
    while x <= 1.0:
        pan, _tilt = edge_velocity(x, 0.5)
        assert pan >= previous
        previous = pan
        x += 0.005


def test_fine_mode_is_a_scale_not_a_different_shape() -> None:
    fast = key_velocity({"up", "right"}, fine=False)
    slow = key_velocity({"up", "right"}, fine=True)
    ratio = slow[0] / fast[0]
    assert slow[1] / fast[1] == pytest.approx(ratio)


def test_an_unknown_key_is_ignored() -> None:
    assert key_velocity({"space"}, fine=False) == (0.0, 0.0)


# --------------------------------------------------------------------------- #
#  The speed the operator chose
# --------------------------------------------------------------------------- #


def test_normal_is_exactly_what_this_console_always_did() -> None:
    """The default must not move a single existing camera differently, so it is
    asserted against the literal numbers rather than against itself."""
    assert key_velocity({"right"}, fine=False, speed="normal") == (0.5, 0.0)
    assert key_velocity({"right"}, fine=True, speed="normal") == (0.08, 0.0)


def test_leaving_the_speed_out_is_the_same_as_asking_for_normal() -> None:
    """Every caller that has not been taught about the setting keeps working."""
    assert key_velocity({"up", "right"}, fine=False) == key_velocity(
        {"up", "right"}, fine=False, speed="normal"
    )
    assert edge_velocity(0.0, 0.5) == edge_velocity(0.0, 0.5, speed="normal")


def test_slow_halves_the_keys_and_fast_doubles_them() -> None:
    assert key_velocity({"right"}, fine=False, speed="slow") == (0.25, 0.0)
    assert key_velocity({"right"}, fine=False, speed="fast") == (1.0, 0.0)


def test_the_edge_obeys_the_speed_as_well_as_the_keys() -> None:
    """Steering with the mouse used to run at full ONVIF speed while the arrow
    keys capped at half, so one camera moved at two rates depending on which
    hand was on it. One number moves both now."""
    deep = 0.0  # hard against the left edge, where the band is at its fastest
    slow = abs(edge_velocity(deep, 0.5, speed="slow")[0])
    normal = abs(edge_velocity(deep, 0.5, speed="normal")[0])
    fast = abs(edge_velocity(deep, 0.5, speed="fast")[0])
    assert 0 < slow < normal < fast


def test_the_deepest_edge_matches_a_held_arrow_key() -> None:
    """The two ways of steering agree at last: the fastest the edge band can
    ask for is the speed a held arrow key asks for."""
    for speed in ("slow", "normal", "fast"):
        edge = abs(edge_velocity(0.0, 0.5, speed=speed)[0])
        key = abs(key_velocity({"left"}, fine=False, speed=speed)[0])
        assert edge == pytest.approx(key, abs=0.002)


@pytest.mark.parametrize("speed", ["slow", "normal", "fast"])
def test_no_speed_can_ask_the_camera_for_more_than_onvif_takes(speed: str) -> None:
    """`fast` doubles, and the edge band already reaches full depth, so an
    unclamped corner would ask for 2.0 - which the camera refuses outright
    rather than capping, leaving the head still in the very gesture meant to
    move it most."""
    for pan, tilt in (
        key_velocity({"left", "up"}, fine=False, speed=speed),
        key_velocity({"right", "down"}, fine=False, speed=speed),
        edge_velocity(0.0, 0.0, speed=speed),
        edge_velocity(1.0, 1.0, speed=speed),
    ):
        assert -1.0 <= pan <= 1.0
        assert -1.0 <= tilt <= 1.0


def test_a_speed_nobody_recognises_steers_at_the_normal_speed() -> None:
    """settings.json can be edited by hand, and this runs inside a Qt key
    handler - the one place an exception must never reach."""
    assert key_velocity({"right"}, fine=False, speed="quick") == (0.5, 0.0)
    assert edge_velocity(0.0, 0.5, speed="") == edge_velocity(0.0, 0.5, speed="normal")


@pytest.mark.parametrize("speed", ["slow", "normal", "fast"])
def test_the_middle_of_the_picture_never_steers_whatever_the_speed(speed: str) -> None:
    """A multiplier must not turn 'not steering' into 'steering slowly'."""
    assert edge_velocity(0.5, 0.5, speed=speed) == (0.0, 0.0)


@pytest.mark.parametrize("speed", ["slow", "normal", "fast"])
def test_no_keys_is_still_no_movement_at_every_speed(speed: str) -> None:
    """The stop is arithmetic - `_drive` sends a stop only when every component
    is exactly zero - so a scale that could not produce zero would be a head
    that never stops."""
    assert key_velocity(set(), fine=False, speed=speed) == (0.0, 0.0)
