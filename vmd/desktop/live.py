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
import time
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.disk import StoragePanel
from vmd.desktop.steering import edge_velocity, key_velocity
from vmd.desktop.style import (
    MONO,
    PALETTE,
    SIZE_BAND,
    SIZE_BODY,
    SIZE_HEADING,
    SIZE_SMALL,
    SPACE_GROUP,
    SPACE_ROOM,
    SPACE_SNUG,
    SPACE_STEP,
    SPACE_TIGHT,
    WEIGHT_VALUE,
)
from vmd.desktop.video import VideoPane
from vmd.ptz.service import UNANSWERED_AFTER, PtzCommands
from vmd.radio.panel import LinkPanel
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

# The number keys that choose which view fills the wall: 1 is everything, 2 is
# the first stream, 3 the second, and so on for as many as the camera has.
#
# Digits and nothing else, because every other key on this tab is already
# steering: the arrows pan and tilt, + and - zoom, Home recentres, and Shift is
# the fine modifier. A letter would collide the first time somebody adds one.
# They are read in `keyPressEvent` beside the steering keys rather than as Qt
# shortcuts, so that pressing one while an arrow is held cannot swallow the
# release of that arrow - a swallowed release is a head that goes on slewing
# with nobody watching, which is the one failure this tab may never have.
VIEW_KEYS: dict[int, int] = {
    int(getattr(Qt.Key, f"Key_{digit}")): digit - 1 for digit in range(1, 10)
}

# What the button showing every view at once is called.
ALL_VIEWS = "All"

# What the wall says when the camera has no views set up at all. A black
# rectangle is not an answer to "why is there no picture?".
NO_VIEWS_NOTE = "No pictures. Add a camera view in Settings."

# How many rows of movement the side column shows. The list is a glance, not an
# archive - the archive is Playback, where the same events are marks on the day.
RECENT_LIMIT = 20

# How many restarts of one stream are reported in full before the console
# starts saying it once in a while instead. The same shape as the supervisor's
# rule, and for the same reason: a stream that will never come back is retried
# for as long as the console is open, and the Logs tab holds five hundred lines.
FAILURES_SPELLED_OUT = 3
FAILURES_BETWEEN_REMINDERS = 100

# How long the console waits before restarting a stream that has just failed,
# and how far that wait grows: 2 s, 4, 8, 16, 32, then a minute for ever.
#
# The first attempt is immediate, because a stream that dropped once and comes
# straight back is the common case and waiting on it would cost the picture for
# nothing. What the growth is for is the other case: a camera that is off, or an
# address that is wrong, fails on every tick for as long as the console is open.
# Restarting it thirty times a minute is this module's own lesson - recovery
# code firing too early - one level up from the pane, and it wrote 40 lines into
# the Logs tab in 18 seconds, which evicts everything else from the 500-line
# ring inside four minutes.
RESTART_FIRST_DELAY = 2.0
RESTART_BACKOFF_MAX = 60.0

# How many heartbeats a pane has to keep saying "playing" before its place on
# that ladder is forgiven.
#
# It was one, and one is the flapping case: failed, playing, failed, which is
# exactly what a stream on a marginal radio link does. A single good reading
# reset the ladder to the bottom rung, so a flapping stream never climbed it at
# all and was restarted every two seconds for as long as the console was open.
# The restarts were not the damage. The damage was the log: `%s failed;
# restarting it` at that rate evicts the 500-line ring the operator reads, and
# that is how go2rtc's "401 Unauthorized" - the line that says WHY - was lost
# while the fault was being hunted.
#
# Five, and the heartbeat is two seconds, so ten seconds of continuous pictures.
# The number is chosen against the pane's own idea of "late": a stream is called
# late after eight seconds without a frame, so ten seconds of `playing` is a
# stream that has been delivering pictures for longer than the console's own
# patience with one that has stopped. Anything shorter is a gap between two
# failures rather than a recovery.
PLAYING_BEFORE_FORGIVEN = 5

# After this many failures in a row the console stops implying it is about to
# fix this. It keeps trying, slowly, because a camera that is switched back on
# must come back without anyone restarting the console - but it stops saying
# "failed" as though the next attempt were the one, and points at the place the
# operator can actually do something.
GIVING_UP_AFTER = 6
GIVEN_UP_WORDS = "failed - not coming back on its own; check the address in Settings"

