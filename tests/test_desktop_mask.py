"""Drawing the ignored areas on the picture, with the mouse and nothing else.

The operator asked for this in his own words: "Remove any prompt asking to
define the area using dots/coordinates. It needs to be strictly visual and easy
to understand." And when he was asked whether a rectangle would do: "yes, free
hand".

So two things are tested harder than anything else here. That a drag round a
treeline becomes an area in the picture's own dots at any dialog size - the
mapping is `vmd/desktop/picker.py`'s own, because a mask drawn against one
mapping and applied against another is wrong without a word of complaint. And
that nothing this dialog puts on screen is a number.
"""

from __future__ import annotations

import math
import re

import pytest
from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from vmd.desktop.mask import MaskCanvas, MaskDialog
from vmd.desktop.picker import to_frame
from vmd.settings import IgnoreShape

FRAME_W, FRAME_H = 1280, 720


def a_picture(width: int = FRAME_W, height: int = FRAME_H) -> QImage:
    """A photograph-like frame: a sky gradient with a dark band across it."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    sky = QLinearGradient(0, 0, 0, height)
    sky.setColorAt(0.0, QColor(24, 30, 44))
    sky.setColorAt(1.0, QColor(180, 186, 198))
    painter = QPainter(image)
    painter.fillRect(0, 0, width, height, sky)
    painter.fillRect(0, height // 2, width, height // 6, QColor(38, 46, 40))
    painter.end()
    return image


# "The caller did not say", which is not the same as "there is no picture" -
# and no picture is a case this dialog has to handle rather than a mistake.
UNSAID = object()


def a_dialog(qtbot, frame=UNSAID, shapes=(), problem="", view=(640, 360)):
    dialog = MaskDialog(
        a_picture() if frame is UNSAID else frame,
        [list(shape) for shape in shapes],
        problem=problem,
    )
    qtbot.addWidget(dialog)
    sized(dialog.canvas, *view)
    return dialog


def sized(canvas: MaskCanvas, width: int, height: int) -> None:
    """Force the picture area to an exact size.

    The real one has a floor under it so it is big enough to aim at; these tests
    are about the arithmetic between a place on screen and a dot of the frame.
    """
    canvas.setMinimumSize(1, 1)
    # Fixed rather than resized: the canvas is in a layout, and the layout takes
    # the size back the moment anything asks the dialog to lay itself out -
    # which grabbing a picture of it does.
    canvas.setFixedSize(width, height)


def drag(canvas: MaskCanvas, path) -> None:
    """One press, the moves between, and the release - as Qt would deliver them.

    Sent rather than synthesised through the window system: this suite never
    shows a window, and an unmapped widget gets no real mouse at all.
    """
    left = Qt.MouseButton.LeftButton
    none = Qt.MouseButton.NoButton
    modifiers = Qt.KeyboardModifier.NoModifier
    for index, (x, y) in enumerate(path):
        if index == 0:
            kind, buttons = QMouseEvent.Type.MouseButtonPress, left
        elif index == len(path) - 1:
            kind, buttons = QMouseEvent.Type.MouseButtonRelease, none
        else:
            kind, buttons = QMouseEvent.Type.MouseMove, left
        where = QPointF(x, y)
        QApplication.sendEvent(
            canvas, QMouseEvent(kind, where, where, where, left, buttons, modifiers)
        )


def click(canvas: MaskCanvas, x: float, y: float) -> None:
    """A press and a release on the same dot: not a drag, whatever else it is."""
    drag(canvas, [(x, y), (x, y)])


def a_traced_circle(cx: float, cy: float, r: float, steps: int = 200) -> list:
    """A drag round something, at the rate a mouse really reports one."""
    return [
        (cx + r * math.cos(2 * math.pi * i / steps), cy + r * math.sin(2 * math.pi * i / steps))
        for i in range(steps + 1)
    ]


# --------------------------------------------------------------- drawing one


def test_a_drag_round_something_becomes_one_ignored_area(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    assert len(dialog.shapes()) == 1
    points = dialog.shapes()[0]
    assert len(points) >= 3
    assert all(isinstance(x, int) and isinstance(y, int) for x, y in points)


def test_what_was_drawn_is_in_the_pictures_own_dots(qtbot) -> None:
    """The preview is scaled to fit the dialog; the setting is absolute.

    Confusing the two misplaces every area without a word of complaint, so the
    same drag is made at two dialog sizes and has to mean the same part of the
    picture both times.
    """
    small = a_dialog(qtbot, view=(640, 360))
    drag(small.canvas, a_traced_circle(200, 150, 60))
    large = a_dialog(qtbot, view=(1280, 720))
    drag(large.canvas, a_traced_circle(400, 300, 120))

    def middle(shape):
        return (
            sum(x for x, _y in shape) / len(shape),
            sum(y for _x, y in shape) / len(shape),
        )

    sx, sy = middle(small.shapes()[0])
    lx, ly = middle(large.shapes()[0])
    assert abs(sx - lx) < 8, (sx, lx)
    assert abs(sy - ly) < 8, (sy, ly)
    # And it is the frame's own dots, not the preview's: the drag was centred a
    # quarter of the way across a 640-wide preview of a 1280-wide picture.
    assert abs(sx - 400) < 8


def test_the_area_never_runs_off_the_picture(qtbot) -> None:
    """A drag that wanders into the dead space beside a letterboxed picture."""
    dialog = a_dialog(qtbot, view=(400, 400))  # 1280x720 letterboxed into a square
    drag(dialog.canvas, a_traced_circle(200, 200, 300))
    for x, y in dialog.shapes()[0]:
        assert 0 <= x < FRAME_W
        assert 0 <= y < FRAME_H


def test_a_traced_area_is_stored_as_a_few_dozen_points(qtbot) -> None:
    """A mouse event every few pixels is not a setting, it is a bitmap in JSON.

    The operator may have to read this file. Two hundred moves went in.
    """
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60, steps=200))
    assert len(dialog.shapes()[0]) < 60


def test_every_area_it_returns_is_one_the_settings_file_will_accept(qtbot) -> None:
    """`IgnoreShape` refuses fewer than three points and anything negative."""
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    drag(dialog.canvas, a_traced_circle(400, 250, 40))
    for shape in dialog.shapes():
        assert IgnoreShape(points=shape).as_tuples() == shape


def test_several_areas_can_be_drawn(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(150, 120, 50))
    drag(dialog.canvas, a_traced_circle(400, 250, 50))
    assert len(dialog.shapes()) == 2


def test_a_box_is_drawn_as_a_box(qtbot) -> None:
    """A road and the sky are boxes, and dragging one is faster than tracing it."""
    dialog = a_dialog(qtbot)
    dialog.box_button.click()
    drag(dialog.canvas, [(100, 60), (200, 100), (300, 160)])
    shape = dialog.shapes()[0]
    assert len(shape) == 4
    xs = sorted({x for x, _y in shape})
    ys = sorted({y for _x, y in shape})
    assert len(xs) == 2 and len(ys) == 2
    assert (xs[0], ys[0]) == to_frame(QPoint(100, 60), QSize(640, 360), QSize(FRAME_W, FRAME_H))


def test_a_box_dragged_backwards_is_still_a_box(qtbot) -> None:
    """Half of people drag upwards and to the left."""
    dialog = a_dialog(qtbot)
    dialog.box_button.click()
    drag(dialog.canvas, [(300, 160), (200, 100), (100, 60)])
    assert len(dialog.shapes()[0]) == 4


def test_freehand_is_what_it_starts_on(qtbot) -> None:
    """The one that had to work well is the one he does not have to choose."""
    dialog = a_dialog(qtbot)
    assert dialog.freehand_button.isChecked()
    assert not dialog.box_button.isChecked()


def test_a_wobble_too_small_to_enclose_anything_is_not_an_area(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, [(100, 100), (104, 101), (108, 100), (108, 100)])
    assert dialog.shapes() == []


# ------------------------------------------------------------- taking one off


def test_clicking_an_area_takes_it_off(qtbot) -> None:
    """The way out of a mistake, without a list and without a number."""
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    assert len(dialog.shapes()) == 1
    click(dialog.canvas, 200, 150)
    assert dialog.shapes() == []


def test_clicking_the_bare_picture_takes_nothing_off(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    click(dialog.canvas, 500, 300)
    assert len(dialog.shapes()) == 1


def test_the_one_underneath_the_click_is_the_one_taken_off(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(150, 120, 40))
    drag(dialog.canvas, a_traced_circle(400, 250, 40))
    click(dialog.canvas, 400, 250)
    assert len(dialog.shapes()) == 1
    # What is left is the one on the left: a half-size preview, so everything
    # it covers is in the left half of the picture.
    assert max(x for x, _y in dialog.shapes()[0]) < FRAME_W / 2


def test_undo_takes_off_the_last_one(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(150, 120, 40))
    drag(dialog.canvas, a_traced_circle(400, 250, 40))
    dialog.undo_button.click()
    assert len(dialog.shapes()) == 1


def test_undo_with_nothing_drawn_does_nothing(qtbot) -> None:
    dialog = a_dialog(qtbot)
    dialog.undo_button.click()
    assert dialog.shapes() == []


def test_everything_can_be_taken_off_at_once(qtbot) -> None:
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(150, 120, 40))
    drag(dialog.canvas, a_traced_circle(400, 250, 40))
    dialog.clear_button.click()
    assert dialog.shapes() == []


# --------------------------------------------------------- what he was given


def test_the_areas_it_was_given_come_back_unchanged(qtbot) -> None:
    """Opening the dialog and pressing Cancel loses nothing he had drawn."""
    already = [[(100, 100), (400, 120), (380, 300), (90, 280)]]
    dialog = a_dialog(qtbot, shapes=already)
    assert dialog.shapes() == already


def test_an_area_drawn_before_can_be_clicked_off(qtbot) -> None:
    dialog = a_dialog(qtbot, shapes=[[(100, 100), (400, 100), (400, 300), (100, 300)]])
    # The middle of that area, in the preview's dots: half size, so half of it.
    click(dialog.canvas, 125, 100)
    assert dialog.shapes() == []


# ------------------------------------------------------------- no picture yet


def test_with_no_picture_it_says_why_and_changes_nothing(qtbot) -> None:
    """The camera is off, or the link is down, and he still has to get out.

    What he had drawn is handed straight back rather than emptied - the failure
    is the camera's, and losing his treeline over it would be this dialog's.
    """
    already = [[(100, 100), (400, 120), (380, 300)]]
    dialog = a_dialog(
        qtbot,
        frame=None,
        shapes=already,
        problem="The camera did not send a picture.",
    )
    assert "did not send a picture" in dialog.canvas.state_words()
    assert dialog.shapes() == already


def test_with_no_picture_nothing_can_be_drawn(qtbot) -> None:
    """A line dragged onto a black rectangle would be saved and believed."""
    dialog = a_dialog(qtbot, frame=None, problem="No picture.")
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    assert dialog.shapes() == []


# ------------------------------------------------------- strictly visual only


def test_nothing_on_screen_is_a_number(qtbot) -> None:
    """His words: no dots, no coordinates, nothing to read twice.

    Not one digit anywhere - not a count of areas, not a size, not a caption
    saying which is which.
    """
    dialog = a_dialog(qtbot, shapes=[[(100, 100), (400, 120), (380, 300)]])
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    for words in dialog.words_on_screen():
        assert not re.search(r"\d", words), words


def test_what_is_ignored_is_shown_on_the_picture(qtbot) -> None:
    """Shaded, where it is, rather than listed as text beside it."""
    dialog = a_dialog(qtbot)
    bare = dialog.canvas.grab().toImage()
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    drawn = dialog.canvas.grab().toImage()
    assert drawn.pixel(200, 150) != bare.pixel(200, 150), "the area is not shaded"
    assert drawn.pixel(500, 300) == bare.pixel(500, 300), "the rest of the picture moved"


def test_the_picture_itself_is_still_visible_under_the_shading(qtbot) -> None:
    """Shaded, not painted over: he has to see what he is ignoring."""
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, [(100, 40), (100, 300), (300, 300), (300, 40), (100, 40)])
    drawn = dialog.canvas.grab().toImage()
    # Two dots inside the same area, over different parts of the picture: a
    # solid fill would make them equal.
    assert drawn.pixel(150, 60) != drawn.pixel(150, 280)


def test_the_one_under_the_pointer_is_marked_before_it_is_clicked_off(qtbot) -> None:
    """Clicking to delete has to be discoverable without a sentence about it."""
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    quiet = dialog.canvas.grab().toImage()
    where = QPointF(200, 150)
    QApplication.sendEvent(
        dialog.canvas,
        QMouseEvent(
            QMouseEvent.Type.MouseMove,
            where,
            where,
            where,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert dialog.canvas.grab().toImage().pixel(200, 150) != quiet.pixel(200, 150)


def test_the_way_of_drawing_that_is_on_is_not_drawn_like_the_one_that_is_off(
    qtbot,
) -> None:
    """A checked button is not marked by the application stylesheet at all.

    Both modes would be the same picture, and which one a drag was about to do
    could only be found out by doing it.
    """
    dialog = a_dialog(qtbot)
    assert dialog.mode_mark(dialog.freehand_button) != dialog.mode_mark(dialog.box_button)
    dialog.box_button.click()
    assert dialog.mode() == "box"
    assert dialog.mode_mark(dialog.box_button) != dialog.mode_mark(dialog.freehand_button)


def test_the_dialog_hands_back_what_was_drawn_when_it_is_accepted(qtbot) -> None:
    """The contract the settings form is built against."""
    dialog = a_dialog(qtbot)
    drag(dialog.canvas, a_traced_circle(200, 150, 60))
    dialog.accept()
    assert dialog.result() == 1
    assert len(dialog.shapes()) == 1


@pytest.mark.parametrize("view", [(640, 360), (1600, 900), (400, 400)])
def test_a_drag_means_the_same_area_at_any_dialog_size(qtbot, view) -> None:
    """Smaller than the frame, larger than it, and letterboxed."""
    dialog = a_dialog(qtbot, view=view)
    canvas = dialog.canvas
    centre = (canvas.width() / 2, canvas.height() / 2)
    drag(canvas, a_traced_circle(centre[0], centre[1], min(centre) / 4))
    shape = dialog.shapes()[0]
    mid_x = sum(x for x, _y in shape) / len(shape)
    mid_y = sum(y for _x, y in shape) / len(shape)
    assert abs(mid_x - FRAME_W / 2) < 10, view
    assert abs(mid_y - FRAME_H / 2) < 10, view
