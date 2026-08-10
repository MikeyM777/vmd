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