# Why the confidence column is sometimes empty, said where the operator can read
# it. Without this line a blank cell reads as "the detector was not sure", which
# is the opposite of the truth: the movement is confirmed, and only its name is
# missing. At 700 m a person is about 13 pixels and no classifier will name it.
UNIDENTIFIED_NOTE = (
    "A blank means unidentified, not uncertain: something moved and was "
    "confirmed, but was too small or too dark to name."
)

# What the steering column says while the camera is sitting on a command. Never
# a blank: a blank in this box means "the camera did as it was told", and saying
# that about a command nobody has answered is the kind of quiet lie that has an
# operator believing the head moved when it did not.
UNANSWERED_NOTE = "the camera did not answer the last command yet"

# What the movement list says before anything has moved. An empty table is a
# black rectangle with a header on it, and a black rectangle is not an answer to
# "has anything happened?" - the operator cannot tell it apart from a list that
# failed to load. The words say which of the two it is.
NOTHING_YET = "Nothing has moved yet."

# How wide the side column is, as a share of the tab and between two stops.
#
# It was 340 px on every screen, which is a fifth of a 1366 laptop panel and a
# twelfth of a 4K one. The sentences in it are word-wrapped, so a column that
# does not grow with the window means the same paragraph is four lines on the
# laptop and four lines on the 4K screen with a third of the width wasted beside
# it. The floor is the width the longest storage and link sentences were written
# against; the ceiling is where a wrapped sentence stops being a column and
# starts being a page.
SIDE_MIN_WIDTH = 330
SIDE_MAX_WIDTH = 420
SIDE_FRACTION = 0.22


