"""Drawing the ignored areas on the picture, with the mouse and nothing else.

The operator asked for this twice, in his own words. First: "Parts to ignore.
Make it a general masking tool, not just for the skyline. Add options to use
different shapes or draw the ignored area directly on the picture. Remove any
prompt asking to define the area using dots/coordinates. It needs to be strictly
visual and easy to understand." And then, asked whether a rectangle would do:
"yes, free hand".

What he was being shown instead was `120 x 80 dots, at 30 across and 40 down`,
in a list beside the picture - the exact opposite of what he asked for, on a
machine with no terminal, for a man who is not an engineer. And the rectangle
those numbers describe cannot describe the thing the setting exists for: a
treeline is a ragged band across a hillside, so a box round it either throws
away the sky above it or leaves half the branches watched, which is why a
swaying tree could alarm all day with the feature switched on.

So there are no numbers on this dialog. Not a coordinate, not a size, not a
count of the areas drawn, not a list of them as text. What is ignored is shaded
on the picture, where it is, and the way to take one off is to click it.

Three things carry the weight.

* The mapping from a place on screen to a dot of the real picture is
  `vmd/desktop/picker.py`'s, not a second one of this file's own. The preview is
  scaled to fit whatever the dialog is and the setting is absolute; a mask drawn
  against one mapping and applied against another is wrong in every dot and
  says nothing about it.
* The outline is thinned before it leaves. A freehand drag reports the mouse
  every few pixels, so one treeline is hundreds of points, and hundreds of
  points in a settings file is a bitmap written in JSON. `sparse_outline` keeps
  the corners and throws away everything within a couple of dots of the line
  through them - which on a treeline is the treeline.
* Which shape a click landed in is answered by `vmd/detect/mask.py`, the same
  arithmetic the detector uses to decide what the shape covers. Two answers to
  that question that can differ is an operator deleting a shape that is not the
  one silencing his perimeter, and nothing anywhere saying so.

Freehand is what it opens on. The box is offered because a road or the sky is a
box and dragging one is faster than tracing it, but the one that had to work
well is the one he asked for.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.live import WrappedNote
from vmd.desktop.picker import (
    CLICK_SLOP,
    SHADE_ALPHA,
    SHADE_CHOSEN,
    PictureArea,
    is_blank,
    region_between,
    scale_of,
    to_frame,
    to_view,
)
from vmd.desktop.style import (
    PALETTE,
    SIZE_BODY,
    SPACE_SNUG,
    SPACE_STEP,
    WEIGHT_VALUE,
)
from vmd.detect.mask import contains, sparse_outline

# The two ways to draw one. Named rather than spelled at each use: they are
# compared in three places and a typo in any of them is a mode that silently
# never turns on.
FREEHAND = "freehand"
BOX = "box"

# How far apart, on screen, two reported mouse positions have to be before the
# second one is kept while tracing. A slow hand reports the same dot a dozen
# times and a fast one skips; this is only about not storing the same place
# twice before the thinning below has had a look at it.
TRACE_STEP = 1.0

# How far the stored outline is allowed to sit from the line the operator
# actually traced, in the dots of the screen he traced it on rather than in the
# picture's own. That is the honest unit: what is thrown away is what he could
# not have seen himself drawing. Two dots on a 1366-wide console is under a
# millimetre, and it is what turns a six-hundred-point drag into a few dozen
# points that still follow the treeline.
TRACE_TOLERANCE = 2.0

# How thick the edge of an ignored area is drawn, and how thick the one under
# the pointer is. The shading colour and its opacity are the picker's, by name,
# so an area drawn here and an area drawn there are the same thing on screen.
EDGE_WIDTH = 1
EDGE_WIDTH_UNDER = 3


class MaskCanvas(PictureArea):
    """The picture, the areas drawn on it, and the mouse.

    Drag to draw one, click one to take it off. Everything it reports is in the
    frame's own dots, so what leaves this widget is what goes in the settings
    file whatever size the dialog happened to be.
    """

    shapes_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shapes: list[list[tuple[int, int]]] = []
        self._mode = FREEHAND
        self._press: QPointF | None = None
        self._trace: list[QPointF] = []
        self._here: QPointF | None = None
        # Which area the pointer is over. It is marked before it is clicked,
        # because "click one to take it off" has to be discoverable without a
        # sentence saying so - and because clicking the wrong one is the mistake
        # this whole dialog exists to make correctable.
        self._under = -1
        # Hover needs the moves that arrive with no button held.
        self.setMouseTracking(True)

    # -- what is on it ------------------------------------------------------

    def shapes(self) -> list[list[tuple[int, int]]]:
        return [list(shape) for shape in self._shapes]

    def set_shapes(self, shapes) -> None:
        self._shapes = [[(int(x), int(y)) for x, y in shape] for shape in shapes]
        self._under = -1
        self.update()

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = BOX if mode == BOX else FREEHAND
        self._press = None
        self._trace = []
        self.update()

    def undo(self) -> None:
        """Take off the one drawn last."""
        if not self._shapes:
            return
        self._shapes.pop()
        self._under = -1
        self.update()
        self.shapes_changed.emit()

    def clear(self) -> None:
        if not self._shapes:
            return
        self._shapes = []
        self._under = -1
        self.update()
        self.shapes_changed.emit()

    # -- drawing on it ------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.has_frame() or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = QPointF(event.position())
        self._trace = [QPointF(event.position())]

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        here = QPointF(event.position())
        self._here = here
        if self._press is None:
            self._hover(here)
            return
        last = self._trace[-1]
        if abs(here.x() - last.x()) >= TRACE_STEP or abs(here.y() - last.y()) >= TRACE_STEP:
            self._trace.append(here)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press is None or not self.has_frame():
            return
        start, self._press = self._press, None
        trace, self._trace = self._trace + [QPointF(event.position())], []
        end = QPointF(event.position())
        # How far the mouse ever went from where it was pressed, rather than how
        # far apart the two ends are. A drag round a tree comes back to where it
        # started, and read as the distance between the ends it would be a click
        # every time - which would delete an area instead of drawing one.
        moved = max(
            max(abs(point.x() - start.x()), abs(point.y() - start.y())) for point in trace
        )
        if moved <= CLICK_SLOP:
            self._take_off(end)
            return
        drawn = self._box(start, end) if self._mode == BOX else self._traced(trace)
        if drawn:
            self._shapes.append(drawn)
            self.shapes_changed.emit()
        self._hover(end)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Nothing is under a pointer that has left the picture."""
        self._here = None
        if self._under != -1:
            self._under = -1
            self.update()
        super().leaveEvent(event)

    def _hover(self, where: QPointF) -> None:
        under = self.shape_at(where)
        if under != self._under:
            self._under = under
            self.update()

    def _take_off(self, where: QPointF) -> None:
        """A click: whichever area is under it comes off, and nothing else.

        A click on the bare picture does nothing at all. It could have meant
        something - the sky line dialog reads one as placing the line - and here
        there is nothing it could sensibly mean except "I meant to drag".
        """
        index = self.shape_at(where)
        if index < 0:
            return
        del self._shapes[index]
        self._under = -1
        self.update()
        self.shapes_changed.emit()

    def shape_at(self, where: QPointF) -> int:
        """Which drawn area a place on screen is inside, or -1.

        The last one drawn wins where two overlap, because that is the one on
        top and the one he can see himself pointing at.
        """
        if not self.has_frame():
            return -1
        x, y = to_frame(where, self.size(), self.frame_size())
        for index in reversed(range(len(self._shapes))):
            if contains(self._shapes[index], x, y):
                return index
        return -1

    def _traced(self, trace: list[QPointF]) -> list[tuple[int, int]]:
        """A freehand drag, as the few points worth keeping of it.

        The tolerance is fixed on screen and converted here, so what is thrown
        away is what he could not see whichever size the dialog is: a preview at
        half the frame's size means two dots of his hand are four of the
        picture's, and thinning at two of the picture's would keep points that
        were never distinguishable.
        """
        frame = self.frame_size()
        points = [to_frame(point, self.size(), frame) for point in trace]
        scale = scale_of(self.size(), frame)
        tolerance = TRACE_TOLERANCE / scale if scale > 0 else TRACE_TOLERANCE
        return sparse_outline(points, tolerance)

    def _box(self, start: QPointF, end: QPointF) -> list[tuple[int, int]]:
        """A dragged box, as the four corners of one.

        The same arithmetic the sky-line dialog draws its patches with, so a box
        dragged in either tool covers the same part of the picture - including
        being drawn upwards and to the left, which half of people do.
        """
        frame = self.frame_size()
        x, y, w, h = region_between(start, end, self.size(), frame)
        if w <= 0 or h <= 0:
            return []
        right = min(x + w, frame.width() - 1)
        bottom = min(y + h, frame.height() - 1)
        if right <= x or bottom <= y:
            return []
        return [(x, y), (right, y), (right, bottom), (x, bottom)]

    # -- what it looks like -------------------------------------------------

    def paint_over(self, painter: QPainter, picture: QRect) -> None:
        painter.setClipRect(picture)
        for index, shape in enumerate(self._shapes):
            self._paint_shape(painter, shape, index == self._under)
        if self._press is not None:
            self._paint_in_progress(painter)
        painter.setClipping(False)

    def _paint_shape(self, painter: QPainter, shape, under: bool) -> None:
        """One ignored area: shaded where it is, and outlined.

        Shaded rather than filled. He has to be able to see what he is ignoring
        - an area painted over solidly is one he cannot check he drew in the
        right place, which is the whole fault this tool corrects.
        """
        polygon = QPolygonF(
            [to_view(x, y, self.size(), self.frame_size()) for x, y in shape]
        )
        shade = QColor(PALETTE["alarm"])
        shade.setAlpha(SHADE_CHOSEN if under else SHADE_ALPHA)
        painter.setPen(
            QPen(QColor(PALETTE["alarm"]), EDGE_WIDTH_UNDER if under else EDGE_WIDTH)
        )
        painter.setBrush(shade)
        painter.drawPolygon(polygon)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_in_progress(self, painter: QPainter) -> None:
        """What is being drawn right now, while the button is still down.

        In the ink colour and dashed, so it reads as something not finished
        rather than as an area that is already being ignored.
        """
        painter.setPen(QPen(QColor(PALETTE["ink"]), EDGE_WIDTH, Qt.PenStyle.DashLine))
        if self._mode == BOX:
            if self._here is not None:
                painter.drawRect(QRect(self._press.toPoint(), self._here.toPoint()))
            return
        if len(self._trace) > 1:
            painter.drawPolyline(QPolygonF(self._trace))


