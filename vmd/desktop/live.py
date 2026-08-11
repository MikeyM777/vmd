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

import datetime
import logging
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.disk import StoragePanel
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

# How many rows of movement the side column shows. The list is a glance, not an
# archive - the archive is Playback, where the same events are marks on the day.
RECENT_LIMIT = 20

# How many restarts of one stream are reported in full before the console
# starts saying it once in a while instead. The same shape as the supervisor's
# rule, and for the same reason: a stream that will never come back is retried
# for as long as the console is open, and the Logs tab holds five hundred lines.
FAILURES_SPELLED_OUT = 3
FAILURES_BETWEEN_REMINDERS = 100

# Why the confidence column is sometimes empty, said where the operator can read
# it. Without this line a blank cell reads as "the detector was not sure", which
# is the opposite of the truth: the movement is confirmed, and only its name is
# missing. At 700 m a person is about 13 pixels and no classifier will name it.
UNIDENTIFIED_NOTE = (
    "A blank means unidentified, not uncertain: something moved and was "
    "confirmed, but was too small or too dark to name."
)


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
    """Video wall, steering, and what moved.

    `make_pane`, `local_url` and `events` are injected so the whole tab can be
    tested with fakes: one needs a display and a stream, one needs a running
    server, and the third needs a database the detector process writes.

    `events` is anything with `recent(limit)` - in the console it is an
    `EventStore` over events.db. None means no detection is being read, which is
    what a console started with --no-services has, and it must cost nothing but
    the list.
    """

    def __init__(
        self,
        ptz,
        make_pane: Callable[[str], VideoPane],
        local_url: Callable[[str], str | None],
        events=None,
        storage=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ptz = ptz
        self._make_pane = make_pane
        self._local_url = local_url
        self._events = events
        # A DiskWatcher, or None for a console started with --no-services, which
        # has no folder to watch. It must cost the storage lines and nothing
        # else - the pictures and the steering are not downstream of the disk.
        self._storage = storage
        self._panes: dict[str, VideoPane] = {}
        self._frames: dict[str, QFrame] = {}
        self._status: dict[str, str] = {}
        self._labels: dict[str, QLabel] = {}
        # How many times each stream has been restarted since it last played.
        # One int per stream, cleared when the streams change.
        self._restarts: dict[str, int] = {}
        self._alarm_stream: str | None = None
        # Which events have already been accounted for, rather than the highest
        # id among them. None, not an empty set: the first read establishes what
        # was already there rather than alarming about it. The detector outlives
        # the window, so opening the console on a Thursday must not blare about
        # Tuesday - and the list still shows Tuesday, because it happened.
        #
        # A set and not a high-water mark, because the ids do not arrive in
        # order and do not always increase. `recent()` sorts by the time the
        # movement happened, and the laptop's clock is set by hand: wind it back
        # a minute and the next event is no longer the first row, so a
        # high-water mark stops moving and no movement is ever announced again.
        # Rebuild the database - a replaced disk, a repair after corruption -
        # and the ids start at 1 again, which a high-water mark reads as
        # nothing new for ever. Bounded by RECENT_LIMIT, so it cannot grow.
        self._seen_ids: frozenset[int] | None = None
        self._listed: tuple = ()
        # How many times the table has actually been rebuilt. refresh() runs
        # every two seconds for months; this is the number that says whether it
        # is doing work for nothing.
        self.rebuilds = 0
        self._held: set[str] = set()
        self._fine = False
        self._zoom = 0.0
        # Starts at rest rather than unknown, so that losing focus before
        # anything has moved does not put a needless stop onto the link.
        self._last_velocity: tuple[float, float, float] | None = (0.0, 0.0, 0.0)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        outer.addWidget(self._build_alarm_strip())

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addLayout(layout, 1)

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
        side.setFixedWidth(340)
        self._side_layout = QVBoxLayout(side)
        self._moving = QLabel("idle")
        self._ptz_note = QLabel("")
        self._ptz_note.setWordWrap(True)

        self._streams_box = QGroupBox("Streams")
        self._streams_layout = QVBoxLayout(self._streams_box)
        self._side_layout.addWidget(self._streams_box)
        # Storage sits above the movement list, as the design's column order has
        # it. The panel is built here rather than injected so the tab owns its
        # own column; what it reads is injected, because reading it touches the
        # filesystem and that must never happen on this thread.
        self._storage_panel = StoragePanel(storage) if storage is not None else None
        if self._storage_panel is not None:
            self._side_layout.addWidget(self._storage_panel)
        self._side_layout.addWidget(self._build_movement_box(), 1)

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

    # ------------------------------------------------------------ what moved

    def _build_alarm_strip(self) -> QWidget:
        """The strip across the top. Hidden until something moves.

        Hidden rather than empty: a strip that is always there is furniture, and
        furniture is not noticed. The operator is watching the pictures, not
        this.
        """
        self._alarm = QFrame()
        self._alarm.setStyleSheet(
            f"background: {PALETTE['alarm']}; color: {PALETTE['bg']};"
        )
        row = QHBoxLayout(self._alarm)
        row.setContentsMargins(12, 8, 12, 8)
        self._alarm_label = QLabel("")
        self._alarm_label.setWordWrap(True)
        self._alarm_label.setStyleSheet(f"background: transparent; color: {PALETTE['bg']};")
        row.addWidget(self._alarm_label, 1)
        acknowledge = QPushButton("Acknowledge")
        acknowledge.clicked.connect(self.acknowledge)
        row.addWidget(acknowledge)
        self._alarm.setVisible(False)
        return self._alarm

    def _build_movement_box(self) -> QWidget:
        box = QGroupBox("Recent movement")
        layout = QVBoxLayout(box)
        self._movement = QTableWidget(0, 4)
        self._movement.setHorizontalHeaderLabels(["Time", "Stream", "What", "Confidence"])
        self._movement.verticalHeader().setVisible(False)
        self._movement.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._movement.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._movement.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self._movement, 1)
        self._movement_note = QLabel(UNIDENTIFIED_NOTE)
        self._movement_note.setWordWrap(True)
        self._movement_note.setStyleSheet(f"color: {PALETTE['muted']};")
        layout.addWidget(self._movement_note)
        return box

    def _refresh_events(self) -> None:
        """Read the movement list, raise the alarm on anything new.

        A store that cannot be read costs detection and nothing else: the
        pictures, the steering and the panes are not downstream of it, and an
        operator who loses the movement list must not lose the camera with it.
        """
        if self._events is None:
            return
        try:
            events = list(self._events.recent(RECENT_LIMIT))
        except Exception:  # noqa: BLE001 - the camera does not depend on this
            logger.exception("the movement list could not be read")
            return

        ids = frozenset(event.id for event in events)
        if self._seen_ids is None:
            self._seen_ids = ids
        else:
            # Anything in the list that was not in it last time. Retention
            # deleting an event is not one of those, so footage being reclaimed
            # cannot announce itself as movement.
            fresh = [event for event in events if event.id not in self._seen_ids]
            self._seen_ids = ids
            if fresh:
                self._raise_alarm(max(fresh, key=lambda event: event.id))

        # Only redraw when the list actually changed. This runs every two
        # seconds for months; the id alone is not enough of a signature, because
        # retention deletes from the *old* end and leaves the newest id exactly
        # where it was.
        signature = tuple((e.id, e.label, e.confidence) for e in events)
        if signature == self._listed:
            return
        self._listed = signature
        self._fill_movement(events)

    def _fill_movement(self, events) -> None:
        self.rebuilds += 1
        self._movement.setRowCount(len(events))
        for row, event in enumerate(events):
            named = bool(event.label)
            cells = [
                datetime.datetime.fromtimestamp(event.started).strftime("%H:%M:%S"),
                event.stream,
                # Blank, not "unknown" and never "0%". An unnamed event is a
                # confirmed one: something moved. A number in this cell would
                # read as "the detector saw nothing", which is a lie about the
                # only thing this system exists to report.
                event.label if named else "",
                f"{event.confidence * 100:.0f}%" if named else "",
            ]
            for column, text in enumerate(cells):
                self._movement.setItem(row, column, QTableWidgetItem(text))

    def _raise_alarm(self, event) -> None:
        clock = datetime.datetime.fromtimestamp(event.started).strftime("%H:%M:%S")
        self._alarm_label.setText(f"Movement on {event.stream} at {clock}")
        self._alarm.setVisible(True)
        self._outline(event.stream)

    def acknowledge(self) -> None:
        """The operator has seen it. Clear the strip and the outline."""
        self._alarm.setVisible(False)
        self._alarm_label.setText("")
        self._outline(None)

    def _outline(self, stream: str | None) -> None:
        self._alarm_stream = stream
        for name, frame in self._frames.items():
            frame.setStyleSheet(
                f"border: 3px solid {PALETTE['alarm']};" if name == stream else ""
            )

    # -- what the tests and the window read ---------------------------------

    def alarm_visible(self) -> bool:
        # isVisibleTo, not isVisible: a widget inside a window nobody has shown
        # yet is not visible, and the strip's own state is what is being asked
        # about.
        return self._alarm.isVisibleTo(self)

    def alarm_text(self) -> str:
        return self._alarm_label.text()

    def alarm_style(self) -> str:
        return self._alarm.styleSheet()

    def outlined_stream(self) -> str | None:
        return self._alarm_stream

    def pane_outline_style(self, name: str) -> str:
        frame = self._frames.get(name)
        return frame.styleSheet() if frame is not None else ""

    def storage_lines(self) -> list[tuple[str, str]]:
        """What the storage panel is saying, or nothing when there is no panel."""
        if self._storage_panel is None:
            return []
        return self._storage_panel.lines()

    def movement_note(self) -> str:
        return self._movement_note.text()

    def recent_rows(self) -> list[tuple[str, str, str, str]]:
        rows: list[tuple[str, str, str, str]] = []
        for row in range(self._movement.rowCount()):
            cells = []
            for column in range(self._movement.columnCount()):
                item = self._movement.item(row, column)
                cells.append(item.text() if item is not None else "")
            rows.append(tuple(cells))  # type: ignore[arg-type]
        return rows

    # ---------------------------------------------------------------- streams

    def apply(self, settings: Settings) -> None:
        """Build a pane for every enabled stream, replacing whatever was there."""
        for pane in self._panes.values():
            pane.stop()
            # Stopped is not finished. A libVLC pane holds a player, its decoder
            # threads and an instance that nothing frees when the object is
            # dropped, and this runs again every time the operator saves the
            # settings. `release` is not part of the VideoPane protocol, so a
            # pane without one is simply dropped.
            release = getattr(pane, "release", None)
            if release is not None:
                try:
                    release()
                except Exception:  # noqa: BLE001 - a leak beats losing the tab
                    logger.exception("a video pane would not let go of libVLC")
            if isinstance(pane, QWidget):
                pane.setParent(None)
        self._panes.clear()
        self._status.clear()
        self._restarts.clear()
        for frame in self._frames.values():
            frame.setParent(None)
        self._frames.clear()
        for label in self._labels.values():
            self._streams_layout.removeWidget(label)
            label.setParent(None)
        self._labels.clear()

        for stream in settings.camera.streams:
            if not (stream.enabled and stream.url):
                continue
            pane = self._make_pane(stream.name)
            self._panes[stream.name] = pane
            # Each pane sits in a frame of its own so that an event can outline
            # the stream it was seen on. The pane itself cannot carry the
            # outline: a VideoPane is only required to show, stop and report a
            # state, and libVLC draws over anything the widget paints anyway.
            frame = QFrame()
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(3, 3, 3, 3)
            if isinstance(pane, QWidget):
                frame_layout.addWidget(pane)
            self._frames[stream.name] = frame
            self._wall.addWidget(frame)
            label = QLabel()
            self._labels[stream.name] = label
            self._streams_layout.addWidget(label)
            url = self._local_url(stream.name)
            if url:
                pane.show(url)
                self._set_status(stream.name, pane.state)
            else:
                self._set_status(stream.name, "stopped")
        # An alarm raised before the streams changed is still unacknowledged.
        self._outline(self._alarm_stream)
        self.overlay.raise_()
        # A saved budget or a saved folder changes what the storage lines say
        # about the reading already taken, so redraw them now rather than at the
        # next heartbeat.
        if self._storage_panel is not None:
            self._storage_panel.refresh()

    def refresh(self) -> None:
        """Read every pane's state. Restart only what has actually failed.

        Late is a report, not a trigger. The pane is left exactly as it is."""
        for name, pane in self._panes.items():
            state = pane.state
            self._set_status(name, state)
            if state == "failed":
                url = self._local_url(name)
                if url:
                    self._say_it_failed(name)
                    pane.show(url)
            elif state == "playing":
                # Actually recovered, rather than merely on its way somewhere.
                # A stream that flaps between failed and connecting has not.
                self._restarts.pop(name, None)
        self._refresh_events()
        if self._storage_panel is not None:
            self._storage_panel.refresh()

    def _say_it_failed(self, name: str) -> None:
        """Report a restart, without reporting the same one every two seconds.

        A camera that is off, or an address that is wrong, fails on every tick
        for as long as the console is open. Unthrottled that is thirty lines a
        minute, and the ring the Logs tab reads holds five hundred: within
        twenty minutes the only thing in it is this line, and go2rtc's "401
        Unauthorized" - the line that says *why* - has been pushed out of the
        one place the operator can read it. The supervisor already learned
        this; the panes had not.
        """
        count = self._restarts.get(name, 0) + 1
        self._restarts[name] = count
        if count <= FAILURES_SPELLED_OUT:
            logger.warning("%s failed; restarting it", name)
        elif count % FAILURES_BETWEEN_REMINDERS == 0:
            logger.warning("%s has failed and been restarted %d times", name, count)

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

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The same rule, for the tab going away rather than the focus.

        `focusOutEvent` only fires when this widget is the one holding focus. An
        arrow key still steers when the focus is on a child - the movement list,
        the Acknowledge button - because the key event travels up to this tab
        unhandled. Switching to Settings then hides the tab without ever taking
        focus off that child, so no focusOut arrives, and neither does the key
        release: the head slews until it reaches its stop, with the operator
        looking at a different tab. Hiding covers the tab switch, the minimise
        and the close alike.
        """
        self.stop_steering()
        super().hideEvent(event)
