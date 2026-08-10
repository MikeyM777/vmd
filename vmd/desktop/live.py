"""The Live tab: the pictures, and the controls that move the camera.

Two things here are not preferences, they are the consequences of failures in
the field:

* The panes read from the local streaming server, never from the camera. One
  connection crosses the radio link - go2rtc's - and everything else on this
  machine reads go2rtc's copy. A second connection to the camera would double
  the load on a link that barely carries one.

* A late stream is reported and left alone. Only a failed stream is restarted.
  The browser version recovered on a timer, and the timer fired early often
  enough that it caused the disconnections it existed to repair.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.steering import edge_velocity, key_velocity
from vmd.desktop.style import PALETTE
from vmd.desktop.video import VideoPane
from vmd.settings import Settings

logger = logging.getLogger(__name__)

ZOOM_SPEED = 0.5

# What each pane state is called on screen, and what colour it is. A late
# stream must not look like a playing one: it is the state an operator has to
# notice, and it is the one the console deliberately does nothing about.
STATE_WORDS: dict[str, str] = {
    "stopped": "stopped",
    "connecting": "connecting",
    "playing": "playing",
    "late": "late - no new pictures",
    "failed": "failed",
}
STATE_COLOURS: dict[str, str] = {
    "stopped": PALETTE["muted"],
    "connecting": PALETTE["muted"],
    "playing": PALETTE["ok"],
    "late": PALETTE["warn"],
    "failed": PALETTE["alarm"],
}

# Arrow keys to the names steering.py uses.
ARROWS: dict[int, str] = {
    int(Qt.Key.Key_Left): "left",
    int(Qt.Key.Key_Right): "right",
    int(Qt.Key.Key_Up): "up",
    int(Qt.Key.Key_Down): "down",
}
ZOOM_IN_KEYS = {int(Qt.Key.Key_Plus), int(Qt.Key.Key_Equal)}
ZOOM_OUT_KEYS = {int(Qt.Key.Key_Minus), int(Qt.Key.Key_Underscore)}


class SteeringOverlay(QWidget):
    """A transparent sheet over the video wall that steers on drag.

    It carries no picture of its own; libVLC draws underneath it. Pressing near
    an edge slews towards that edge, faster the closer to the edge you are, and
    letting go stops. It tracks the button itself rather than reading
    `event.buttons()` so that a move event delivered without button state - as
    Qt does for synthetic moves - cannot be mistaken for a drag.
    """

    def __init__(self, tab: "LiveTab", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tab = tab
        self._pressed = False
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

    def _fraction(self, event: QMouseEvent) -> tuple[float, float]:
        position = event.position()
        width = max(self.width(), 1)
        height = max(self.height(), 1)
        return (position.x() / width, position.y() / height)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pressed = True
        x, y = self._fraction(event)
        self._tab.pointer_at(x, y, True)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._pressed:
            event.ignore()
            return
        x, y = self._fraction(event)
        self._tab.pointer_at(x, y, True)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._pressed = False
        x, y = self._fraction(event)
        self._tab.pointer_at(x, y, False)
        event.accept()

    def leaveEvent(self, event: QEvent) -> None:
        """The pointer left the picture. Whatever it was steering, stop: a slew
        that outlives the gesture that started it is how a head ends up against
        its stop."""
        if self._pressed:
            self._pressed = False
            self._tab.pointer_at(0.5, 0.5, False)
        super().leaveEvent(event)


class LiveTab(QWidget):
    """Video wall plus steering.

    `make_pane` and `local_url` are injected so the whole tab can be tested with
    fakes: one needs a display and a stream, the other needs a running server.
    """

    def __init__(
        self,
        ptz,
        make_pane: Callable[[str], VideoPane],
        local_url: Callable[[str], str | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ptz = ptz
        self._make_pane = make_pane
        self._local_url = local_url
        self._panes: dict[str, VideoPane] = {}
        self._status: dict[str, str] = {}
        self._labels: dict[str, QLabel] = {}
        self._held: set[str] = set()
        self._fine = False
        self._zoom = 0.0
        # Starts at rest rather than unknown, so that losing focus before
        # anything has moved does not put a needless stop onto the link.
        self._last_velocity: tuple[float, float, float] | None = (0.0, 0.0, 0.0)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # The splitter cannot hold the overlay: it turns every child widget into
        # a pane. So the wall lives in a plain container, and the overlay is a
        # sibling of the splitter kept at the container's full size.
        self._wall_area = QWidget()
        wall_layout = QVBoxLayout(self._wall_area)
        wall_layout.setContentsMargins(0, 0, 0, 0)
        self._wall = QSplitter(Qt.Orientation.Horizontal)
        wall_layout.addWidget(self._wall)
        self.overlay = SteeringOverlay(self, self._wall_area)
        self.overlay.setGeometry(self._wall_area.rect())
        self.overlay.raise_()
        self._wall_area.installEventFilter(self)
        layout.addWidget(self._wall_area, 1)

        side = QWidget()
        side.setFixedWidth(292)
        self._side_layout = QVBoxLayout(side)
        self._moving = QLabel("idle")
        self._ptz_note = QLabel("")
        self._ptz_note.setWordWrap(True)

        self._streams_box = QGroupBox("Streams")
        self._streams_layout = QVBoxLayout(self._streams_box)
        self._side_layout.addWidget(self._streams_box)

        steering_box = QGroupBox("Steering")
        steering_layout = QVBoxLayout(steering_box)
        steering_layout.addWidget(QLabel("Arrow keys pan and tilt. Shift for fine."))
        steering_layout.addWidget(QLabel("+ and - zoom. Home recentres."))
        steering_layout.addWidget(QLabel("Drag near an edge of the picture to slew."))
        steering_layout.addWidget(self._moving)
        steering_layout.addWidget(self._ptz_note)
        self._side_layout.addWidget(steering_box)
        self._side_layout.addStretch(1)
        layout.addWidget(side)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._wall_area and event.type() == QEvent.Type.Resize:
            self.overlay.setGeometry(self._wall_area.rect())
            self.overlay.raise_()
        return super().eventFilter(watched, event)

    # ---------------------------------------------------------------- streams

    def apply(self, settings: Settings) -> None:
        """Build a pane for every enabled stream, replacing whatever was there."""
        for pane in self._panes.values():
            pane.stop()
            if isinstance(pane, QWidget):
                pane.setParent(None)
        self._panes.clear()
        self._status.clear()
        for label in self._labels.values():
            self._streams_layout.removeWidget(label)
            label.setParent(None)
        self._labels.clear()

        for stream in settings.camera.streams:
            if not (stream.enabled and stream.url):
                continue
            pane = self._make_pane(stream.name)
            self._panes[stream.name] = pane
            if isinstance(pane, QWidget):
                self._wall.addWidget(pane)
            label = QLabel()
            self._labels[stream.name] = label
            self._streams_layout.addWidget(label)
            url = self._local_url(stream.name)
            if url:
                pane.show(url)
                self._set_status(stream.name, pane.state)
            else:
                self._set_status(stream.name, "stopped")
        self.overlay.raise_()

    def refresh(self) -> None:
        """Read every pane's state. Restart only what has actually failed.

        Late is a report, not a trigger. The pane is left exactly as it is."""
        for name, pane in self._panes.items():
            state = pane.state
            self._set_status(name, state)
            if state == "failed":
                url = self._local_url(name)
                if url:
                    logger.warning("%s failed; restarting it", name)
                    pane.show(url)

    def stream_names(self) -> list[str]:
        return list(self._panes)

    def stream_status_text(self, name: str) -> str:
        return self._status.get(name, "stopped")

    def stream_label_text(self, name: str) -> str:
        label = self._labels.get(name)
        return label.text() if label is not None else ""

    def stream_label_style(self, name: str) -> str:
        label = self._labels.get(name)
        return label.styleSheet() if label is not None else ""

    def _set_status(self, name: str, state: str) -> None:
        self._status[name] = state
        label = self._labels.get(name)
        if label is None:
            return
        label.setText(f"{name}  -  {STATE_WORDS.get(state, state)}")
        label.setStyleSheet(f"color: {STATE_COLOURS.get(state, PALETTE['muted'])};")

    # --------------------------------------------------------------- steering

    def key_down(self, key: str, fine: bool) -> None:
        self._held.add(key)
        self._fine = fine
        self._steer()

    def key_up(self, key: str) -> None:
        self._held.discard(key)
        self._fine = False
        self._steer()

    def pointer_at(self, x: float, y: float, pressed: bool) -> None:
        if not pressed:
            # Releasing the pointer ends the pointer's contribution only. If a
            # key is still held the head keeps going, which is what the operator
            # asked for; otherwise this is a stop.
            self._steer()
            return
        pan, tilt = edge_velocity(x, y)
        self._drive(pan, tilt, self._zoom)

    def zoom(self, direction: int) -> None:
        self._zoom = ZOOM_SPEED * direction
        self._steer()

    def go_home(self) -> None:
        self._held.clear()
        self._zoom = 0.0
        self._last_velocity = None
        self._moving.setText("home")
        self._report(self._ptz.home())

    def stop_steering(self) -> None:
        """Forget everything held and bring the head to rest."""
        self._held.clear()
        self._fine = False
        self._zoom = 0.0
        self._steer()

    def _steer(self) -> None:
        pan, tilt = key_velocity(self._held, self._fine)
        self._drive(pan, tilt, self._zoom)

    def _drive(self, pan: float, tilt: float, zoom: float) -> None:
        """Send a velocity, or a stop. Repeats are dropped: a held key produces
        a stream of identical events and every one of them would otherwise be a
        request across the link."""
        velocity = (pan, tilt, zoom)
        if velocity == self._last_velocity:
            return
        self._last_velocity = velocity

        if pan == 0.0 and tilt == 0.0 and zoom == 0.0:
            result = self._ptz.stop()
            self._moving.setText("idle")
        else:
            result = self._ptz.move(pan, tilt, zoom)
            self._moving.setText(f"pan {pan:+.2f}  tilt {tilt:+.2f}  zoom {zoom:+.2f}")
        self._report(result)

    def _report(self, result) -> None:
        if isinstance(result, dict) and result.get("ok") is False:
            self._ptz_note.setText(result.get("error", "the camera refused the command"))
        else:
            self._ptz_note.setText("")

    # --------------------------------------------------------- keyboard, focus

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Windows sends a press *and a release* for every auto-repeat while a
        # key is simply held down. Acting on the release would stutter the head;
        # acting on the press would put one request per repeat onto the link.
        if event.isAutoRepeat():
            event.accept()
            return
        key = int(event.key())
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key in ARROWS:
            self.key_down(ARROWS[key], fine)
        elif key in ZOOM_IN_KEYS:
            self.zoom(1)
        elif key in ZOOM_OUT_KEYS:
            self.zoom(-1)
        elif key == int(Qt.Key.Key_Home):
            self.go_home()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat():
            event.accept()
            return
        key = int(event.key())
        if key in ARROWS:
            self.key_up(ARROWS[key])
        elif key in ZOOM_IN_KEYS or key in ZOOM_OUT_KEYS:
            self.zoom(0)
        else:
            super().keyReleaseEvent(event)
            return
        event.accept()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        """Nothing will deliver the release of a key held when the window went
        away, so the console must not be holding one either: forget them all and
        stop. A head left slewing because another window took focus is a hazard,
        not an inconvenience."""
        self.stop_steering()
        super().focusOutEvent(event)