class WrappedNote(QLabel):
    """A sentence that asks for the height its text really needs.

    A word-wrapped QLabel asks for the height of ONE line unless its size policy
    says its height depends on its width, and a layout short of room believes it:
    the second line is drawn over the line beneath, and neither can be read. The
    two sentences this is for are the two in the side column that do not fit in
    one line of 340 px - the camera not having answered the last command, and why
    a confidence cell is blank. The first is two lines and the second is four.

    Both are whole on screen today, and for a reason that is not their own doing:
    the column is a QScrollArea, and a scroll area asks its widget's layout how
    tall it is at the width it has rather than trusting the width-independent
    minimum. Take that away - a column that stops scrolling, one of these moved
    into a layout that believes what it is told - and each says it can live in
    one line. Squeezed to that, the camera note was measured at 16 px of the 42
    its two lines need.

    The link and storage panels below it fit their labels from the panel's own
    resize, because there the labels are the panel's own children and the panel
    knows the width they will get. These two sit inside boxes the tab does not
    lay out itself, so each fits itself the moment it is given a width: the same
    measurement, taken where the width is known rather than guessed at.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self.setSizePolicy(policy)

    def fit(self) -> None:
        """Ask for the height this text needs at the width there is."""
        needed = self.heightForWidth(max(self.width(), 1)) if self.text() else 0
        # Only when it changes: setting it invalidates the layout, and a layout
        # invalidated on every pass of itself does not settle.
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self.fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.fit()


class ViewChooser(QWidget):
    """Which view fills the wall: everything, or one of them alone.

    DESIGN.md's segmented control - flush buttons in a bordered group, the
    active one raised and heavier. It is a thing the operator changes all day
    from two metres back, so which one is active has to be readable without
    reading: the chosen button is the only one drawn in the ink colour on the
    raised surface, and the rest are muted on the panel.

    Every button refuses focus. Clicking one must not take the keyboard away
    from the tab, because the tab is what steers the camera - and a button that
    took focus would leave the operator's next arrow key going nowhere until
    they clicked back on the picture.
    """

    chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)
        self._buttons: list[QPushButton] = []
        self._views: list[str] = [""]
        self._chosen = ""
        self._row.addStretch(1)
        self.set_views([])

    def views(self) -> list[str]:
        """Every view this control offers, in order. "" is all of them."""
        return list(self._views)

    def chosen_view(self) -> str:
        return self._chosen

    def labels(self) -> list[str]:
        return [button.text() for button in self._buttons]

    def set_views(self, names: list[str]) -> None:
        """Offer one button per stream, plus the one that shows them all.

        The buttons come from the streams that are actually configured, not
        from a fixed list of two: a camera calls its views whatever it likes,
        and a console offering "visible only" on a machine with no visible
        stream would be offering a black rectangle.
        """
        self._views = [""] + list(names)
        while len(self._buttons) < len(self._views):
            button = QPushButton()
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            index = len(self._buttons)
            button.clicked.connect(lambda _checked=False, i=index: self._pick(i))
            self._row.insertWidget(index, button)
            self._buttons.append(button)
        for index, button in enumerate(self._buttons):
            if index < len(self._views):
                name = self._views[index]
                button.setText(ALL_VIEWS if not name else name)
                button.setVisible(True)
            else:
                button.setVisible(False)
        if self._chosen not in self._views:
            self._chosen = ""
        self._draw()

    def choose(self, view: str, announce: bool = True) -> None:
        """Show this view. An unknown one falls back to showing everything."""
        if view not in self._views:
            view = ""
        changed = view != self._chosen
        self._chosen = view
        self._draw()
        if changed and announce:
            self.chosen.emit(view)

    def choose_at(self, index: int) -> bool:
        """Pick by position, for the number keys. False if there is no such
        view, so that a key nobody has a stream for does nothing at all."""
        if not 0 <= index < len(self._views):
            return False
        self.choose(self._views[index])
        return True

    def _pick(self, index: int) -> None:
        self.choose_at(index)

    def _draw(self) -> None:
        for index, button in enumerate(self._buttons):
            active = index < len(self._views) and self._views[index] == self._chosen
            button.setStyleSheet(
                f"QPushButton {{ background: "
                f"{PALETTE['raised'] if active else PALETTE['surface']}; "
                f"color: {PALETTE['ink'] if active else PALETTE['muted']}; "
                f"border: 1px solid {PALETTE['line']}; "
                # The chosen one carries a bar in the accent, the same mark the
                # tab bar uses for the page you are on. One vocabulary for "this
                # is where you are", and DESIGN.md's one permitted amber.
                f"border-bottom: 2px solid "
                f"{PALETTE['accent'] if active else PALETTE['line']}; "
                f"font-size: {SIZE_BODY}px; "
                f"font-weight: {WEIGHT_VALUE if active else 400}; "
                f"padding: {SPACE_SNUG}px {SPACE_GROUP}px; }}"
                f"QPushButton:hover {{ color: {PALETTE['ink']}; }}"
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
    """Video wall, view modes, steering, and what moved.

    `make_pane`, `local_url` and `events` are injected so the whole tab can be
    tested with fakes: one needs a display and a stream, one needs a running
    server, and the third needs a database the detector process writes.

    `events` is anything with `recent(limit)` - in the console it is an
    `EventStore` over events.db. None means no detection is being read, which is
    what a console started with --no-services has, and it must cost nothing but
    the list.
    """

    # The operator chose a different view. The window turns this into a line in
    # settings.json, because the tab does not own that file - and because a
    # choice that does not survive the night is not a choice, it is a chore.
    view_changed = Signal(str)

    def __init__(
        self,
        ptz,
        make_pane: Callable[[str], VideoPane],
        local_url: Callable[[str], str | None],
        events=None,
        storage=None,
        radio=None,
        clock: Callable[[], float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ptz = ptz
        # Every command to the camera goes through here rather than straight out
        # of the key handler. See PtzCommands: the camera is at the far end of a
        # radio link, and a key handler is the one place in this program that
        # must never wait for it.
        self._commands = PtzCommands(ptz)
        self._make_pane = make_pane
        self._local_url = local_url
        self._events = events
        # A DiskWatcher, or None for a console started with --no-services, which
        # has no folder to watch. It must cost the storage lines and nothing
        # else - the pictures and the steering are not downstream of the disk.
        self._storage = storage
        # The RadioService, or None for the same reason. It answers from what it
        # last read and never waits, which is the only reason this may be asked
        # on the same heartbeat that draws the window: an unreachable radio
        # costs about 12 s of login timeouts.
        self._radio = radio
        self._panes: dict[str, VideoPane] = {}
        self._frames: dict[str, QFrame] = {}
        self._status: dict[str, str] = {}
        self._labels: dict[str, QLabel] = {}
        # How many times each stream has been restarted since it last played.
        # One int per stream, cleared when the streams change.
        self._restarts: dict[str, int] = {}
        # And when each may be tried again. Injected clock so a test can wind
        # four hundred seconds of heartbeats past without waiting for any.
        self._next_try: dict[str, float] = {}
        # How many heartbeats in a row each stream has been playing. A stream
        # has to stay up, not merely be up for one reading, before the console
        # forgives its place on the backoff ladder.
        self._playing_for: dict[str, int] = {}
        # The camera address each stream was last built against. A Save that did
        # not touch a stream has no business forgiving that stream's failures.
        self._urls: dict[str, str] = {}
        self._clock = clock or time.monotonic
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
        outer.setContentsMargins(SPACE_STEP, SPACE_STEP, SPACE_STEP, SPACE_STEP)
        outer.setSpacing(SPACE_STEP)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_STEP)
        outer.addLayout(layout, 1)

        # The splitter cannot hold the overlay: it turns every child widget into
        # a pane. So the wall lives in a plain container, and the overlay is a
        # sibling of the splitter kept at the container's full size.
        self._wall_area = QWidget()
        wall_layout = QVBoxLayout(self._wall_area)
        wall_layout.setContentsMargins(0, 0, 0, 0)
        self._wall = QSplitter(Qt.Orientation.Horizontal)
        # A handle wide enough to be grabbed. It was a hairline, which between
        # two near-black pictures is both invisible and unusable.
        self._wall.setHandleWidth(SPACE_SNUG)
        wall_layout.addWidget(self._wall)
        self.overlay = SteeringOverlay(self, self._wall_area)
        self.overlay.setGeometry(self._wall_area.rect())
        self.overlay.raise_()
        self._wall_area.installEventFilter(self)

        # The alarm strip belongs to the pictures, so it lives in their column
        # and not across the whole tab. Below them, which is DESIGN.md's rule -
        # an alarm is the moment the picture matters most, and the notice about
        # it may neither cover the picture nor push it down the screen. Outside
        # the wall area rather than inside it, because the steering overlay
        # covers everything in there and would swallow the Acknowledge button.
        pictures = QVBoxLayout()
        pictures.setContentsMargins(0, 0, 0, 0)
        pictures.setSpacing(SPACE_SNUG)
        self.views = ViewChooser()
        self.views.chosen.connect(self._view_chosen)
        pictures.addWidget(self.views)
        # Shown in place of the wall when the camera has no views set up. A
        # black rectangle with nothing in it is the one thing an operator
        # cannot diagnose.
        self._no_views = WrappedNote(NO_VIEWS_NOTE)
        self._no_views.setStyleSheet(f"color: {PALETTE['muted']};")
        self._no_views.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_views.setVisible(False)
        pictures.addWidget(self._no_views)
        pictures.addWidget(self._wall_area, 1)
        pictures.addWidget(self._build_alarm_strip())
        layout.addLayout(pictures, 1)

        side = QWidget()
        self._side_layout = QVBoxLayout(side)
        self._side_layout.setContentsMargins(0, 0, 0, 0)
        self._side_layout.setSpacing(SPACE_ROOM)
        self._moving = QLabel("idle")
        # What the head is doing is a reading, not a caption: it is the one line
        # in this box that changes, and it is set in the same monospace figures
        # as every other number in the console so that a velocity does not
        # jitter as it counts.
        self._moving.setStyleSheet(
            f"font-family: {MONO}; font-size: {SIZE_BODY}px; "
            f"font-weight: {WEIGHT_VALUE}; color: {PALETTE['ink']};"
        )
        self._ptz_note = WrappedNote("")
        self._ptz_note.setStyleSheet(f"color: {PALETTE['warn']};")

        # Steering, link, storage, recent movement: the column order the design
        # gives. Steering is above the two panels rather than below them because
        # the column scrolls now, and what it holds is not only a list of keys -
        # it is where the camera says it did not answer the last command. That
        # sentence must not be the one below the fold.
        #
        # The keys are one wrapped caption and not three lines of their own,
        # because they are read once and then never again, while the two lines
        # under them - what the head is doing, and whether the camera answered -
        # are read all day. Three permanent lines of instruction above them
        # inverted that.
        steering_box = QGroupBox("Steering")
        steering_layout = QVBoxLayout(steering_box)
        steering_layout.setSpacing(SPACE_SNUG)
        steering_layout.addWidget(self._moving)
        steering_layout.addWidget(self._ptz_note)
        self._keys_note = WrappedNote(
            "Arrow keys pan and tilt. Shift for fine. "
            "+ and - zoom. Home recentres. "
            "Drag near an edge of the picture to slew."
        )
        self._keys_note.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )
        steering_layout.addWidget(self._keys_note)
        self._side_layout.addWidget(steering_box)

        # Both panels are built here rather than injected so the tab owns its
        # own column; what they read is injected, because one touches the
        # filesystem and the other the radio, and neither may happen on this
        # thread.
        self._link_panel = LinkPanel(radio) if radio is not None else None
        if self._link_panel is not None:
            self._side_layout.addWidget(self._link_panel)
        self._storage_panel = StoragePanel(storage) if storage is not None else None
        if self._storage_panel is not None:
            self._side_layout.addWidget(self._storage_panel)
        # No stretch after it: the movement list takes whatever the column has
        # left. It used to share the leftover with a stretch, which is why the
        # bottom of a 1080p column was four hundred pixels of empty grey next to
        # a list squeezed into a box the size of five rows.
        self._side_layout.addWidget(self._build_movement_box(), 1)

        # The column scrolls rather than squeezing. It carries five boxes now -
        # streams, link, storage, movement, steering - and on a laptop screen
        # that is more than fits. A Qt layout short of room does not shrink a
        # word-wrapped sentence to fit: it gives the box less height than it
        # asked for and lays the next line over the tail of the last one, so the
        # sentence saying the link is full is drawn through the middle of the
        # line beneath it and neither can be read. Scrolling costs a bar the
        # operator will rarely need; squeezing costs the words.
        self._side = QScrollArea()
        self._side.setWidget(side)
        self._side.setWidgetResizable(True)
        self._side.setFixedWidth(SIDE_MIN_WIDTH)
        self._side.setFrameShape(QFrame.Shape.NoFrame)
        self._side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._side)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Give the column a share of the tab rather than a number.

        Still a fixed width once chosen, because that is what stops the video
        from squeezing it - but the number is now a share of the window between
        two stops instead of the same 340 px on a 1366 laptop panel and on a 4K
        screen. Set here and not in a size policy because the column is a scroll
        area, and a scroll area with an expanding policy takes room from the
        pictures, which are the point of the tab.
        """
        super().resizeEvent(event)
        self._side.setFixedWidth(self.column_width(self.width()))

    @staticmethod
    def column_width(width: int) -> int:
        """How wide the side column is on a tab this wide.

        Pure, and separate from the resize that applies it, so the rule can be
        checked at sizes no machine running the tests has a screen for - a
        window asked to be 3840 px wide on a 1080p desk is quietly given 1684.
        """
        return max(SIDE_MIN_WIDTH, min(SIDE_MAX_WIDTH, int(width * SIDE_FRACTION)))

    def clipped(self) -> list[str]:
        """Any sentence in the side column too short for the words in it.

        The same question the link and storage panels answer about themselves,
        asked for the whole column - the two sentences the tab draws itself, and
        then each panel about its own.
        """
        cut: list[str] = []
        for label in (
            self._ptz_note,
            self._movement_note,
            self._keys_note,
            self._movement_empty,
            self._alarm_label,
        ):
            if not label.text() or not label.isVisibleTo(self):
                continue
            if label.height() < label.heightForWidth(max(label.width(), 1)):
                cut.append(label.text())
        for panel in (self._link_panel, self._storage_panel):
            if panel is not None:
                cut += panel.clipped()
        return cut

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
        row.setContentsMargins(SPACE_ROOM, SPACE_STEP, SPACE_ROOM, SPACE_STEP)
        row.setSpacing(SPACE_ROOM)
        # The glyph as well as the colour and the words: DESIGN.md's rule that
        # no state is ever carried by colour alone, and the thing that makes
        # this strip readable from across the room rather than merely red.
        glyph = QLabel("■")
        glyph.setStyleSheet(
            f"background: transparent; color: {PALETTE['bg']}; "
            f"font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
        )
        row.addWidget(glyph)
        # A WrappedNote rather than a plain word-wrapped label: this is the one
        # sentence on the tab that must never be drawn through the line under
        # it, and a word-wrapped QLabel claims it can live in one line.
        self._alarm_label = WrappedNote("")
        self._alarm_label.setStyleSheet(
            f"background: transparent; color: {PALETTE['bg']}; "
            f"font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
        )
        row.addWidget(self._alarm_label, 1)
        acknowledge = QPushButton("Acknowledge")
        acknowledge.clicked.connect(self.acknowledge)
        row.addWidget(acknowledge)
        self._alarm.setVisible(False)
        return self._alarm

    def _build_movement_box(self) -> QWidget:
        box = QGroupBox("Recent movement")
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACE_SNUG)
        self._movement = QTableWidget(0, 4)
        self._movement.setHorizontalHeaderLabels(["Time", "Stream", "What", "Confidence"])
        self._movement.verticalHeader().setVisible(False)
        self._movement.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._movement.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._movement.setShowGrid(False)
        self._movement.setAlternatingRowColors(False)
        # The three narrow columns to their contents, the last to whatever is
        # left. All four to their contents pushed "Confidence" off the right of
        # a 300 px column on a laptop panel, and a movement list you have to
        # scroll sideways to read is one nobody reads.
        header = self._movement.horizontalHeader()
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        # Left-aligned, so a heading with less room than it wants loses its tail
        # rather than both its ends: "Confide" is a word being cut short, and
        # "nfiden" is something the operator has to stop and work out.
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        # The list fits the column instead of scrolling sideways inside it.
        # Three columns to their contents and the last to whatever is left: the
        # time, the stream and what it was are the values an operator reads, and
        # none of them may be elided. "Confidence" takes the remainder, and its
        # heading is elided by Qt when the remainder is small - which is the
        # right thing to lose, because "81%" is the part that carries anything.
        #
        # This is also why SIDE_MIN_WIDTH is what it is: those three columns and
        # a readable percentage need about 290 px of list, and the column's
        # borders, padding and scrollbar take the rest.
        self._movement.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._movement, 1)
        # Shown in the table's place while there is nothing in it. An empty
        # table is a black rectangle, and a black rectangle is indistinguishable
        # from a list that failed to load - which is the wrong thing to leave an
        # operator guessing about on the one panel that reports intruders.
        self._movement_empty = WrappedNote(NOTHING_YET)
        self._movement_empty.setStyleSheet(
            f"color: {PALETTE['muted']}; padding: {SPACE_ROOM}px;"
        )
        self._movement_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._movement_empty, 1)
        self._show_movement_or_not()
        self._movement_note = WrappedNote(UNIDENTIFIED_NOTE)
        self._movement_note.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )
        layout.addWidget(self._movement_note)
        return box

    def _show_movement_or_not(self) -> None:
        """Whichever of the list and the empty state has something to say."""
        empty = self._movement.rowCount() == 0
        self._movement.setVisible(not empty)
        self._movement_empty.setVisible(empty)

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
        self._show_movement_or_not()

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
                f"QFrame#videoFrame {{ border: 3px solid {PALETTE['alarm']}; "
                f"background: {PALETTE['well']}; }}"
                if name == stream
                else f"QFrame#videoFrame {{ border: 1px solid {PALETTE['line_strong']}; "
                f"background: {PALETTE['well']}; }}"
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

    def link_lines(self) -> list[tuple[str, str]]:
        """What the link panel is saying, or nothing when there is no panel."""
        if self._link_panel is None:
            return []
        return self._link_panel.lines()

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
        # What each stream had climbed to, and what it was pointed at, before
        # this Save. Carried across below for any stream the Save did not
        # change: a Save that corrects the storage folder has said nothing about
        # a camera that is switched off, and clearing the ladder for it put the
        # console straight back to restarting it every two seconds.
        was_at = dict(self._restarts)
        was_due = dict(self._next_try)
        was_pointed_at = dict(self._urls)
        self._restarts.clear()
        self._next_try.clear()
        self._playing_for.clear()
        self._urls.clear()
        for frame in self._frames.values():
            frame.setParent(None)
        self._frames.clear()
        # No loop over the labels: each one is a child of the frame above its
        # own picture now, and it went when the frame did. Touching one here is
        # a use-after-free through shiboken, which is a raised RuntimeError in
        # the middle of applying the settings the operator just saved.
        self._labels.clear()

        for stream in settings.camera.streams:
            if not (stream.enabled and stream.url):
                continue
            pane = self._make_pane(stream.name)
            self._panes[stream.name] = pane
            self._urls[stream.name] = stream.url
            # Only a stream whose address really changed starts again from the
            # bottom of the ladder. Everything else keeps the count it had, so a
            # camera that is off is still tried at the interval it had earned.
            if was_pointed_at.get(stream.name) == stream.url:
                if stream.name in was_at:
                    self._restarts[stream.name] = was_at[stream.name]
                if stream.name in was_due:
                    self._next_try[stream.name] = was_due[stream.name]
            # Each pane sits in a frame of its own so that an event can outline
            # the stream it was seen on. The pane itself cannot carry the
            # outline: a VideoPane is only required to show, stop and report a
            # state, and libVLC draws over anything the widget paints anyway.
            frame = QFrame()
            frame.setObjectName("videoFrame")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(0, 0, 0, 0)
            frame_layout.setSpacing(0)
            # The name of the view and what it is doing, on the picture it is
            # about. They used to be a list in the side column, three feet of
            # screen away from the black rectangle they were describing, which
            # is the one arrangement that makes "which of these two is the one
            # that failed?" a question at all.
            #
            # Above the pane and not over it. libVLC draws into the pane's own
            # window handle and paints over anything the widget itself draws, so
            # a caption composited onto the picture would only be visible when
            # there was no picture. A sibling in the same layout is neither
            # between the pane and its parent nor inside it.
            label = QLabel()
            label.setContentsMargins(SPACE_SNUG, SPACE_TIGHT, SPACE_SNUG, SPACE_TIGHT)
            self._labels[stream.name] = label
            frame_layout.addWidget(label)
            if isinstance(pane, QWidget):
                frame_layout.addWidget(pane, 1)
            self._frames[stream.name] = frame
            self._wall.addWidget(frame)
            self._set_status(stream.name, "stopped")
        # The buttons come from the streams that exist, so a view nobody has a
        # stream for is never on offer. The saved choice is applied after them,
        # and one naming a stream that has since been removed falls back to
        # showing everything rather than to a black rectangle.
        self.views.set_views(list(self._panes))
        self.views.choose(settings.wall_view, announce=False)
        self._apply_view(force=True)
        # An alarm raised before the streams changed is still unacknowledged.
        self._outline(self._alarm_stream)
        self.overlay.raise_()
        # A saved budget or a saved folder changes what the storage lines say
        # about the reading already taken, and a saved radio address changes
        # what the link panel is describing, so redraw both now rather than at
        # the next heartbeat.
        if self._link_panel is not None:
            self._link_panel.refresh()
        if self._storage_panel is not None:
            self._storage_panel.refresh()

    # ------------------------------------------------------------- view modes

    def chosen_view(self) -> str:
        """The stream filling the wall, or "" for all of them side by side."""
        return self.views.chosen_view()

    def show_view(self, view: str) -> None:
        """Show this view, exactly as pressing its button would."""
        self.views.choose(view)

    def shown_streams(self) -> list[str]:
        """The views actually on the wall right now."""
        return [name for name, frame in self._frames.items() if frame.isVisibleTo(self)]

    def _view_chosen(self, view: str) -> None:
        self._apply_view()
        self.view_changed.emit(view)

    def _apply_view(self, force: bool = False) -> None:
        """Show what was chosen, and stop what is not being looked at.

        Stopping is the whole point of the mode, not a tidy-up after it. libVLC
        decoding a stream nobody can see costs this laptop real processor time
        for nothing - it is a dedicated machine with one job and no headroom to
        spare - and the operator who asked for one view asked precisely because
        two were too much for it.

        Bringing one back is `show(url)`, the same call `apply` makes, so a view
        switched away from and back to is a fresh start rather than a paused
        one. That matters on a live stream: what a paused decoder would resume
        into is a picture of a minute ago.
        """
        chosen = self.views.chosen_view()
        for name, frame in self._frames.items():
            wanted = not chosen or name == chosen
            # `force` is for the pass straight after the panes were built, when
            # every frame is already visible and nothing has changed - but
            # nothing has been started either.
            if frame.isVisibleTo(self) == wanted and not force:
                continue
            frame.setVisible(wanted)
            pane = self._panes.get(name)
            if pane is None:
                continue
            if wanted:
                url = self._local_url(name)
                if url:
                    pane.show(url)
                    self._set_status(name, pane.state)
                    self._playing_for[name] = 0
                    if not force:
                        # A view the operator has just switched BACK to is a
                        # fresh start, and its old failures were not it failing.
                        # Not on the forced pass, though: that one is `apply`
                        # rebuilding every pane, and a Save is not a reason to
                        # forgive a camera that is switched off.
                        self._restarts.pop(name, None)
                        self._next_try.pop(name, None)
            else:
                pane.stop()
                self._set_status(name, "stopped")
        self._no_views.setVisible(not self._frames)
        self._wall_area.setVisible(bool(self._frames))
        self.overlay.raise_()

    def refresh(self) -> None:
        """Read every pane's state. Restart only what has actually failed.

        Late is a report, not a trigger. The pane is left exactly as it is."""
        for name, pane in self._panes.items():
            # A view the operator has switched away from is stopped on purpose.
            # Reading its state here would report it as stopped, which is true,
            # and restarting it would undo the thing the mode exists to do.
            if not self._frames[name].isVisibleTo(self):
                continue
            state = pane.state
            self._set_status(name, state)
            if state == "failed":
                self._playing_for[name] = 0
                self._restart_when_due(name, pane)
            elif state == "playing":
                # Actually recovered, and not merely up at the instant the
                # console looked. A stream on a marginal link comes back for a
                # heartbeat and goes again, and forgiving the ladder on one good
                # reading meant it never climbed one - two seconds between
                # restarts, for ever, drowning the log the operator reads.
                settled = self._playing_for.get(name, 0) + 1
                self._playing_for[name] = settled
                if settled >= PLAYING_BEFORE_FORGIVEN:
                    self._restarts.pop(name, None)
                    self._next_try.pop(name, None)
            else:
                # Connecting, late or stopped. None of them is a recovery, and
                # a run of good readings broken by one of them starts again.
                self._playing_for[name] = 0
        self._refresh_events()
        # The camera answers on its own thread now, so its answer is picked up
        # here rather than where the key was pressed.
        self._show_camera_note()
        if self._link_panel is not None:
            self._link_panel.refresh()
        if self._storage_panel is not None:
            self._storage_panel.refresh()

    def _restart_when_due(self, name: str, pane) -> None:
        """Restart a failed stream, but never faster than the backoff allows.

        The first attempt is immediate; each one after it waits longer, up to a
        minute. A stream that has failed this many times running is not about to
        be fixed by trying harder, and the console must not spend the operator's
        Logs tab saying so.
        """
        now = self._clock()
        if now < self._next_try.get(name, 0.0):
            return
        url = self._local_url(name)
        if not url:
            return
        count = self._restarts.get(name, 0) + 1
        self._restarts[name] = count
        self._next_try[name] = now + min(
            RESTART_FIRST_DELAY * 2 ** (count - 1), RESTART_BACKOFF_MAX
        )
        self._say_it_failed(name)
        pane.show(url)
        # The word on screen changes once the console has stopped believing its
        # own retries; it is written here so the change lands with the attempt.
        self._set_status(name, "failed")

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
        count = self._restarts.get(name, 0)
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
        words = STATE_WORDS.get(state, state)
        if state == "failed" and self._restarts.get(name, 0) >= GIVING_UP_AFTER:
            words = GIVEN_UP_WORDS
        label.setText(f"{name}  -  {words}")
        # The instrument label from DESIGN.md: a tag on a plate at the top of
        # the picture, in the same monospace as every other reading, coloured by
        # the state it is reporting. The colour is never the only signal - the
        # state is spelled out in the words beside it.
        label.setStyleSheet(
            f"background: {PALETTE['surface']}; "
            f"color: {STATE_COLOURS.get(state, PALETTE['muted'])}; "
            f"font-family: {MONO}; font-size: {SIZE_HEADING}px; "
            f"font-weight: {WEIGHT_VALUE};"
        )

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
        self._commands.home()
        self._show_camera_note()

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
            self._commands.stop()
            self._moving.setText("idle")
        else:
            self._commands.move(pan, tilt, zoom)
            self._moving.setText(f"pan {pan:+.2f}  tilt {tilt:+.2f}  zoom {zoom:+.2f}")
        self._show_camera_note()

    def _show_camera_note(self) -> None:
        """Say what the camera last said, or that it has not said anything.

        Three states and not two, because the answer now arrives after the key
        that asked for it. A command still on the wire is not a command that
        succeeded, and showing nothing for it would read as one.
        """
        waiting = self._commands.unanswered_for()
        if waiting is not None and waiting >= UNANSWERED_AFTER:
            self._ptz_note.setText(UNANSWERED_NOTE)
            return
        answered = self._commands.last_answer()
        result = answered.result if answered is not None else None
        if isinstance(result, dict) and result.get("ok") is False:
            self._ptz_note.setText(result.get("error", "the camera refused the command"))
        elif waiting is None:
            self._ptz_note.setText("")

    def camera_note(self) -> str:
        return self._ptz_note.text()

    def wait_for_camera(self, timeout: float = 5.0) -> bool:
        """Wait until nothing is queued for the camera or on the wire.

        Bounded, and the answer is a bool rather than an exception: the callers
        are a closing window, which cannot afford to wait, and the tests, which
        must fail rather than hang.
        """
        return self._commands.wait_until_idle(timeout)

    def shutdown(self) -> None:
        """Bring the head to rest and let the command sender go.

        Both halves matter and in this order. A window closed with a key down
        owes the camera a stop, and letting the thread go before delivering it
        would leave the head slewing with nobody watching.
        """
        self.stop_steering()
        self._commands.close()

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
        elif key in VIEW_KEYS and self.views.choose_at(VIEW_KEYS[key]):
            # Nothing about the held keys is touched. A number pressed while an
            # arrow is down changes what is on the wall and leaves the head
            # doing exactly what it was doing, and the release of that arrow
            # still arrives at `keyReleaseEvent` and still stops it. A shortcut
            # that swallowed the release would leave the camera slewing with
            # nobody watching, which is the failure this tab may never have.
            pass
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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The tab is going away for good: stop the head, drop the sender."""
        self.shutdown()
        super().closeEvent(event)
