"""The palette: converted once from DESIGN.md, and never guessed at again."""

from __future__ import annotations

import re

from vmd.desktop.style import PALETTE, stylesheet


def test_every_token_from_the_design_system_is_present() -> None:
    expected = {
        "bg", "surface", "raised", "well", "line", "line_strong",
        "ink", "muted", "ok", "warn", "alarm", "accent",
    }
    assert set(PALETTE) == expected


def test_every_colour_is_a_hex_value_qt_can_parse() -> None:
    for name, value in PALETTE.items():
        assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{name} is not a Qt hex colour"


def test_the_video_well_is_the_darkest_surface() -> None:
    """DESIGN.md: nothing except video is this dark."""
    def brightness(hex_colour: str) -> int:
        return sum(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))

    assert brightness(PALETTE["well"]) < brightness(PALETTE["bg"])


def test_the_stylesheet_uses_the_palette_and_square_corners() -> None:
    sheet = stylesheet()
    assert PALETTE["bg"] in sheet
    assert PALETTE["accent"] in sheet
    assert "border-radius: 0" in sheet
