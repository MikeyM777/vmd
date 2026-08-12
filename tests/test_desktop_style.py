"""The palette: converted once from DESIGN.md, and never guessed at again."""

from __future__ import annotations

import re

from vmd.desktop.style import CHECKBOX_SIZE, PALETTE, stylesheet


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


# ------------------------------------------------------------- the tick boxes
#
# "There should be zero doubt about whether a box is checked or unchecked." Half
# the settings on his console are tick boxes, read from two metres back, and
# Qt's own indicator is 13 logical pixels - narrower than the word beside it -
# with a pale tick inside a pale box.


def test_a_tick_box_is_big_enough_to_be_seen_from_across_the_room() -> None:
    """Not a preference. The console is a dedicated laptop nobody sits at, and
    Qt's default is smaller than the letters it is drawn beside."""
    assert CHECKBOX_SIZE >= 20, CHECKBOX_SIZE
    sheet = stylesheet()
    assert f"width: {CHECKBOX_SIZE}px" in sheet, sheet
    assert f"height: {CHECKBOX_SIZE}px" in sheet, sheet


def _drawn(qtbot, checked: bool):
    """One tick box, painted exactly as the console paints it."""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QCheckBox

    box = QCheckBox("Watch thermal for movement")
    qtbot.addWidget(box)
    box.setStyleSheet(stylesheet())
    box.setChecked(checked)
    box.resize(360, 44)
    image = QImage(box.size(), QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)
    box.render(image)
    return image.convertToFormat(QImage.Format.Format_Grayscale8)


def _brightness(image) -> float:
    total = 0
    for y in range(image.height()):
        for x in range(image.width()):
            total += image.pixelColor(x, y).red()
    return total / (image.width() * image.height())


def test_a_ticked_box_and_an_unticked_one_are_not_the_same_picture(qtbot) -> None:
    """Measured in grey, with every colour thrown away first.

    DESIGN.md: colour never carries meaning alone. A checked state told apart
    only by a hue is a checked state that is not told apart at all by the
    operator on the day the panel is washed out by the window behind him - and
    this is the one control on the console where being wrong is silent.

    So the two states are rendered and flattened to grey, and the difference has
    to survive that. It does because the box is hollow when it is off and solid
    when it is on, and because the row behind the words is painted too.
    """
    off = _brightness(_drawn(qtbot, checked=False))
    on = _brightness(_drawn(qtbot, checked=True))
    assert on - off > 2.0, f"unticked {off:.2f}, ticked {on:.2f} - the same grey"