class MaskDialog(QDialog):
    """The picture, and everything he can do to it with a mouse.

    Built around a frame that has already been fetched rather than one it goes
    and gets: getting it crosses the radio link and takes seconds, the tab that
    opens this already knows how to do that off the window thread, and a dialog
    that cannot be opened without a camera is a dialog that cannot undo a
    mistake made while the camera was working.

    `frame` is a picture, or nothing when there was none. The bytes a grab hands
    back are accepted as well as a `QImage`, and refused in the same words the
    sky-line dialog refuses them in: bytes that are not a picture, and a picture
    with nothing in it, are both worse than no picture at all, because a line
    dragged onto a black rectangle is saved and believed.
    """

    def __init__(
        self,
        frame: QImage | bytes | None,
        shapes,
        problem: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Parts of the picture to ignore")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_STEP, SPACE_STEP, SPACE_STEP, SPACE_STEP)
        outer.setSpacing(SPACE_STEP)

        self.instructions = WrappedNote(
            "Drag around anything you want ignored - a treeline that sways, a "
            "flag, a road. Draw as many as you like. Click one to take it off "
            "again."
        )
        outer.addWidget(self.instructions)

        self.canvas = MaskCanvas()
        self.canvas.set_shapes(shapes)
        outer.addWidget(self.canvas, 1)

        # Two ways to draw one, one of them always on: a segmented control, drawn
        # the way this design draws one - the chosen one raised, in the ink
        # colour, heavier, under the accent bar the tab bar uses for the page you
        # are on. Left to a `:checked` rule it would not be drawn at all: the
        # application stylesheet has no opinion about a checked button, so the
        # mode he is in and the mode he is not would be the same picture.
        tools = QHBoxLayout()
        tools.setSpacing(SPACE_SNUG)
        self.freehand_button = QPushButton("Draw round it")
        self.box_button = QPushButton("Drag a box")
        self._modes = QButtonGroup(self)
        self._modes.setExclusive(True)
        for button in (self.freehand_button, self.box_button):
            button.setCheckable(True)
            self._modes.addButton(button)
            tools.addWidget(button)
        self.freehand_button.setChecked(True)
        self.freehand_button.clicked.connect(lambda: self.set_mode(FREEHAND))
        self.box_button.clicked.connect(lambda: self.set_mode(BOX))

        tools.addStretch(1)
        self.undo_button = QPushButton("Undo the last one")
        self.undo_button.clicked.connect(self.canvas.undo)
        self.clear_button = QPushButton("Take them all off")
        self.clear_button.clicked.connect(self.canvas.clear)
        tools.addWidget(self.undo_button)
        tools.addWidget(self.clear_button)
        outer.addLayout(tools)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.use_button = QPushButton("Use what I drew")
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.use_button)
        outer.addLayout(buttons)

        self._draw_modes()
        self._show(frame, problem)

    # -- what he drew -------------------------------------------------------

    def shapes(self) -> list[list[tuple[int, int]]]:
        """The ignored areas, in the picture's own dots."""
        return self.canvas.shapes()

    def mode(self) -> str:
        return self.canvas.mode()

    def set_mode(self, mode: str) -> None:
        self.canvas.set_mode(mode)
        self.freehand_button.setChecked(self.canvas.mode() == FREEHAND)
        self.box_button.setChecked(self.canvas.mode() == BOX)
        self._draw_modes()

    def words_on_screen(self) -> list[str]:
        """Every word this dialog puts in front of the operator.

        Read by the test that says none of them is a number, which is the thing
        he asked for in the plainest terms he used anywhere: "Remove any prompt
        asking to define the area using dots/coordinates."
        """
        return [
            self.windowTitle(),
            self.instructions.text(),
            self.freehand_button.text(),
            self.box_button.text(),
            self.undo_button.text(),
            self.clear_button.text(),
            self.cancel_button.text(),
            self.use_button.text(),
            self.canvas.state_words(),
        ]

    def mode_mark(self, button: QPushButton) -> str:
        """How a mode button is drawn, so a test can read it off the dialog.

        Returned rather than compared here: what has to be true is that the mode
        he is in does not look like the one he is not, and that is a question
        about what is drawn rather than about a particular colour.
        """
        return button.styleSheet()

    # -- the picture --------------------------------------------------------

    def _show(self, frame, problem: str) -> None:
        """Put the picture up, or the reason there is not one.

        No picture is never a dead end. The camera is off, or the link is down,
        or go2rtc has not been started - and the areas he has already drawn are
        still his to delete, so the dialog opens either way and what he had is
        handed straight back if he leaves it alone.
        """
        image = _as_picture(frame)
        if isinstance(image, QImage):
            self.canvas.set_frame(image)
            return
        self.canvas.show_problem(image or problem or _NO_PICTURE)

    def _draw_modes(self) -> None:
        for button in (self.freehand_button, self.box_button):
            on = button.isChecked()
            button.setStyleSheet(
                f"QPushButton {{ background: "
                f"{PALETTE['raised'] if on else PALETTE['surface']}; "
                f"color: {PALETTE['ink'] if on else PALETTE['muted']}; "
                f"border: 1px solid {PALETTE['line']}; "
                f"border-bottom: 2px solid "
                f"{PALETTE['accent'] if on else PALETTE['line']}; "
                f"font-size: {SIZE_BODY}px; "
                f"font-weight: {WEIGHT_VALUE if on else 400}; }}"
                f"QPushButton:hover {{ color: {PALETTE['ink']}; }}"
            )


