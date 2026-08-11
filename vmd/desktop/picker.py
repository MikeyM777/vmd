"""A picture from the camera, to draw the sky line and the ignored patches on.

Both of those settings are native-frame pixel coordinates, and both were typed
blind: a spin box, no picture, and nothing on screen saying how tall the frame
is. Nobody can read "340" off a treeline. The design calls these regions
operator-painted; until this file they were operator-guessed, and the cost is
one-sided - a wrong sky line throws away real movement below it and never says
so, and a patch in the wrong place does the same over a rectangle.

Three things carry the weight here.

* The picture is fetched off the window thread, through the same QRunnable and
  QThreadPool seam the camera tools already use. It crosses a radio link and
  takes seconds; on the window thread that is a console that looks dead.
* What is drawn is converted to the frame's own dots, never the preview's. The
  preview is scaled to fit whatever the dialog is, the setting is absolute, and
  confusing the two misplaces every patch without a word of complaint. That
  conversion is four small functions with no widget in them, so it can be
  checked at a preview smaller than the frame, larger than it, and letterboxed.
* No picture is never a dead end. The camera is off, or the link is down, or
  go2rtc has not been started - and the operator still has to be able to
  configure the machine, so the failure is one plain sentence and the numeric
  boxes in the form are left exactly as they were.

A blank or stale preview would be worse than none, because the operator would
trust the line they dragged onto it. So bytes that are not a picture are refused
in the same words as no bytes at all.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import PALETTE

logger = logging.getLogger(__name__)

# How long the camera gets to produce one frame. Bounded on its own, because it
# is the only wait here that talks to the far end of a radio link, and every
# other wait in this file is bounded independently of it.
GRAB_SECONDS = 20

# How long the dialog waits, at most, for a fetch to finish once it has been
# closed. Bounded separately from the grab above: this one is on the window
# thread, and a console frozen while the operator closes a dialog is exactly the
# failure this whole file exists to avoid.
CLOSE_WAIT_MS = 3000

# A press and a release this far apart, or less, in the preview's own dots, is a
# click rather than a drag. Nobody releases the mouse on the dot they pressed.
CLICK_SLOP = 4


class FrameUnavailable(RuntimeError):
    """No picture, in one sentence the operator can act on.

    Never carries the address it tried: that address has the password
    percent-encoded into it, and this text goes on screen.
    """


# --------------------------------------------------------------- the geometry
#
# Widget-free on purpose. Everything below is checkable at any preview size
# without a window, which is what makes "the settings are absolute" a fact
# rather than an intention.


def scale_of(view: QSize, frame: QSize) -> float:
    """How many preview dots one frame dot is drawn as, fitted and undistorted."""
    if frame.width() <= 0 or frame.height() <= 0:
        return 1.0
    return min(view.width() / frame.width(), view.height() / frame.height())


def origin_of(view: QSize, frame: QSize) -> QPointF:
    """Where the picture starts inside the preview.

    A preview is rarely the frame's shape, so the picture is centred and the
    rest is dead space. Forgetting this offset shifts every patch by the depth
    of the bars, which looks plausible and is wrong.
    """
    scale = scale_of(view, frame)
    return QPointF(
        (view.width() - frame.width() * scale) / 2.0,
        (view.height() - frame.height() * scale) / 2.0,
    )


def to_frame(point: QPoint | QPointF, view: QSize, frame: QSize) -> tuple[int, int]:
    """A place on the preview, as a dot of the real picture.

    Clamped into the picture: a click on the dead space either side is still a
    click, and a negative row of dots is not a setting anything can use.
    """
    scale = scale_of(view, frame)
    if scale <= 0:
        return (0, 0)
    origin = origin_of(view, frame)
    x = int((point.x() - origin.x()) / scale)
    y = int((point.y() - origin.y()) / scale)
    return (
        max(0, min(frame.width() - 1, x)),
        max(0, min(frame.height() - 1, y)),
    )


def to_view(x: float, y: float, view: QSize, frame: QSize) -> QPointF:
    """A dot of the real picture, as a place on the preview."""
    scale = scale_of(view, frame)
    origin = origin_of(view, frame)
    return QPointF(origin.x() + x * scale, origin.y() + y * scale)


def _to_edge(point: QPoint | QPointF, view: QSize, frame: QSize) -> tuple[int, int]:
    """A place on the preview, as an edge between dots rather than a dot.

    The corner of a box is not a dot the way the sky line is a row of dots: a
    box drawn to the far right of the picture has to be able to reach the far
    right of the picture, which the last dot's own index never does.
    """
    scale = scale_of(view, frame)
    if scale <= 0:
        return (0, 0)
    origin = origin_of(view, frame)
    x = round((point.x() - origin.x()) / scale)
    y = round((point.y() - origin.y()) / scale)
    return (
        max(0, min(frame.width(), int(x))),
        max(0, min(frame.height(), int(y))),
    )


def region_between(
    start: QPoint | QPointF, end: QPoint | QPointF, view: QSize, frame: QSize
) -> tuple[int, int, int, int]:
    """The box between two places on the preview, in the real picture's dots.

    Whichever way round it was drawn, and never running off the edge of the
    picture: half of people drag a rectangle upwards and to the left, and a
    patch with a negative width is not a patch.
    """
    x1, y1 = _to_edge(start, view, frame)
    x2, y2 = _to_edge(end, view, frame)
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return (left, top, right - left, bottom - top)


# ------------------------------------------------------------------ the picture


class FramePicker(QWidget):
    """One frame, with the sky line and the patches drawn over it.

    Click puts the line; drag draws a patch. Both are reported in the frame's
    own dots, so what leaves this widget is what goes in the settings file.
    """

    horizon_picked = Signal(int)
    region_drawn = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._horizon: int | None = None
        self._regions: list[tuple[int, int, int, int]] = []
        self._selected = -1
        self._press: QPointF | None = None
        self._drag: QPointF | None = None
        self.setMinimumSize(360, 240)
        self.setMouseTracking(False)

    # -- what it is showing -------------------------------------------------

    def has_frame(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def frame_size(self) -> QSize:
        return self._image.size() if self.has_frame() else QSize(0, 0)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self.update()

    def horizon(self) -> int | None:
        return self._horizon

    def set_horizon(self, value: int | None) -> None:
        self._horizon = None if value is None else int(value)
        self.update()

    def regions(self) -> list[tuple[int, int, int, int]]:
        return list(self._regions)

    def set_regions(self, regions) -> None:
        self._regions = [tuple(int(n) for n in region) for region in regions]
        self.update()

    def select_region(self, index: int) -> None:
        self._selected = index
        self.update()

    # -- drawing on it ------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.has_frame() or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = QPointF(event.position())
        self._drag = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press is None:
            return
        self._drag = QPointF(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press is None or not self.has_frame():
            return
        start, self._press = self._press, None
        self._drag = None
        end = QPointF(event.position())
        frame = self.frame_size()
        moved = max(abs(end.x() - start.x()), abs(end.y() - start.y()))
        if moved <= CLICK_SLOP:
            _x, y = to_frame(end, self.size(), frame)
            self.set_horizon(y)
            self.horizon_picked.emit(y)
            return
        region = region_between(start, end, self.size(), frame)
        if region[2] <= 0 or region[3] <= 0:
            return
        self._regions.append(region)
        self.update()
        self.region_drawn.emit(region)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PALETTE["well"]))
        if not self.has_frame():
            painter.end()
            return
        frame = self.frame_size()
        scale = scale_of(self.size(), frame)
        top_left = to_view(0, 0, self.size(), frame)
        picture = QRect(
            int(top_left.x()),
            int(top_left.y()),
            int(frame.width() * scale),
            int(frame.height() * scale),
        )
        painter.drawImage(picture, self._image)

        if self._horizon is not None:
            y = to_view(0, self._horizon, self.size(), frame).y()
            sky = QRect(picture.left(), picture.top(), picture.width(), int(y) - picture.top())
            painter.fillRect(sky, QColor(0, 0, 0, 110))
            painter.setPen(QPen(QColor(PALETTE["accent"]), 2))
            painter.drawLine(picture.left(), int(y), picture.right(), int(y))

        for index, (x, y, w, h) in enumerate(self._regions):
            corner = to_view(x, y, self.size(), frame)
            box = QRect(
                int(corner.x()), int(corner.y()), max(1, int(w * scale)), max(1, int(h * scale))
            )
            chosen = index == self._selected
            painter.fillRect(box, QColor(255, 83, 75, 90 if chosen else 50))
            painter.setPen(QPen(QColor(PALETTE["alarm"]), 3 if chosen else 1))
            painter.drawRect(box)

        if self._press is not None and self._drag is not None:
            painter.setPen(QPen(QColor(PALETTE["ink"]), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRect(self._press.toPoint(), self._drag.toPoint()))
        painter.end()


# ------------------------------------------------------------- fetching a frame


class _FrameSignals(QObject):
    """The fetch talking back to the dialog it cannot touch directly."""

    done = Signal(object)


class _FrameJob(QRunnable):
    """One fetch, on a pool thread. The same shape as the camera tools' jobs.

    Whatever goes wrong comes back as a sentence rather than an exception on a
    thread nobody is watching: an unhandled one there would take the console
    with it and say nothing.
    """

    def __init__(self, work, signals: _FrameSignals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._work()
        except FrameUnavailable as exc:
            result = str(exc)
        except Exception as exc:  # noqa: BLE001 - a failed fetch must not end the console
            logger.exception("the picture could not be fetched")
            result = str(exc) or "The picture could not be fetched."
        self._signals.done.emit(result)


def _ffmpeg() -> str:
    from vmd.storage.recorder import find_tool

    return find_tool("ffmpeg")


def _address_of(settings, stream: str) -> str:
    """The address one frame can be pulled from, with the login already in it.

    Built the same way the streaming server's is, so that what is drawn on is
    the picture the detector will actually be given - including a stream set to
    the ffmpeg reader, which is stored wrapped and has no host of its own until
    it is unwrapped.
    """
    from vmd.streaming.go2rtc import build_config, probe_target

    chosen = next((s for s in settings.camera.streams if s.name == stream), None)
    if chosen is None or not chosen.url:
        raise FrameUnavailable(
            f'"{stream}" has no address yet, so there is no picture to draw on. '
            f"Type the address in above and press Save first."
        )
    config = build_config(settings, 1984, 8554)
    return probe_target(config["streams"].get(stream, chosen.url))


def _without_secrets(text: str, settings) -> str:
    """The same words with every password taken back out of them.

    ffmpeg quotes the address it was given back inside its own errors, and that
    address carries the password percent-encoded into it - `p@ss/word` travels
    as `p%40ss%2Fword`, which a search for the typed form does not match at all.
    """
    for secret in (settings.camera.password, settings.radio.password):
        if not secret:
            continue
        for form in (secret, quote(secret, safe="")):
            text = text.replace(form, "****")
    return text


def grab_frame(settings, stream: str, seconds: int = GRAB_SECONDS) -> bytes:
    """One frame from the named view, as the bytes of a picture.

    ffmpeg rather than anything richer, because it is already on this machine -
    the offline install puts it beside go2rtc - and it is what everything else
    here reads a stream with. One frame and out: this crosses the radio link the
    live picture is also using, and the operator is looking at a still.
    """
    address = _address_of(settings, stream)
    with tempfile.TemporaryDirectory(prefix="vmd-frame-") as folder:
        target = Path(folder) / "frame.jpg"
        try:
            run = subprocess.run(
                [
                    _ffmpeg(), "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp", "-timeout", "5000000",
                    "-i", address, "-frames:v", "1", "-f", "image2", "-y", str(target),
                ],
                capture_output=True, text=True, timeout=seconds, check=False,
            )
        except FileNotFoundError:
            raise FrameUnavailable(
                "The part of VMD that fetches pictures is not installed on this "
                "machine, so there is no picture to draw on. Type the numbers in "
                "instead."
            ) from None
        except subprocess.TimeoutExpired:
            raise FrameUnavailable(
                f"The camera sent no picture within {seconds} seconds. It may be "
                f"switched off, or the radio link may be down. Type the numbers "
                f"in instead."
            ) from None
        except OSError as exc:
            raise FrameUnavailable(
                f"The picture could not be fetched: {_without_secrets(str(exc), settings)}"
            ) from None
        if not target.exists() or target.stat().st_size == 0:
            # What it actually said goes to the log, not to the operator. It is
            # "[tcp @ 000001bf] Connection to tcp://10.0.0.2:554 failed: Error
            # number -138 occurred", which tells a non-technical operator
            # nothing and tells whoever reads the Logs tab everything. The
            # button that turns that into sentences already exists on this tab.
            complaint = (run.stderr or "").strip().splitlines()
            if complaint:
                logger.info(
                    "no frame from %s: %s", stream, _without_secrets(complaint[0], settings)
                )
            raise FrameUnavailable(
                "The camera did not send a picture, so there is nothing to draw "
                "on. Type the numbers in instead, or press \"Test the camera\" "
                "to find out why it is quiet."
            )
        return target.read_bytes()


# ------------------------------------------------------------------ the dialog


class PickerDialog(QDialog):
    """The picture, what has been drawn on it, and a way to undraw it.

    The delete path is the one that matters most today: an operator with a patch
    in the wrong place has no way out of it at all short of editing the settings
    file by hand, which nobody on this machine is ever going to do.
    """

    def __init__(
        self,
        stream: str,
        horizon: int | None,
        regions,
        grab,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Draw on the picture from {stream}")
        self._grab = grab
        self._closed = False
        self._signals = _FrameSignals()
        self._signals.done.connect(self._fetched)
        # This dialog's own, so that closing it waits for its own fetch and for
        # nothing else. One thread: there is one picture to fetch.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self.instructions = QLabel(
            "Click the picture to put the sky line where the ground stops. "
            "Drag a box over anything you want ignored - a tree that sways, a "
            "flag, a road."
        )
        self.instructions.setWordWrap(True)
        outer.addWidget(self.instructions)

        self.picker = FramePicker()
        self.picker.set_horizon(horizon)
        self.picker.set_regions(regions)
        self.picker.horizon_picked.connect(self._horizon_picked)
        self.picker.region_drawn.connect(self._region_drawn)

        middle = QHBoxLayout()
        middle.setSpacing(8)
        middle.addWidget(self.picker, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.horizon_label = QLabel("")
        self.horizon_label.setWordWrap(True)
        self.clear_horizon_button = QPushButton("Take the sky line off")
        self.clear_horizon_button.clicked.connect(self._clear_horizon)
        self.regions_label = QLabel("Patches that are ignored:")
        self.regions_list = QListWidget()
        self.regions_list.currentRowChanged.connect(self.picker.select_region)
        self.remove_button = QPushButton("Delete the selected patch")
        self.remove_button.clicked.connect(self._remove_region)
        self.size_label = QLabel("")
        self.size_label.setWordWrap(True)
        self.size_label.setStyleSheet(f"color: {PALETTE['muted']};")
        for widget in (
            self.horizon_label,
            self.clear_horizon_button,
            self.regions_label,
            self.regions_list,
            self.remove_button,
            self.size_label,
        ):
            side.addWidget(widget)
        side.addStretch(1)
        middle.addLayout(side)
        outer.addLayout(middle)

        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet(f"color: {PALETTE['warn']};")
        outer.addWidget(self.problem_label)

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

        self._show_regions()
        self._show_horizon()
        self._start()

    # -- what the operator chose -------------------------------------------

    def horizon(self) -> int | None:
        return self.picker.horizon()

    def regions(self) -> list[tuple[int, int, int, int]]:
        return self.picker.regions()

    def horizon_text(self) -> str:
        return self.horizon_label.text()

    def size_text(self) -> str:
        return self.size_label.text()

    def problem_text(self) -> str:
        return self.problem_label.text()

    def busy(self) -> bool:
        """Whether a fetch is still running. False once this dialog is closed."""
        return self._pool.activeThreadCount() > 0

    def words_on_screen(self) -> list[str]:
        """Every word this dialog puts in front of the operator."""
        return [
            self.windowTitle(),
            self.instructions.text(),
            self.horizon_label.text(),
            self.clear_horizon_button.text(),
            self.regions_label.text(),
            self.remove_button.text(),
            self.size_label.text(),
            self.cancel_button.text(),
            self.use_button.text(),
        ] + [self.regions_list.item(i).text() for i in range(self.regions_list.count())]

    # -- fetching -----------------------------------------------------------

    def _start(self) -> None:
        self.problem_label.setText("")
        self.size_label.setText("Getting a picture from the camera...")
        self._pool.start(_FrameJob(self._grab, self._signals))

    def _fetched(self, result) -> None:
        """A picture, or the sentence saying why there is not one.

        A result that arrives after the dialog has gone is dropped: the operator
        has closed it, and quietly filling in a preview behind them is the one
        thing worse than no preview.
        """
        if self._closed:
            return
        if not isinstance(result, (bytes, bytearray)):
            self._failed(str(result))
            return
        image = QImage()
        if not image.loadFromData(bytes(result)) or image.isNull():
            # Anything but a picture, including an empty file and an HTML error
            # page. Showing a blank rectangle would be worse than showing
            # nothing, because a line dragged onto it would be believed.
            self._failed(
                "What came back from the camera was not a picture, so there is "
                "nothing to draw on. Type the numbers in instead."
            )
            return
        self.picker.set_frame(image)
        self.size_label.setText(
            f"This picture is {image.width()} dots across and {image.height()} "
            f"dots down. The sky line is counted from the top."
        )
        self._show_horizon()

    def _failed(self, text: str) -> None:
        self.problem_label.setText(text)
        self.size_label.setText(
            "There is no picture, so the boxes in the settings are the way to "
            "set these. Nothing you had is changed."
        )

    # -- the drawing --------------------------------------------------------

    def _horizon_picked(self, y: int) -> None:
        self._show_horizon()

    def _region_drawn(self, region) -> None:
        self._show_regions()

    def _clear_horizon(self) -> None:
        self.picker.set_horizon(None)
        self._show_horizon()

    def _remove_region(self) -> None:
        row = self.regions_list.currentRow()
        if row < 0:
            return
        regions = self.picker.regions()
        del regions[row]
        self.picker.set_regions(regions)
        self.picker.select_region(-1)
        self._show_regions()

    def _show_horizon(self) -> None:
        value = self.picker.horizon()
        if value is None:
            self.horizon_label.setText("No sky line. Click the picture to put one.")
        else:
            self.horizon_label.setText(f"Sky line: {value} dots from the top.")

    def _show_regions(self) -> None:
        self.regions_list.clear()
        for x, y, w, h in self.picker.regions():
            self.regions_list.addItem(f"{w} x {h} dots, at {x} across and {y} down")

    # -- closing ------------------------------------------------------------

    def done(self, result: int) -> None:  # noqa: D102 - Qt's own
        """Close, and take the fetch with it.

        Every wait here is bounded on its own: the fetch itself gives up on the
        camera after GRAB_SECONDS, and this waits at most CLOSE_WAIT_MS for it.
        A fetch that outlives even that emits into signals nothing is connected
        to any more, which is why it is disconnected before the wait rather than
        after it.
        """
        self._closed = True
        try:
            self._signals.done.disconnect(self._fetched)
        except (RuntimeError, TypeError):  # already disconnected
            pass
        if not self._pool.waitForDone(CLOSE_WAIT_MS):
            logger.warning("the picture fetch is still running; it will be ignored")
        super().done(result)
