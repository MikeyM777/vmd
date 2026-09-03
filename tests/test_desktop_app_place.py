"""Placing the window: the one-monitor, two-camera split the VMD button opens.

`place_half` is a plain function over a geometry and a stub window, so it needs
neither a display nor a QApplication - the same shape as everything else in
`vmd/desktop/app.py` that was written to be testable without a screen.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt

from vmd.desktop.app import parse_args, place_half, place_on_screen


class StubWindow:
    """Records what geometry and window state it was handed.

    setGeometry takes *args so it stands in for both callers: place_half passes
    four ints, place_on_screen passes a single QRect.
    """

    def __init__(self, state=Qt.WindowState.WindowMaximized) -> None:
        self._state = state
        self.geometry = None

    def windowState(self):  # noqa: N802 - Qt naming
        return self._state

    def setWindowState(self, state) -> None:  # noqa: N802 - Qt naming
        self._state = state

    def setGeometry(self, *args) -> None:  # noqa: N802 - Qt naming
        self.geometry = args if len(args) != 1 else args[0]


SCREEN = QRect(0, 0, 1920, 1080)


def test_the_left_half_starts_at_the_edge_and_takes_half_the_width() -> None:
    window = StubWindow()
    assert place_half(window, "left", [SCREEN])
    assert window.geometry == (0, 0, 960, 1080)


def test_the_right_half_starts_at_the_middle_and_takes_the_rest() -> None:
    window = StubWindow()
    assert place_half(window, "right", [SCREEN])
    assert window.geometry == (960, 0, 960, 1080)


def test_the_two_halves_meet_with_no_gap_on_an_odd_width() -> None:
    """An odd number of pixels must not leave a seam down the middle or steal a
    column from one side: left takes the floor, right takes the remainder."""
    odd = QRect(0, 0, 1921, 1080)
    left = StubWindow()
    right = StubWindow()
    place_half(left, "left", [odd])
    place_half(right, "right", [odd])
    lx, _, lw, _ = left.geometry
    rx, _, rw, _ = right.geometry
    assert lx + lw == rx, "the right half must start exactly where the left ends"
    assert lw + rw == 1921, "the two halves must cover the whole width"


def test_a_maximised_window_is_taken_out_of_that_state_first() -> None:
    """A window given a geometry while maximised keeps the whole screen and hides
    the other camera behind it, so the state is cleared before the geometry."""
    window = StubWindow(state=Qt.WindowState.WindowMaximized)
    place_half(window, "left", [SCREEN])
    assert not (window.windowState() & Qt.WindowState.WindowMaximized)


def test_a_screen_that_is_not_the_first_can_be_halved() -> None:
    second = QRect(1920, 0, 1280, 1024)
    window = StubWindow()
    assert place_half(window, "right", [SCREEN, second], number=2)
    assert window.geometry == (1920 + 640, 0, 640, 1024)


def test_no_side_does_nothing_so_the_remembered_window_wins() -> None:
    window = StubWindow()
    assert place_half(window, None, [SCREEN]) is False
    assert window.geometry is None


def test_no_screens_is_refused_rather_than_crashing() -> None:
    window = StubWindow()
    assert place_half(window, "left", []) is False
    assert window.geometry is None


def test_place_on_screen_clears_maximised_before_moving() -> None:
    """A window restored maximised must be taken out of that state, or the
    --screen / settings.screen placement is silently ignored."""
    window = StubWindow(state=Qt.WindowState.WindowMaximized)
    second = QRect(1920, 0, 1280, 1024)
    assert place_on_screen(window, 2, [SCREEN, second])
    assert not (window.windowState() & Qt.WindowState.WindowMaximized)
    assert window.geometry == second


def test_place_on_screen_out_of_range_is_refused() -> None:
    window = StubWindow()
    assert place_on_screen(window, 5, [SCREEN]) is False
    assert window.geometry is None


def test_place_is_parsed_from_the_command_line() -> None:
    assert parse_args(["--place", "left"]).place == "left"
    assert parse_args(["--place", "right"]).place == "right"
    assert parse_args([]).place is None