# What is written across the picture area when there is no picture and nobody
# said why. It should not happen - the tab that opens this carries the reason -
# and an empty black rectangle with no explanation is the failure this console
# has already been reported for once.
_NO_PICTURE = (
    "There is no picture from the camera, so there is nothing to draw on. "
    "Anything already marked out is still here, and Cancel leaves it alone."
)


def _as_picture(frame) -> QImage | str:
    """The frame as a picture, or the sentence saying why it is not one.

    Bytes as well as a `QImage`, because a grab hands back the bytes of a JPEG
    and the tab that opens this passes them straight through. Both refusals are
    the sky-line dialog's own words: what came back was not a picture, and what
    came back was blank. The second matters more than it looks - a valid JPEG of
    nothing happens while a stream is still coming up, nothing about it looks
    like a failure, and an area drawn on it would be saved as a real setting
    that quietly silences part of a picture he never looked at.
    """
    if isinstance(frame, QImage):
        return frame if not frame.isNull() else ""
    if not isinstance(frame, (bytes, bytearray)) or not frame:
        return ""
    image = QImage()
    if not image.loadFromData(bytes(frame)) or image.isNull():
        return (
            "What came back from the camera was not a picture, so there is "
            "nothing to draw on."
        )
    if is_blank(image):
        return (
            "The picture that came back is blank, so there is nothing to draw "
            "on. The camera may still be starting up - try again in a moment."
        )
    return image
