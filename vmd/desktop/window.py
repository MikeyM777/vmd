"""The window: four tabs, one heartbeat, and a status line that tells the truth.

Two rules run through all of it, and both come from the machine this runs on -
an unattended laptop at the end of a radio link, with one operator and no second
screen to fall back on:

* Nothing here may refuse to open. A tab that will not build becomes a label
  saying why, in its place, and the other three still work. Settings and Logs
  are how a broken installation gets diagnosed and fixed, so losing them because
  the video pane could not find libVLC would take away the only tools left.
* Nothing here may stop the recording. Closing the window closes the window; the
  recorder is a separate process on purpose, and it keeps going.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRect, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.fullscreen import LEAVE_KEY, FullscreenLive
from vmd.desktop.link import link_trouble
from vmd.desktop.live import LiveTab, WrappedNote
from vmd.desktop.logs import LogBuffer, LogsTab, attach
from vmd.desktop.playback import PlaybackTab
from vmd.desktop.settings_tab import SettingsTab
from vmd.desktop.style import (
    MONO,
    PALETTE,
    SIZE_BAND,
    SIZE_TITLE,
    SPACE_GROUP,
    SPACE_HAIR,
    SPACE_ROOM,
    SPACE_SNUG,
    SPACE_WIDE,
    WEIGHT_HEADING,
    WEIGHT_VALUE,
    state_colour,
    state_glyph,
)
from vmd.desktop.video import VideoPane
from vmd.radio.panel import STALE_AFTER_SECONDS
from vmd.settings import Settings, consoles_on_this_radio, load_settings, save_settings
from vmd.storage.index import SegmentIndex
from vmd.update.version import describe as describe_version

logger = logging.getLogger(__name__)

HEARTBEAT_MS = 2000

# How old a link reading may be before the status line says how old it is. The
# radio is asked every four seconds and an unreachable one takes about twelve to
# answer, so anything inside that is the ordinary rhythm of a link that is up.
# Past it, the number on screen is a number from a while ago, and saying so is
# the difference between "the link is at -63 dBm" and "the link was".
#
# One number, shared with the panel in the Live tab's side column: a bar calling
# a reading current while the panel beside it calls the same reading old would
# be the console arguing with itself.
LINK_STALE_SECONDS = STALE_AFTER_SECONDS

# What the streaming server says when there is nothing wrong, and when there is
# nothing to do. Compared rather than parsed: `Go2rtcService.status()` produces
# exactly these two for its two quiet states and a sentence for every other one,
# so anything that is neither is a fault by construction and stays a fault when
# a new one is added.
STREAMING_HEALTHY = "streaming"
STREAMING_OFF = "not enabled"

# How fast the recording dot pulses, in milliseconds between changes.
#
# 900 ms, and both ends of that are chosen. Faster reads as an error - a fast
# flash is the vocabulary of "something is wrong", and this dot means the
# opposite - and it is exhausting on a screen someone stands in front of for
# months. Slower and a glance can miss the change, which is the one thing the
# dot has to survive: an operator looking over for two seconds must always catch
# at least one transition, and at 900 ms they catch two.
BLINK_MS = 900

# How long a closing window waits for a save that is still being applied.
#
# Two seconds, and the number is the same one every other bounded wait in this
# console uses. The work behind it is stopping and starting child processes, and
# those outlive the window by design - so abandoning it costs nothing except the
# line under the Save button, which is on a page that is closing anyway.
SAVE_STOP_MS = 2000

# What the dot dims to on the off beat, rather than going out. A dot that
# vanishes is indistinguishable from no dot at all for as long as it is away,
# which would put the operator back to "am I looking at the wrong moment?" -
# the exact doubt this indicator exists to remove. It dims; it never leaves.
DIM_ALPHA = 0.30

# "Nobody has asked yet", as something that cannot be confused with a state.
# `status_parts`, `status_text` and `recording_now` each need the services'
# state, and each used to ask for it - so a single heartbeat had the services
# compose the whole thing twice over, including the recorder's sentence and the
# detector's report. Given one, they share it; given none, they ask.
_UNASKED = object()

# Where the console remembers the shape of its own window, beside settings.json.
#
# Beside it and deliberately not in it. settings.json is the operator's
# configuration: the file he edits, the file worth backing up, the file that
# gets read out down a phone when something is wrong. Where the window was
# dragged to last night is none of those. Keeping them in separate files means a
# Save can never fight a resize, a geometry that has gone wrong can be deleted
# without touching the camera's address, and `Settings` does not grow five
# fields it would then have to validate and migrate.
#
# It is not the registry either, which is where a Qt program would ordinarily
# put this. This console ships to an offline machine and is sometimes run from a
# folder that gets copied; a setting the operator cannot see, cannot delete and
# cannot carry with the rest of the installation is the wrong kind of memory for
# it. A small JSON file beside the settings is the seam this codebase already
# uses everywhere else - streaming.json, detection.json, recording.json.
WINDOW_FILENAME = "window.json"

# The smallest window this will ever restore to. A file half written by a power
# cut, or one from a version that spelled these keys differently, reads as zero -
# and a window with no size is a window that is not there, on a machine with no
# terminal to start another one from.
LEAST_WINDOW = (640, 400)


def read_window_state(path: str | Path) -> dict | None:
    """What the console last remembered about its window, if anything.

    Shaped after `read_endpoint` and `read_detection_status`: anything that is
    not a usable file - missing, unreadable, not JSON, JSON that is not an
    object - is None, and None means the defaults. None of them may raise. A
    console that will not open because of a file about window pixels would be
    the worst trade in this program.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def fitted(saved: QRect, screens: list[QRect]) -> QRect:
    """The remembered window, moved and cut down until it is on a real screen.

    The failure this exists to prevent: a monitor unplugged, a resolution
    changed, display scaling altered overnight - and the console opens at
    coordinates that no longer exist. The operator has no terminal, no second
    machine and no way to drag a window he cannot see. So the rule is
    safe-but-wrong over faithful-but-invisible: he can move a window that opened
    in the wrong place, and he can do nothing at all about one that did not
    open where he can reach it.

    The screen with most of the window on it is the screen it is kept on, so a
    console the operator put on the second monitor on purpose is not tidied back
    onto the first every morning. A window that fits where it was left is left
    exactly there; one hanging over an edge is pulled back inside that same
    screen; and one that is on no screen at all is put in the middle of the best
    of them, which is the only position he certainly cannot miss.
    """
    if not screens:
        return saved
    room = max(screens, key=lambda screen: _overlap(saved, screen))
    if _overlap(saved, room) > 0 and room.contains(saved):
        return saved

    width = max(min(saved.width(), room.width()), min(LEAST_WINDOW[0], room.width()))
    height = max(min(saved.height(), room.height()), min(LEAST_WINDOW[1], room.height()))
    if _overlap(saved, room) > 0:
        # Still where he left it, only pulled back inside the edge it was over.
        x = min(max(saved.x(), room.left()), room.left() + room.width() - width)
        y = min(max(saved.y(), room.top()), room.top() + room.height() - height)
        return QRect(x, y, width, height)
    # On no screen at all: put it where he cannot miss it.
    return QRect(
        room.left() + (room.width() - width) // 2,
        room.top() + (room.height() - height) // 2,
        width,
        height,
    )


def _overlap(rect: QRect, screen: QRect) -> int:
    """How much of the window is actually drawable on this screen, in pixels."""
    shared = rect.intersected(screen)
    return shared.width() * shared.height() if shared.isValid() else 0


def _dimmed(colour: str, alpha: float = DIM_ALPHA) -> str:
    """The same colour mixed down towards the page, as a hex Qt can parse."""
    page = PALETTE["bg"]
    mixed = []
    for index in (1, 3, 5):
        top = int(colour[index : index + 2], 16)
        bottom = int(page[index : index + 2], 16)
        mixed.append(int(bottom + (top - bottom) * alpha))
    return "#" + "".join(f"{part:02X}" for part in mixed)


def _streaming_state(reason) -> str:
    """Whether there are pictures, from the sentence saying why there are not."""
    words = str(reason or "")
    if words == STREAMING_HEALTHY:
        return "ok"
    if words == STREAMING_OFF:
        return "muted"
    return "alarm"


def _detection_state(detection: dict) -> str:
    """Off is not a failure. Detection is opt-in per stream, and a console that
    drew "nobody ticked the box" in alarm red would teach its operator to ignore
    the chip that one day says something true."""
    if not detection.get("enabled"):
        return "muted"
    return "ok" if detection.get("running") else "alarm"


def _doubled_words(streams: list[str]) -> str:
    """What the band says when the radio link is carrying a stream twice.

    Not an alarm and not a fault: recording and detection are both working, and
    a picture is still arriving. What has gone wrong is one level below all of
    that - the same camera is being pulled across the link more than once, on a
    link that barely carries it once, and the cost is paid by whatever needs the
    room next. The band's amber is exactly this: nothing has failed, and this is
    not healthy.

    Written in the operator's own terms. He is not technical, has no terminal
    and has never heard of a streaming server: what he has is a camera, a radio
    link and this laptop, so those are the only three things named.
    """
    named = ", ".join(streams[:-1]) + " and " + streams[-1] if len(streams) > 1 else streams[0]
    it = "them" if len(streams) > 1 else "it"
    coming = "are" if len(streams) > 1 else "is"
    return (
        f"{named} {coming} coming straight from the camera instead of through this "
        f"laptop - the link is carrying {it} twice"
    )


def _link_state(link: dict) -> str:
    """The link, in the state the panel below it has already worked out.

    This used to reason about the signal here, from the panel's thresholds, so
    that the two could not disagree about a signal reading. They still cannot -
    but they disagreed about the LINK, because the signal is not the only thing
    that decides whether a link is in trouble. On his own radio, at -66 dBm with
    88% of the airtime spent, the panel said `FULL` in red and the chip above it
    stayed a quiet green, because -66 is inside the healthy signal band. The
    thing that was full is the thing the whole system runs through.

    Two views of one radio that disagree are worse than one view, because now
    the operator has to decide which of his console's opinions to believe. So
    there is one view: `link_summary` decides, both draw what it says.
    """
    from vmd.radio.panel import link_summary

    return link_summary(link)["state"]


def _link_glance(link: dict) -> str:
    """What the link chip says when it is not the one telling the whole story.

    The bug this exists for: a chip that has given up its sentence fell back to
    its own NAME, and its name is the healthy word. A dead radio therefore drew
    as an alarm-red box containing the word `link`. Colour said fault, word said
    fine, and at two metres in a hurry the word is what is legible.

    Healthy stays the bare noun - `DESIGN.md`'s rule, and a chip that is loud
    about good news is a chip nobody reads. Anything else takes the panel's own
    headline, so the band and the panel say the same word about the same radio.
    """
    from vmd.radio.panel import HEADLINE_NO_LINK, link_summary

    summary = link_summary(link)
    if summary["state"] in ("ok", "muted"):
        return "link"
    headline = summary["headline"]
    return "no link" if headline == HEADLINE_NO_LINK else f"link {headline.lower()}"


# What each part of the band is called when it is NOT well.
#
# The worst thing the review of this console found, and it is a one-word bug
# with a whole-system consequence. A chip that is not the one speaking falls
# back to a glance word, and the glance word was the part's name: `recording`,
# `detection`, `link`. Those are the words for the healthy case. So a console
# with the recorder stopped, the detector stopped and the radio dead drew three
# alarm-red boxes reading `recording`, `detection`, `link` - the healthiest
# words it owns, inside the reddest boxes it draws, about the one fact this
# entire system exists to guarantee.
#
# Colour alone cannot carry it. `DESIGN.md` says so as a rule, and this is the
# case that rule was written for: at two metres, in a hurry, at night, the word
# is what is legible and the colour is what is peripheral. They have to agree.
#
# `muted` is not in here on purpose - detection switched off is not a fault, and
# a console that shouted `no detection` at a deliberate setting would teach him
# to stop reading the band.
TROUBLE_WORDS = {
    "services": "no services",
    "recording": "NOT recording",
    "streaming": "no pictures",
    "detection": "no detection",
    "camera": "sent twice",
}


def _base_name(part) -> str:
    """Which part of the system a chip is about, whatever word it is showing.

    The fourth element when there is one. A three-element part - anything
    driving the band directly, and every test written before the fourth was
    added - falls back to the first, which for those is the base name already.
    """
    return part[3] if len(part) > 3 else part[0]


def _glance_word(name: str, state: str) -> str:
    """The short word a chip shows when another chip is doing the talking."""
    if state in ("ok", "muted"):
        return name
    return TROUBLE_WORDS.get(name, name)


def _views_glance(trouble: list[tuple[str, str]]) -> str:
    """The short word for pictures that are not arriving.

    Names the view when there is one, because which camera has gone is the
    first thing he needs and the band has room for a word. Two or more and it
    becomes a count: `no pictures (2)` fits, `thermal, visible and gate` does
    not, and the sentence beside it names them all.
    """
    if len(trouble) == 1:
        name, state = trouble[0]
        return f"{name} frozen" if state == "late" else f"no {name}"
    return f"no pictures ({len(trouble)})"


def _views_words(trouble: list[tuple[str, str]]) -> str:
    """The sentence, in his terms. Never the word "stream": he has cameras."""
    said = []
    for name, state in trouble:
        said.append(
            f"{name} has stopped sending new pictures - what you can see is the "
            "last one that arrived"
            if state == "late"
            else f"{name} is not arriving"
        )
    return "; ".join(said)


# What `ConsoleServices.on_progress` is set back to once a save is done. A
# function and not None, so that a stray late call from a worker cannot raise.
def _SILENT(step: str) -> None:  # noqa: N802 - it is a constant, spelled as one
    return None


class _SaveSignals(QObject):
    """A save happening on a worker, talking back to the window it cannot touch.

    The only sanctioned way back to the GUI thread other than "leave a value for
    the heartbeat to read" - and this one has to be a signal, because a save has
    an end and the operator is standing there waiting for it.
    """

    progress = Signal(str)
    done = Signal(list)


class _SaveJob(QRunnable):
    """`ConsoleServices.apply` off the thread that draws the window.

    It kills and waits for up to three child processes; run inline, that is tens
    of seconds in which nothing repaints, the supervisor does not tick and the
    alarm strip cannot appear. What it must not lose in moving is the answer:
    the operator has to be told what restarted, what did not, and whether the
    settings are actually in effect, so `done` always fires and always carries
    the problems - including the case where `apply` itself threw.
    """

    def __init__(self, apply, settings, signals: _SaveSignals) -> None:
        super().__init__()
        self._apply = apply
        self._settings = settings
        self._signals = signals

    def run(self) -> None:
        try:
            answered = self._apply(self._settings)
        except Exception:  # noqa: BLE001 - the file is saved either way
            logger.exception("the child processes would not take the saved settings")
            answered = ["the child processes would not take the saved settings"]
        problems = list(answered) if isinstance(answered, list) else []
        self._signals.done.emit(problems)


# Which fault explains which, when more than one is up at once and only one of
# them can have the room for its sentence.
#
# Not the order the chips are drawn in. Everything on this machine reads its
# pictures from the local streaming server: the recorder, the detector and the
# Live tab alike. So a streaming server that is down is why footage is not
# reaching the disk and why nothing is watching for movement, and its sentence
# is the one that says what to do about all three. `services` is ahead of it
# because a console that could not ask anything knows nothing at all, and the
# link is behind because a link fault shows up as a streaming fault long before
# anyone reads the band. Anything not named here sorts after everything named.
#
# These are the BASE names of the parts - `streaming`, `recording` - and never
# the words a chip shows when it is in trouble. That distinction is the whole
# of a bug this table had for a long time: `worst` looked the rank up by the
# chip's glance word, and a chip in trouble shows a TROUBLE word instead of its
# name ("NOT recording", "no pictures"). None of those are keys here, so every
# troubled chip tied at the bottom rank and the order they happened to be drawn
# in decided who spoke. On the commonest broken install of all - go2rtc not
# there yet - the band said "NOT recording" and put `go2rtc is not installed -
# run install.bat` away in a glance word: the symptom out loud and the cure
# hidden, which is exactly what this table exists to prevent. Every part now
# carries its base name as a fourth element and is ranked on that.
CAUSE_BEFORE_EFFECT = {
    name: rank
    for rank, name in enumerate(
        ("services", "streaming", "views", "recording", "detection", "link", "camera")
    )
}


class StatusChip(QFrame):
    """One thing about the system, in the size it deserves.

    A glyph, a word and a border, in that order of what carries the meaning.
    DESIGN.md: colour never says anything on its own, so the glyph says the same
    thing for anyone who cannot tell green from amber, and the sentence beside
    it says it in words for everyone else.

    Quiet chips are the name of the part and nothing else - the glyph has
    already said it is fine, and `streaming: streaming` says it a second time in
    the vertical space this console owes to the pictures. The border and the
    panel behind it arrive with the fault, so the one chip that is worth reading
    is the only one drawn as a box.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusChip")
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_SNUG, 0, SPACE_SNUG, 0)
        row.setSpacing(SPACE_SNUG)
        self._glyph = QLabel("")
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # One line, never two. This was a WrappedNote, on the reasoning that the
        # longest of these is a whole sentence and a sentence must not be cut in
        # half - which is right about the sentence and wrong about where it
        # belongs. A wrapped label given less width than its words takes the
        # height instead, and the band is across the top of every tab: on a
        # 1080p panel at 150% scaling the logical screen is 1280 px, four fault
        # sentences do not fit across it, and the band that should be one line
        # was taking a quarter of the screen from the pictures. The operator had
        # faults up most of a day, so that is what he was looking at.
        #
        # The full sentence still exists and is still read - `status_text`
        # carries it into the Logs tab unchanged, and the link's version is in
        # the Live tab's own panel. What this label promises is height, not
        # completeness, and `text()` below still answers with the whole thing.
        self._full = ""
        self._words = QLabel("")
        self._words.setWordWrap(False)
        # Ignored, so the label never demands the width of its sentence. Without
        # it a chip whose words do not fit pushes the band wider than the window
        # and the last chip goes off the right-hand edge; with it, the words
        # shorten and the row keeps its shape.
        self._words.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        row.addWidget(self._glyph)
        row.addWidget(self._words, 1)
        # Set by the recording dot, which draws its own glyph rather than the
        # one the state would give it. None means "whatever the state says".
        self._own_glyph: tuple[str, str] | None = None
        self.show_state("", "muted")

    def sizeHint(self):  # noqa: N802 - Qt naming
        """As wide as saying it in one line, and no wider.

        A word-wrapped QLabel asks for a squarish block rather than for its
        sentence, so four chips left to their own hints wrap on a 1920 px screen
        with half the band empty beside them. This asks for the line, which the
        layout grants while there is room and squeezes when there is not - and
        squeezing is the case the wrapping was built for.
        """
        hint = super().sizeHint()
        text = self._full
        if text:
            layout = self.layout()
            margins = layout.contentsMargins()
            hint.setWidth(
                margins.left()
                + margins.right()
                + self._glyph.sizeHint().width()
                + layout.spacing()
                + self._words.fontMetrics().horizontalAdvance(text)
                # The frame's own border, on both sides.
                + 2
            )
        return hint

    def text(self) -> str:
        """What this chip is saying, whole. What is painted may be shortened to
        the room there is; what it means never is."""
        return self._full

    def painted_text(self) -> str:
        """What is actually on the screen, which is the shortened form when
        there is not room for the sentence. Separate from `text` so that a test
        about the words and a test about the room cannot be confused."""
        return self._words.text()

    def _fit_words(self) -> None:
        """Put as much of the sentence on the screen as there is room for.

        Shortened from the right, with an ellipsis, so what survives is the
        beginning - which is where these sentences put the part being reported
        and the state it is in. `detection: NOT running - restarted 9 times...`
        still says the two things worth glancing at.
        """
        room = max(self._words.width(), 1)
        metrics = self._words.fontMetrics()
        shown = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, room)
        # Only on a change: setting it invalidates the layout, and a layout
        # invalidated from inside its own resize does not settle.
        if shown != self._words.text():
            self._words.setText(shown)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._fit_words()

    def glyph(self) -> str:
        return self._glyph.text()

    def glyph_colour(self) -> str:
        return self._own_glyph[1] if self._own_glyph is not None else ""

    def set_glyph(self, glyph: str, colour: str) -> None:
        """Draw this glyph in this colour, whatever the state would have drawn.

        The recording chip is the one that does this: its dot is red in both of
        its states, and what tells them apart is that one of them moves.
        """
        self._own_glyph = (glyph, colour)
        self._glyph.setText(glyph)
        self._glyph.setStyleSheet(
            f"background: transparent; color: {colour}; "
            f"font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
        )

    def show_state(self, text: str, state: str) -> None:
        colour = state_colour(state)
        if self._own_glyph is None:
            self._glyph.setText(state_glyph(state))
            self._glyph.setStyleSheet(
                f"background: transparent; color: {colour}; "
                f"font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
            )
        # Healthy is written in ordinary ink and only the glyph is green. Four
        # green sentences across the top of the window is a wall of colour that
        # says nothing; one red one in the middle of three quiet ones is seen
        # from the other side of the room.
        words = PALETTE["ink"] if state in ("ok", "muted") else colour
        self._words.setStyleSheet(
            f"background: transparent; color: {words}; "
            f"font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
        )
        self._full = text
        self._fit_words()
        # The sentence just changed, so the width this chip is asking for has
        # changed with it. Without this the layout keeps handing out the room the
        # previous words needed.
        self.updateGeometry()
        # The border tints toward the state, as DESIGN.md has it. A chip with
        # nothing to report is not a box at all: no panel behind it and an edge
        # the colour of the page. It keeps the border rather than dropping it so
        # that a fault arriving does not move the row by two pixels - the reflow
        # this band accepts is the sentence getting longer, which is meaning, and
        # not an outline appearing, which is decoration.
        quiet = state in ("ok", "muted")
        panel = "transparent" if quiet else PALETTE["surface"]
        edge = "transparent" if quiet else colour
        self.setStyleSheet(
            f"QFrame#statusChip {{ background: {panel}; border: 1px solid {edge}; }}"
        )


class StatusBand(QFrame):
    """The health of the whole system, across the top of every tab.

    This is the same sentence the status bar used to carry in eleven pixels of
    grey at the bottom of the window - whether footage is reaching the disk,
    whether there are pictures, whether anything is watching them, and whether
    the radio link is up. It is the most important thing on the screen and it
    was the least prominent, which is the wrong way round for a console someone
    is standing in front of all day.

    One line of it, and no more. What earns the band its place at the top of
    every tab is the 16 px - what an operator two metres back can read - and not
    the padding around it: this is a screen whose whole purpose is showing
    video, and a band that is a block rather than a line is charging the
    pictures for space it is not using.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBand")
        self.setStyleSheet(
            f"QFrame#statusBand {{ background: {PALETTE['bg']}; "
            f"border-bottom: 1px solid {PALETTE['line']}; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_ROOM, SPACE_HAIR, SPACE_ROOM, SPACE_HAIR)
        # Wide between chips, because they are no longer boxes: with the borders
        # gone from the quiet ones, the gap is the only thing separating one
        # reading from the next.
        row.setSpacing(SPACE_GROUP)
        name = QLabel("VMD")
        name.setStyleSheet(
            f"background: transparent; color: {PALETTE['muted']}; "
            f"font-family: {MONO}; font-size: {SIZE_TITLE}px; "
            f"font-weight: {WEIGHT_HEADING};"
        )
        name.setContentsMargins(0, 0, SPACE_WIDE, 0)
        row.addWidget(name)
        self._row = row
        # The room to the right of the chips, so that each is as wide as what it
        # is saying rather than a quarter of the window. Four words used to be
        # stretched across four boxes the width of the longest sentence any of
        # them might one day carry; now the chip with the fault takes the room
        # it needs and the quiet ones take none.
        row.addStretch(1)
        self._chips: list[StatusChip] = []

        # One line high, and the number does not depend on a word anybody types
        # into a fault sentence. Measured from the type rather than from the
        # chips, so it is the same on an empty band, a healthy one and one with
        # five faults up - which is the whole point: this band sits above the
        # pictures on every tab, and its height is space taken from them.
        ruler = QFont(self.font())
        ruler.setPixelSize(SIZE_BAND)
        line = QFontMetrics(ruler).height()
        # The chip's own border, top and bottom, then the band's margins.
        self.setFixedHeight(line + 2 + 2 * SPACE_HAIR)

    def chips(self) -> list[str]:
        """What each chip is saying, for the window and for the tests."""
        return [chip.text() for chip in self._chips if chip.isVisibleTo(self)]

    def glyphs(self) -> list[str]:
        """The mark beside each chip. What says a part has failed even when the
        room for its sentence went to a worse one."""
        return [chip.glyph() for chip in self._chips if chip.isVisibleTo(self)]

    def painted(self) -> list[str]:
        """What is actually drawn, shortened to the room there is."""
        return [chip.painted_text() for chip in self._chips if chip.isVisibleTo(self)]

    def recording_glyph(self) -> str:
        """The dot itself, for the tests: a circle beats a bar."""
        return self._chips[0].glyph() if self._chips else ""

    def recording_colour(self) -> str:
        """What colour the dot is being drawn in right now."""
        return self._chips[0].glyph_colour() if self._chips else ""

    def show_recording(self, recording: bool, bright: bool, chosen: bool = False) -> None:
        """The dot that says whether the perimeter is being recorded.

        Three states, and none of them is "nothing there": a pulsing circle
        while footage is reaching the disk, a still red bar when it has stopped
        and should not have, and a quiet hollow circle when it is off because
        somebody switched it off. What separates the first from the others
        across a room is the movement, not the colour - so a console that is not
        recording cannot be mistaken for one whose dot happened to be on its dim
        beat when somebody looked.

        The third is why the third exists. Recording off on purpose - the
        Playback tab switched off - drawn in the same red as a dead drive is a
        band with a permanent alarm in it, and a permanent alarm is one nobody
        reads.
        """
        if not self._chips:
            return
        if recording:
            self._chips[0].set_glyph(
                "●", PALETTE["alarm"] if bright else _dimmed(PALETTE["alarm"])
            )
        elif chosen:
            self._chips[0].set_glyph("○", PALETTE["muted"])
        else:
            self._chips[0].set_glyph("■", PALETTE["alarm"])

    def show_parts(self, parts: list[tuple[str, str, str]]) -> None:
        """Draw one chip per part. The number of them varies: a services object
        that cannot be asked anything answers with one sentence, not three.

        Each part arrives as the word it goes by, the sentence it would say in
        full, and the state. Healthy is the word; anything else is the sentence.
        The rule is one rule and it is the honest way round: a chip says its own
        name while there is nothing to add, and says everything the moment there
        is - and "nobody could be asked" counts as something to add.

        One of them, though, and not four. A console with the streaming server
        down has detection down as well and the link complaining, and four
        sentences of that do not fit across 1280 logical pixels - which is what
        a 1080p laptop panel at 150% scaling is. They wrapped, and the band took
        a quarter of the screen away from the pictures for as long as the faults
        were up, which on a bad day is all day.

        So the room goes to the worst one, which is also nearly always the one
        that explains the rest: the streaming server being down is *why*
        detection is not running. The others keep their glyph, their colour and
        their border - nothing is hidden, and a second fault is still plainly a
        fault at a glance - and give up only their sentence, which is in the
        Logs tab in full, where `status_text` has always put it.
        """
        while len(self._chips) < len(parts):
            chip = StatusChip()
            # Before the stretch that holds the right-hand room, so the chips
            # stay in the order they were given and the space stays at the end.
            self._row.insertWidget(self._row.count() - 1, chip)
            self._chips.append(chip)
        speaking = self.worst(parts)
        for index, chip in enumerate(self._chips):
            if index < len(parts):
                # The first three and not the whole tuple: a part may carry a
                # fourth element naming which part of the system it is about,
                # which is what `worst` ranks on and which nothing here draws.
                glance, words, state = parts[index][:3]
                chip.show_state(words if index == speaking else glance, state)
                chip.setVisible(True)
            else:
                chip.setVisible(False)

    @staticmethod
    def worst(parts: list[tuple[str, str, str]]) -> int | None:
        """Which one part gets to say its sentence, or None when all is well.

        A failure outranks a warning. Among equals it is the one that EXPLAINS
        the others, which is not the order they are drawn in: everything on this
        machine reads its pictures from the local streaming server, so a
        streaming server that is down is why footage is not reaching the disk
        and why nothing is watching for movement. Telling the operator "NOT
        recording" when the sentence one chip along says `go2rtc is not
        installed - run install.bat` is telling him the symptom and keeping the
        cure.

        `muted` never speaks: it is not a fault, and a console that spelled out
        "detection is switched off" every four seconds would be teaching him to
        stop reading the band.

        Pure and separate from the drawing, so the rule can be checked without a
        window.
        """
        for wanted in ("alarm", "warn"):
            standing = [
                index for index, part in enumerate(parts) if part[2] == wanted
            ]
            if standing:
                return min(
                    standing,
                    key=lambda index: (
                        CAUSE_BEFORE_EFFECT.get(
                            _base_name(parts[index]), len(CAUSE_BEFORE_EFFECT)
                        ),
                        index,
                    ),
                )
        return None


class ConsoleWindow(QMainWindow):
    def __init__(
        self,
        settings_path: str | Path,
        services,
        ptz,
        radio,
        index_path: str | Path,
        make_pane: Callable[[str], VideoPane],
        events_path: str | Path | None = None,
        log_buffer: LogBuffer | None = None,
        parent: QWidget | None = None,
        panes=None,
    ) -> None:
        super().__init__(parent)
        # What `make_pane` reads when it builds a video pane, or None for a
        # window built without one - a test, or anything driving this directly.
        # The only thing on it is how far behind the camera the live picture
        # runs, and this window's part in that is one line in `settings_saved`:
        # write the saved figure in before the Live tab rebuilds its panes.
        self._panes = panes
        self.setWindowTitle("VMD")
        self.resize(1440, 900)

        self._settings_path = Path(settings_path)
        self._geometry_path = self._settings_path.parent / WINDOW_FILENAME
        self._services = services
        self._ptz = ptz
        self._radio = radio
        self._index: SegmentIndex | None = None
        # Handed in by `main`, which attaches it before the services are
        # started so that what they say while starting is not lost. One is made
        # here only for a window built without one - a test, or anything that
        # constructs the console directly.
        self._buffer = attach(log_buffer if log_buffer is not None else LogBuffer())
        # Opened before the tabs and outside their factories, so that a database
        # which will not open costs detection rather than the Live tab.
        #
        # This store belongs to the GUI thread and is the Playback tab's, which
        # reads it when the operator picks a day. The Live tab reads the same
        # file on a worker instead - see `_movement_reader` - because it reads
        # on the heartbeat and this file is in the folder that goes away. Two
        # readers of one file, not two answers: sqlite in WAL mode is how the
        # detector writes it while both of them read it.
        self._events_path = events_path
        self.events = self._open_events(events_path)

        try:
            settings = load_settings(self._settings_path)
        except Exception:  # noqa: BLE001 - an unreadable file is a Settings tab job
            logger.exception("the settings could not be read; using the defaults")
            settings = Settings()

        def build_live() -> QWidget:
            tab = LiveTab(
                ptz=ptz,
                make_pane=make_pane,
                local_url=services.local_url,
                # Not the store itself: the Live tab reads on a worker.
                events=self._movement_reader(),
                # The same reading the status line asks about recording, drawn
                # in the right column as the design has it. `getattr`, because
                # services are handed in and one without a disk watcher must
                # cost the storage lines and nothing else.
                storage=getattr(services, "disk", None),
                # The same cached reading the status line asks about the link,
                # drawn in full in the right column. The bar is the glance and
                # the panel is the detail: signal against what it means,
                # throughput against the capacity that explains the stuttering.
                # It reads the service's answer and never the radio, because
                # asking the radio costs about 12 s when it is unreachable.
                radio=radio,
                # No zoom is passed. The bars under the pictures are driven by a
                # `ZoomHandle` the tab builds over its own command sender - see
                # `LiveTab.__init__` - because that sender is what keeps a
                # button press off the radio link. The parameter exists so a
                # test can put something else in its place.
            )
            # Before `apply`, which is what passes it on: the panel is built
            # by the constructor above and the sentence under its bar is about
            # the whole radio, not this camera.
            tab.set_cameras_on_the_link(
                consoles_on_this_radio(self._settings_path, settings)
            )
            # Built here rather than after the tabs are assembled so that a
            # stream that cannot be shown fails this tab and nothing else.
            tab.apply(settings)
            tab.view_changed.connect(self.view_changed)
            tab.show_footage.connect(self.show_footage)
            return tab

        # Kept, because the Playback tab is built and destroyed while the
        # console runs rather than only at startup. See `show_playback_tab`.
        self._index_path = index_path
        self._make_pane = make_pane

        def build_settings() -> QWidget:
            tab = SettingsTab(settings_path=self._settings_path)
            tab.load()
            return tab

        self.live = self._tab("Live", build_live)
        # None until it is asked for, and off unless the settings say otherwise.
        # See `Settings.show_playback`: looking back through footage is no
        # longer part of what this console is for, and the tab that does it is
        # not on the screen by default.
        self.playback: QWidget | None = None
        self.settings_tab = self._tab("Settings", build_settings)
        self.logs = self._tab("Logs", lambda: LogsTab(self._buffer))

        # A save has to reach what is running, or it has changed nothing the
        # operator can see. The Settings tab may be a label saying why it could
        # not be built, in which case there is nothing to connect and nothing
        # that could have been saved.
        saved = getattr(self.settings_tab, "saved", None)
        if saved is not None:
            saved.connect(self.settings_saved)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.logs, "Logs")
        # After the three that are always there, so that turning it on puts it
        # back in its old place - second, between Live and Settings - rather
        # than on the end where nobody has ever looked for it.
        self.show_playback_tab(settings.show_playback)
        # And the name of the place, on the window and above the pictures. Done
        # here rather than in `build_live`, because it is also the window's
        # title and there are two consoles side by side on one desktop: which
        # of them the taskbar button belongs to is the same question.
        self.set_title(settings.title)

        # The band above the tabs rather than a sentence in a footer, and above
        # rather than inside any one tab, because it is true of the machine and
        # not of whichever page happens to be open. The status bar it replaces
        # is gone: two places saying the same thing is one place too many, and
        # the one that was there was the smallest text on the screen.
        self.band = StatusBand()
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.band)
        column.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        # The pictures on the whole screen, which is how this console is meant
        # to be watched most of the day. Built here because the mode is the
        # window's: what it hides is the band above the tabs, the tab bar, and
        # the Live tab's own side column - and the pictures themselves are not
        # moved, rebuilt or reparented by any of it. See
        # `vmd/desktop/fullscreen.py` for why that last part is not optional.
        # `settings.screen` goes with it: the mode fills the screen the window
        # is on, and which screen this console belongs on is the installation's
        # decision rather than the window's. Without it, a console dragged onto
        # the other monitor for a moment took THAT monitor the next time F11 was
        # pressed - and there are two of these on one desktop, with the operator
        # working on the screen the other one is not watching from.
        self.fullscreen = FullscreenLive(
            window=self,
            tabs=self.tabs,
            band=self.band,
            live=self.live,
            screen=settings.screen,
        )
        # The Live tab carries the button; a tab that could not be built carries
        # nothing, and then the keys are the whole of it.
        asked = getattr(self.live, "fullscreen_asked", None)
        if asked is not None:
            asked.connect(self.fullscreen.set_active)
        # The gear above the pictures asks for Settings. It is the only way in
        # to Settings once the tab bar is hidden by stream-only, so it must
        # work even then. A tab that could not be built carries no such signal.
        asked_settings = getattr(self.live, "settings_asked", None)
        if asked_settings is not None:
            asked_settings.connect(self.show_settings)
        # Show only the pictures, if the settings say so. Applied through the
        # fullscreen object because it hides exactly the same three things -
        # band, tab bar, side column - so the two modes cannot fight; see
        # `FullscreenLive.set_stream_only`. Read here at start-up and re-applied
        # on every Save. The window is not moved or resized: stream-only is an
        # ordinary window on purpose, so two of them fit side by side.
        #
        # But never when there is no gear to come back with. The gear lives on
        # the Live tab, and every tab in this window may instead be a label
        # saying why it could not be built - a Live tab needs libVLC, and a
        # machine without it is not rare, it is the offline laptop before VLC
        # has been installed. Hiding the tab bar there would leave no tab bar,
        # no gear and no way into Settings or Logs at all, on the one machine
        # with no terminal to fix it from. So the chrome stays.
        if settings.stream_only and asked_settings is None:
            logger.warning(
                "showing only the pictures was asked for, but the Live tab could "
                "not be built and it carries the button that opens the settings, "
                "so the tabs are being left on screen"
            )
        else:
            self.fullscreen.set_stream_only(settings.stream_only)

        # One at a time, and never on this thread: applying a save restarts up
        # to three child processes. See `_SaveJob`.
        self._save_pool = QThreadPool(self)
        self._save_pool.setMaxThreadCount(1)
        self._saving: list[_SaveSignals] = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.heartbeat)
        self._timer.start(HEARTBEAT_MS)

        # One timer for the whole console, and it only runs while there is
        # something to pulse. A widget that owns its own timer is a widget that
        # keeps repainting behind a hidden window for months.
        self._bright = True
        self._blink = QTimer(self)
        self._blink.setInterval(BLINK_MS)
        self._blink.timeout.connect(self._beat)
        state = self._ask_state()
        self.band.show_parts(self.status_parts(state))
        self._show_recording(state)

        # Last, after the tabs exist. A size restored before them is a size the
        # layouts have not been consulted about, and the one it would be clamped
        # to is whatever the four tabs together turn out to need.
        self._restore_geometry()

    def _restore_geometry(self) -> None:
        """Open where he left it, the size he left it, on a screen that exists.

        The only unfinished thing in this console that cost him something every
        single day: it opened at a default size every morning, and resizing it
        was the first thing he did. Nothing here may raise - a remembered window
        is a kindness, and a kindness that stops the console opening is not one.
        """
        state = read_window_state(self._geometry_path)
        if not state:
            return
        try:
            saved = QRect(
                int(state["x"]), int(state["y"]), int(state["width"]), int(state["height"])
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "the remembered window could not be read; opening at the usual size"
            )
            return
        try:
            screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
            self.setGeometry(fitted(saved, screens))
            if state.get("maximised"):
                # The state, not `showMaximized`: this runs while the window is
                # still being built and nothing has shown it yet, and a window
                # that showed itself here would appear before `main` was ready
                # for it. Set this way it opens maximised on the first `show`,
                # and leaves the fullscreen bit - which is not this console's to
                # set - exactly as it found it.
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        except Exception:  # noqa: BLE001 - the console opens either way
            logger.exception("the remembered window could not be restored")

    def _remember_geometry(self) -> None:
        """Write down the shape of the window, for tomorrow morning.

        The size under a maximised or fullscreen window rather than the screen
        it is filling, so that un-maximising tomorrow gives back the window he
        actually chose rather than one the size of the display.
        """
        covering = self.isMaximized() or self.isFullScreen()
        rect = self.normalGeometry() if covering else self.geometry()
        if rect.width() <= 0 or rect.height() <= 0:
            rect = self.geometry()
        try:
            self._geometry_path.write_text(
                json.dumps(
                    {
                        "x": rect.x(),
                        "y": rect.y(),
                        "width": rect.width(),
                        "height": rect.height(),
                        "maximised": bool(self.isMaximized()),
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            # A full disk, a folder that has gone, a file somebody made
            # read-only. Every one of those is a real state on this machine and
            # none of them is a reason a window will not shut.
            logger.warning(
                "the window's size and position could not be remembered", exc_info=True
            )

    def _movement_reader(self):
        """The movement list, readable from a thread that is not this one.

        The Live tab reads it on a worker, because events.db is in the
        recordings root and that is the folder that goes away - see
        `vmd/desktop/watch.py`. The store the Playback tab uses cannot go with
        it: `EventStore`'s own rule is that one instance belongs to one thread,
        and sqlite enforces that by refusing.

        So the tab is handed this instead, which opens its own store for each
        reading and closes it again - which is exactly what `EventStore` says
        another thread should do, and WAL is why it is safe beside the detector
        writing. None when there is no database to read, which costs the
        movement list and nothing else.
        """
        if self.events is None:
            return None
        path = self._events_path

        class _MovementAnywhere:
            @staticmethod
            def recent(limit: int):
                from vmd.detect.events import EventStore

                store = EventStore(path)
                try:
                    return store.recent(limit)
                finally:
                    store.close()

        return _MovementAnywhere()

    @staticmethod
    def _open_events(events_path: str | Path | None):
        """The movement events, or None and a line in the log.

        What is guarded here is the database, not the import. A file that has
        been corrupted, a disk that is not mounted, a folder that cannot be
        written - none of those is a reason to lose the window, because the
        Settings and Logs tabs behind it are how they get fixed.

        It used to be guarding something else as well, and that was the harm:
        `vmd.detect` pulled the whole vision stack in, so on a laptop without it
        this caught the ImportError and the operator silently lost the movement
        list and every mark on the timeline, with the reason only in the Logs
        tab. A console that opens with no movement in it looks like a quiet
        perimeter. `vmd/detect/__init__.py` resolves its re-exports on first use
        now, so `EventStore` costs sqlite3 and that case no longer exists - and
        if it ever comes back, an unimportable module is a broken installation
        and is said as loudly as everything else here.
        """
        if events_path is None:
            return None
        try:
            from vmd.detect.events import EventStore
        except ImportError:
            # Not "the database is unreadable". Part of VMD is missing, which is
            # a different fault with a different fix, and it must not be filed
            # away under a corrupt file.
            logger.exception(
                "the movement events could not be opened because part of VMD is "
                "missing; reinstall VMD"
            )
            return None
        try:
            return EventStore(events_path)
        except Exception:  # noqa: BLE001 - a console with no movement list still helps
            logger.exception("the movement events could not be opened: %s", events_path)
            return None

    @staticmethod
    def _tab(name: str, build: Callable[[], QWidget]) -> QWidget:
        """The tab, or a label saying why there isn't one.

        Whatever went wrong is written where the tab would have been, and the
        traceback goes to the log - which is still reachable, because this is
        the reason the Logs tab is built the same way as the rest.
        """
        try:
            return build()
        except Exception as exc:  # noqa: BLE001 - three tabs beat no window
            logger.exception("the %s tab could not be built", name)
            label = QLabel(f"The {name} tab could not be opened: {exc}")
            label.setWordWrap(True)
            label.setMargin(16)
            return label

    def _build_playback(self) -> QWidget:
        self._index = SegmentIndex(self._index_path)
        return PlaybackTab(
            index=self._index, pane=self._make_pane("playback"), events=self.events
        )

    def show_playback_tab(self, wanted: bool) -> None:
        """Put the Playback tab on the window, or take it off.

        Built and destroyed rather than hidden, and that is the safe direction
        rather than the tidy one. Hiding it would mean keeping a libVLC video
        pane alive for a tab nobody can reach - a player, its decoder threads,
        and an open segment database - on a console that is expected to run for
        months without being restarted. Rebuilding costs the fraction of a
        second nobody is waiting through, because the only way here is a press
        of Save.

        Nothing about the footage moves either way. The recorder is a separate
        process, it is not told, and the files are where they were.

        Called with what is already true on every save, so the tab itself is
        left alone unless the answer has changed: one torn down and rebuilt
        because somebody corrected a password would drop whatever the operator
        had open on it. The Live tab is told either way, because the first call
        of all is the one that has nothing to build and still has to say that
        there is nowhere to go.
        """
        wanted = bool(wanted)
        changed = wanted != (self.playback is not None)
        if changed and wanted:
            self.playback = self._tab("Playback", self._build_playback)
            # Second, where it has always been. `insertTab` past the end is a
            # plain append, so this is right even for a window built with fewer
            # tabs than usual.
            self.tabs.insertTab(1, self.playback, "Playback")
        elif changed:
            going = self.playback
            self.playback = None
            index = self.tabs.indexOf(going)
            if index >= 0:
                self.tabs.removeTab(index)
            try:
                # Parented to nothing first: `removeTab` alone leaves the widget
                # a top-level window, which on the way out of a tab bar means a
                # bare Playback tab flashing up as its own window.
                going.setParent(None)
                going.deleteLater()
            except Exception:  # noqa: BLE001 - the tab is off the bar either way
                logger.exception("the Playback tab would not let go")
            self._close_index()
        # The two controls on the Live tab that go to Playback, told there is
        # somewhere to go or told there is not.
        tell = getattr(self.live, "set_playback", None)
        if tell is not None:
            try:
                tell(wanted)
            except Exception:  # noqa: BLE001 - a button beats the window
                logger.exception("the Live tab would not take the Playback switch")

    def _close_index(self) -> None:
        """Let go of the segment database, if one is open."""
        index, self._index = self._index, None
        if index is None:
            return
        try:
            index.close()
        except Exception:  # noqa: BLE001 - closing a file may not stop anything
            logger.exception("the segment index would not close")

    def set_title(self, name: str) -> None:
        """The name of the place this console watches: window and Live tab.

        On the window because there are two of these running side by side on one
        desktop, and the taskbar, Alt-Tab and the title bar are how Windows
        tells them apart. "VMD" twice is no answer; "VMD - ירושלים" is.

        The application's own name stays in front of it. It is what the
        installer, the shortcut and every instruction in the guide call this
        program, and a window that calls itself only "ירושלים" has quietly
        renamed the thing somebody was told to look for.
        """
        name = (name or "").strip()
        # The version is part of the name of the program, not decoration: it is
        # the first thing anybody is asked for when they report something, and
        # on this machine there is no About box, no terminal and no second
        # screen to find it on.
        #
        # Read from the project root, not from the settings folder: on a
        # multi-camera install the settings file lives in
        # cameras\250\settings.json, and the VERSION file that travels with an
        # update lives at the top of the whole checkout, three levels above
        # this module (vmd/desktop/window.py -> vmd/desktop -> vmd -> root).
        root = Path(__file__).resolve().parent.parent.parent
        program = describe_version(root)
        self.setWindowTitle(f"{program} - {name}" if name else program)
        tell = getattr(self.live, "set_title", None)
        if tell is not None:
            try:
                tell(name)
            except Exception:  # noqa: BLE001 - a caption is not the console
                logger.exception("the Live tab would not take the name")

    def heartbeat(self) -> None:
        """Restart whatever died, read every pane, refresh what is on screen.

        Every step is separately guarded: the one that matters is the first, and
        a Logs tab that throws while redrawing must not stop the supervisor from
        being asked again in two seconds' time.
        """
        try:
            self._services.tick()
        except Exception:  # noqa: BLE001 - a bad tick must not stop the console
            logger.exception("supervising the child processes failed")

        self._refresh(self.live)
        if self.tabs.currentWidget() is self.logs:
            self._refresh(self.logs)
        # Asked once and handed to both. See `_UNASKED`.
        state = self._ask_state()
        self._tell_live_about_detection(state)
        self._tell_live_about_recording(state)
        self.band.show_parts(self.status_parts(state))
        self._show_recording(state)

    def _tell_live_about_detection(self, state) -> None:
        """Let the movement list know whether anything is watching.

        It said "Nothing has moved yet." whatever was happening, which is the
        reassuring one of the two things an empty list can mean - and the wrong
        one whenever the detector is off or dead. Every other empty state in
        this console distinguishes "nothing to report" from "nobody is
        reporting"; the panel that reports intruders was the one that did not.

        The window knows and the tab does not, so the window tells it. Guarded
        like everything else on the heartbeat.
        """
        tell = getattr(getattr(self, "live", None), "set_watching", None)
        if tell is None:
            return
        try:
            detection = (state or {}).get("detection") or {}
            tell(_detection_state(detection) if state is not None else "alarm")
        except Exception:  # noqa: BLE001 - the heartbeat goes on
            logger.exception("the movement list could not be told about detection")

    def _tell_live_about_recording(self, state) -> None:
        """Let the pictures know if footage has stopped reaching the disk.

        The one fault stream-only would otherwise hide silently: "NOT recording"
        lives in the status band and the side column, and stream-only hides
        both. So the window - which already has the services' state in hand on
        this heartbeat, and is the only thing that polls the recorder - hands the
        Live tab the fault, which shows it on one line above the pictures.
        Guarded like everything else on the heartbeat.
        """
        tell = getattr(getattr(self, "live", None), "set_recording_fault", None)
        if tell is None:
            return
        try:
            tell(self._recording_fault(state))
        except Exception:  # noqa: BLE001 - the heartbeat goes on
            logger.exception("the pictures could not be told whether it is recording")

    @staticmethod
    def _recording_fault(state) -> str | None:
        """The sentence for the fault bar when recording has stopped, else None.

        The same rule the band's recording chip uses, so the two cannot
        disagree about the one thing this console exists to guarantee: a fault is
        footage not reaching the disk when nobody chose to stop it. Recording
        that is off on purpose - "Record everything to disk" unticked, which is
        how this ships - is `chosen`, is not a fault, and puts nothing on the
        screen at all. A services object that cannot be asked is itself the
        fault and is said, because a console that cannot tell whether it is
        recording is not one that is recording.
        """
        if state is None:
            return "VMD cannot tell whether it is recording. Restart VMD."
        recording = state.get("recording_state") or {}
        if bool(state.get("recording")) or bool(recording.get("chosen")):
            return None
        return recording.get("reason") or "NOT recording"

    def _show_recording(self, state=_UNASKED) -> None:
        """Point the dot at the truth, and run the timer only while it moves."""
        recording = self.recording_now(state)
        # Whether the dot is a state to notice or a state that was asked for.
        # `state` may be the sentinel that means "nobody has asked yet", and a
        # sentinel has no opinion about recording.
        reading = state if isinstance(state, dict) else {}
        chosen = bool((reading.get("recording_state") or {}).get("chosen"))
        self.band.show_recording(recording, self._bright, chosen=chosen)
        if recording and self.isVisible():
            if not self._blink.isActive():
                self._blink.start()
        elif self._blink.isActive():
            self._blink.stop()

    def _beat(self) -> None:
        """One beat of the recording dot, and nothing else on the screen.

        Deliberately not a refresh: this fires every 900 ms for months, and
        redrawing a side column to move one dot is the kind of cost that turns
        into a laptop fan nobody can explain.
        """
        self._bright = not self._bright
        self.band.show_recording(True, self._bright)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._show_recording()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Nothing pulses behind a window nobody is looking at."""
        self._blink.stop()
        super().hideEvent(event)

    def show_footage(self, event) -> None:
        """Take him to the movement he asked about, on the tab that shows it.

        The only thing in this console that owns both tabs, which is why the
        going happens here and not in either of them. Five steps become one: the
        tab, the day, the stream, the mark and the click were all his to find,
        under the pressure the alarm had just created, on a machine where he has
        no second screen to look anything up on.

        The tab is changed first and the seek follows, for a reason that has
        already cost this console a commit: an arrow key held while the focus is
        on a child of the Live tab is a key whose release will never arrive once
        the tab is gone, and the Live tab's `hideEvent` is what stops the head.
        Changing tab before the seek means that stop is delivered before
        anything slower can go wrong, rather than after.

        Nothing here may raise. It runs from a button press during an alarm, and
        a Playback tab that could not be built - the case every tab in this
        window is written to survive - must cost the operator the footage and
        not the console.
        """
        show = getattr(self.playback, "show_event", None)
        if show is None:
            logger.warning(
                "there is nowhere to show that movement: the Playback tab is "
                "switched off, or it could not be opened"
            )
            return
        # Out of fullscreen first, and this is a trap rather than a nicety.
        # Fullscreen hides the tab bar and the status band, because it exists to
        # show pictures and nothing else. Changing tab underneath that left him
        # on Playback with no tab bar to leave it by, no band, and a Live tab's
        # Esc key that was no longer the thing with focus - at the one moment
        # this console is under pressure, which is while an alarm is up.
        #
        # Leaving is also the right thing on its own account: he pressed a
        # button that means "show me the recording", and the recording is on a
        # tab, and a tab is not what fullscreen is for.
        leave = getattr(getattr(self, "fullscreen", None), "leave", None)
        if leave is not None:
            try:
                leave()
            except Exception:  # noqa: BLE001 - the footage matters more
                logger.exception("could not leave fullscreen to show that movement")
        index = self.tabs.indexOf(self.playback)
        if index >= 0:
            self.tabs.setCurrentIndex(index)
        try:
            show(event)
        except Exception:  # noqa: BLE001 - the console must survive a button
            logger.exception("that movement could not be shown")

    def show_settings(self) -> None:
        """Go to the Settings page, from the gear above the pictures.

        The gear is how Settings is reached when the tab bar is hidden, which is
        what stream-only does - so this is the way in that must not depend on
        there being a tab to click. The Settings tab may be a label saying why
        it could not be built, as every tab in this console may be, and going to
        that label is still right: it is where the reason is written, and it is
        the only account of the fault the operator can reach.
        """
        index = self.tabs.indexOf(self.settings_tab)
        if index >= 0:
            self.tabs.setCurrentIndex(index)

    def view_changed(self, view: str) -> None:
        """Remember which view the operator is looking at, for tomorrow.

        Read from the file and written back to it, rather than written from
        anything held in memory: the Settings tab may have half-typed edits on
        screen, and a view change is not the moment to commit them. Only this
        one field moves.

        Guarded end to end. A settings file that cannot be written is a real
        state on this machine - a full disk is one of the things this console
        exists to report - and it may not cost the operator the view they just
        asked for. The view is already on the wall; this is only the memory of
        it.
        """
        try:
            settings = load_settings(self._settings_path)
            if settings.wall_view == view:
                return
            settings.wall_view = view
            save_settings(settings, self._settings_path)
        except Exception:  # noqa: BLE001 - the wall changed either way
            logger.exception("which view is on the wall could not be remembered")

    def settings_saved(self, settings) -> None:
        """Point the running console at what was just written.

        Nothing here re-reads settings.json by itself: go2rtc parses its
        configuration once at startup, the PTZ and radio services hold the
        address and password they were built with, and the panes hold the URLs
        they were given. A save that only wrote the file would leave every one
        of them on the old settings until the laptop was rebooted - and this
        operator has no terminal, no second machine, and a camera that has to
        come back.

        The children go to a worker. `ConsoleServices.apply` runs `taskkill` and
        up to four process waits per child, tens of seconds in the bad case, and
        it used to run inside this slot - so pressing Save froze the window at
        the moment the operator is most likely to be standing in front of it
        waiting for an answer. That is the fault the PTZ and the radio services
        were both rewritten to remove; this was the last place it lived.

        The camera and the radio are asked here, because both already answer
        from a thread of their own and neither waits. Each separately: a camera
        that will not take the change must not cost the radio, and none of them
        may throw back into the Save button.

        The pictures are pointed at the new settings when the children are done
        rather than now, and that ordering is deliberate: the panes read their
        URLs from the streaming server, and a wall rebuilt while that server is
        halfway through a restart is a wall pointed at a port nothing is
        listening on.
        """
        # First, and not on the worker: neither of these touches a child
        # process, and both are things the operator has just watched himself
        # ask for. A name that appeared three seconds after Save, once go2rtc
        # had finished restarting, would read as the console having ignored him.
        try:
            self.set_title(getattr(settings, "title", ""))
            self.show_playback_tab(getattr(settings, "show_playback", False))
            # Which monitor this console belongs on can be changed from the
            # Settings tab, and fullscreen is what reads it after start-up. A
            # mode still holding the number the console opened with would put
            # the pictures back on the old monitor at the next F11, hours after
            # the operator had watched himself change it.
            self.fullscreen.set_screen(getattr(settings, "screen", None))
            # And whether to show only the pictures. The same object that owns
            # fullscreen owns this, so the two cannot fight; turning it on here
            # hides the chrome on the window that is open, which is the whole
            # point of a save on a machine the operator cannot restart.
            #
            # Guarded the same way it is at start-up: the gear that opens these
            # settings again lives on the Live tab, and if that tab could not be
            # built there is no gear - so hiding the tab bar would take away the
            # only remaining way back. Refusing to hide it is the safe way to be
            # wrong.
            wanted_stream_only = getattr(settings, "stream_only", False)
            if wanted_stream_only and getattr(self.live, "settings_asked", None) is None:
                logger.warning(
                    "showing only the pictures was saved, but the Live tab could "
                    "not be built and it carries the button that opens the "
                    "settings, so the tabs are being left on screen"
                )
            else:
                self.fullscreen.set_stream_only(wanted_stream_only)
        except Exception:  # noqa: BLE001 - the rest of the save must still run
            logger.exception("the window would not take the saved name or tabs")

        # Before the children are restarted and long before the wall is rebuilt,
        # because rebuilding the wall is what reads it. A pane is built with its
        # delay and cannot be told afterwards - it is a libVLC instance option -
        # so the order here is the whole of why the setting takes effect at all.
        tell = getattr(self.live, "set_cameras_on_the_link", None)
        if tell is not None:
            try:
                tell(consoles_on_this_radio(self._settings_path, settings))
            except Exception:  # noqa: BLE001 - a sentence is not the save
                logger.exception("could not say how many cameras share the link")

        if self._panes is not None:
            try:
                self._panes.delay_ms = int(settings.live_delay_ms)
                self._panes.boxes = bool(settings.show_boxes)
            except Exception:  # noqa: BLE001 - a delay is not the save
                logger.exception("the saved picture delay could not be applied")

        for what, target in (("the camera", self._ptz), ("the radio", self._radio)):
            apply = getattr(target, "apply", None)
            if apply is None:
                continue
            try:
                apply(settings)
            except Exception:  # noqa: BLE001 - the file is saved either way
                # Not reported back to the operator. The camera and the radio
                # are at the far end of a radio link and answer when they feel
                # like it; the save itself succeeded and the next heartbeat asks
                # them again. A child that would not restart is different:
                # nothing asks it again, and what is running is not what was
                # saved.
                logger.exception("%s would not take the saved settings", what)

        apply = getattr(self._services, "apply", None)
        if apply is None:
            self._save_finished(settings, [])
            return

        signals = _SaveSignals()
        signals.progress.connect(self._say_saving)
        signals.done.connect(lambda found: self._save_finished(settings, found, signals))
        self._saving.append(signals)
        self._say_saving("putting it into effect")
        # The same seam the camera tools use: whoever runs the work says where
        # to report it.
        try:
            self._services.on_progress = signals.progress.emit
        except Exception:  # noqa: BLE001 - a caption is not the save
            logger.exception("the save progress could not be wired up")
        self._save_pool.start(_SaveJob(apply, settings, signals))

    def _say_saving(self, step: str) -> None:
        """What is being done, under the button, with the button held.

        A frozen window says nothing and a finished one says "Saved." The
        seconds in between are the ones the operator is actually watching, and
        this line is the only place on the machine where they can be described.
        """
        saying = getattr(self.settings_tab, "report_progress", None)
        if saying is None:
            return
        try:
            saying(f"Saved. {step[:1].upper()}{step[1:]}...")
        except Exception:  # noqa: BLE001 - the save is running either way
            logger.exception("the save progress could not be shown")

    def _save_finished(self, settings, problems: list, signals=None) -> None:
        """The children are done: point the pictures at the new settings, and
        say what did and did not take effect."""
        if signals is not None and signals in self._saving:
            self._saving.remove(signals)
        try:
            self._services.on_progress = _SILENT
        except Exception:  # noqa: BLE001 - the save is done either way
            logger.exception("the save progress could not be unwired")

        apply = getattr(self.live, "apply", None)
        if apply is not None:
            try:
                apply(settings)
            except Exception:  # noqa: BLE001 - the file is saved either way
                logger.exception("the pictures would not take the saved settings")

        self._report_save([str(problem) for problem in problems])
        state = self._ask_state()
        self.band.show_parts(self.status_parts(state))
        self._show_recording(state)

    def _report_save(self, problems: list[str]) -> None:
        """Say, under the button that was just pressed, what took effect.

        The file really was written, so "Saved." is not a lie - but on its own
        it is the wrong half of the truth when a child would not restart, and
        the operator has no terminal, walks away, and believes the system is
        running what they typed.

        Said every time now rather than only when something went wrong, because
        the line under the button no longer says "Saved." while the children are
        being restarted - it says what is being done - and a page still
        describing work that has finished is a page that has stopped being true.
        """
        report = getattr(self.settings_tab, "report_after_save", None)
        if report is None:
            return
        try:
            report("Saved." if not problems else "Saved, but " + "; ".join(problems) + ".")
        except Exception:  # noqa: BLE001 - the save is done either way
            logger.exception("the save result could not be shown")

    @staticmethod
    def _refresh(tab: QWidget) -> None:
        """Redraw a tab, if it is a tab and if redrawing it works."""
        refresh = getattr(tab, "refresh", None)
        if refresh is None:
            return
        try:
            refresh()
        except Exception:  # noqa: BLE001 - the next tick tries again
            logger.exception("redrawing %s failed", type(tab).__name__)

    def status_text(self) -> str:
        """One line, always. A status bar that raises has told the operator
        nothing, and taken the heartbeat down with it."""
        # `part[1]` rather than unpacking: a part carries a fourth element
        # naming which part of the system it is about, and this line is only
        # ever after the sentence.
        return " · ".join(part[1] for part in self.status_parts())

    def _ask_state(self):
        """What the services are doing, or None because they could not be asked.

        None is a state of its own and is drawn as one: a services object that
        cannot answer is a fault, not an empty answer.
        """
        try:
            return self._services.state()
        except Exception:  # noqa: BLE001 - the console must go on drawing
            logger.exception("the services could not say what they are doing")
            return None

    def status_parts(self, state=_UNASKED) -> list[tuple[str, str, str]]:
        """The same sentences the status line has always carried, each with the
        word it goes by and the state it is reporting.

        Split out so the band across the top can draw each one as a chip of its
        own with a glyph and a colour, instead of the whole thing being one grey
        sentence in a footer. The sentences are unchanged and `status_text`
        still joins them exactly as before: what changed is that the console now
        knows which of them is the bad one, and what each is called when there
        is nothing to say about it.

        The glance word and the sentence come from here together rather than
        from two methods, because two lists of four things are two lists that
        can end up describing different parts of the system in the same column.
        """
        parts: list[tuple[str, str, str]] = []
        if state is _UNASKED:
            state = self._ask_state()

        if state is None:
            # No glance word: this is never the healthy case, so nothing would
            # ever draw one.
            # "the services could not be asked what they are doing" was a
            # sentence about our own source code - "services" is what this
            # console calls the recorder and the detector between ourselves, and
            # he has never seen the word - and it ended without saying what to
            # do, which on the one screen he has is where the sentence has to
            # end. The glance word stays "services": it is never drawn while the
            # chip has room for the sentence, and nothing else in the band is
            # short enough to name this.
            parts.append(
                (
                    "services",
                    "VMD cannot see its own recorder and detector. Restart VMD.",
                    "alarm",
                    "services",
                )
            )
        else:
            # The recorder's own sentence, which says whether it died and was
            # restarted rather than only whether it is up - the treatment
            # detection has had from the start. `.get` twice, because the
            # services are handed in and one that answers only yes or no must
            # still produce a status line.
            recording = state.get("recording_state") or {}
            # The honest signal, and the only one this dot is ever allowed to
            # follow: `recording` is whether footage is reaching the disk, not
            # whether a process was alive at the instant the console looked.
            is_recording = bool(state.get("recording"))
            # Three states and not two. Recording, not recording because
            # something is wrong, and not recording because it was switched off
            # - and the third is not an alarm. A console that is doing what it
            # was told, drawn in the same red as a dead drive, is a band that
            # teaches the operator to stop reading it.
            chosen = bool(recording.get("chosen"))
            if is_recording:
                mood = "ok"
            elif chosen:
                mood = "muted"
            else:
                mood = "alarm"
            parts.append(
                (
                    _glance_word("recording", mood),
                    recording.get("reason")
                    or ("recording" if is_recording else "NOT recording"),
                    mood,
                    "recording",
                )
            )
            streaming = state.get("streaming")
            parts.append(
                (
                    _glance_word("streaming", _streaming_state(streaming)),
                    f"streaming: {streaming}",
                    _streaming_state(streaming),
                    "streaming",
                )
            )
            # `.get` twice: the services are handed in, and a state without a
            # word about detection must produce a status line, not a KeyError
            # that costs the operator the recording state as well.
            detection = state.get("detection") or {}
            parts.append(
                (
                    _glance_word("detection", _detection_state(detection)),
                    f"detection: {detection.get('reason', 'unknown')}",
                    _detection_state(detection),
                    "detection",
                )
            )

        try:
            link = self._radio.status()
        except Exception:  # noqa: BLE001
            logger.exception("the radio could not be asked about the link")
            parts.append(("no link", "link unknown", "alarm", "link"))
        else:
            # The signal figure is not in the glance word on purpose. A reading
            # inside the healthy band is the same news every four seconds, and
            # the Live tab's link panel carries it in full - with what it means
            # beside it - one tab away, for the moment somebody wants the
            # number rather than the reassurance.
            parts.append(
                (_link_glance(link), self._link_words(link), _link_state(link), "link")
            )

        # And whether that link is carrying anything twice. Last, beside the
        # link it is about, and only when there is something to say: a chip that
        # is present and quiet on a healthy machine is furniture, and this one
        # has to be noticed the once in a year it appears.
        #
        # `.get`, because the services are handed in and one that has never
        # heard of this must still produce a status line.
        doubled = []
        if state is not None:
            try:
                doubled = list(state.get("on_camera") or [])
            except Exception:  # noqa: BLE001 - the band must go on being drawn
                logger.exception("what the link is carrying could not be read")
        if doubled:
            parts.append(
                (_glance_word("camera", "warn"), _doubled_words(doubled), "warn", "camera")
            )

        # And whether he can actually see the fence, which is the thing this
        # band was not reporting at all. See `LiveTab.views_in_trouble`: the
        # chips are about services, and a camera view is not a service, so one
        # dead picture and one playing drew four green chips.
        #
        # First in the list rather than last, because it outranks every other
        # part of it: a recorder that is running is recording nothing worth
        # having if no picture is arriving to record.
        #
        # Being first no longer decides it, though - `views` has its own rank in
        # CAUSE_BEFORE_EFFECT, ahead of recording and detection for the reason
        # above and behind streaming, because a streaming server that is down is
        # WHY no picture is arriving and its sentence is the one with the cure in
        # it. Position in the list is now only the tie-break it was always meant
        # to be.
        trouble = self._views_in_trouble()
        if trouble:
            parts.insert(
                0, (_views_glance(trouble), _views_words(trouble), "alarm", "views")
            )

        return parts

    def _views_in_trouble(self) -> list[tuple[str, str]]:
        """Which pictures are not arriving. Never raises: this is the band."""
        ask = getattr(getattr(self, "live", None), "views_in_trouble", None)
        if ask is None:
            return []
        try:
            return list(ask())
        except Exception:  # noqa: BLE001 - the band must go on being drawn
            logger.exception("the pictures could not be asked how they are")
            return []

    def recording_now(self, state=_UNASKED) -> bool:
        """Whether footage is reaching the disk, for the dot that says so.

        Read from `recording` and from nothing else, so that a services object
        which cannot be asked anything leaves the dot saying "not recording" -
        which is the truth, and is the safe way round. A dot that keeps blinking
        because nobody could be asked is exactly the lie this indicator exists
        to stop telling.

        It takes the heartbeat's own reading when there is one rather than
        asking again: composing the state a second time on every beat asked the
        recorder, the disk and the detector's report all over again for an
        answer the beat already had.
        """
        if state is _UNASKED:
            state = self._ask_state()
        try:
            return bool(state is not None and state.get("recording"))
        except Exception:  # noqa: BLE001 - the dot must not take the heartbeat down
            logger.exception("the services could not say whether they are recording")
            return False

    @staticmethod
    def _link_words(link: dict) -> str:
        """The link, in the state it is actually in.

        Four states, not two. The radio is read on a thread of its own now -
        asking it costs about 12 s when it is unreachable, and the window may
        not stop repainting for that - so the answer here can be a reading
        nobody has managed to take yet, or one taken a while ago. Neither may be
        shown as though it were the state of the link now: an operator who reads
        a signal figure believes the link was up when they read it.
        """
        if link.get("checking"):
            return "link checking"
        signal = link.get("signal_dbm")
        if signal is None:
            # `link -` is what this console says when the radio has nothing to
            # report. A radio that has refused the login has something to
            # report, and saying nothing about it is how a hard failure came to
            # look exactly like one still being checked.
            if _link_state(link) == "alarm":
                return link_trouble(str(link.get("reason") or ""))[0]
            return "link -"
        age = link.get("age_seconds")
        if isinstance(age, (int, float)) and age >= LINK_STALE_SECONDS:
            return f"link {signal} dBm ({age:.0f} s ago)"
        # The signal is not always what is wrong with the link, and it was the
        # only thing this said. At -66 dBm with 88% of the airtime spent, this
        # sentence read "link -66 dBm" - a perfectly healthy-looking number
        # about a link with nothing left in it. When there is something else to
        # say, the panel has already worked out what it is; say that, and put
        # the number after it rather than instead of it.
        from vmd.radio.panel import link_summary

        summary = link_summary(link)
        if summary["state"] in ("warn", "alarm") and summary["note"]:
            # Through `_link_glance` rather than the headline directly, so the
            # sentence and the short word are the same words - and so that
            # "no link" does not come out as "link no link".
            return f"{_link_glance(link)}: {summary['note']} ({signal} dBm)"
        return f"link {signal} dBm"

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """The two keys that own the fullscreen mode, wherever the focus is.

        Read here rather than bound as Qt shortcuts, which is the rule the Live
        tab already states for the number keys: a shortcut is delivered ahead of
        the ordinary key handling, and this window steers a camera with keys
        that are HELD. Nothing in this console may ever be in a position to
        swallow the release of an arrow, because a swallowed release is a head
        that goes on slewing with nobody watching.

        Key events that nothing handled travel up the parent chain to here, so
        `Esc` pressed on the picture, in the movement list or in a settings
        field all arrive the same way.
        """
        # A held F11 auto-repeats, and a mode that toggled thirty times a second
        # is a screen nobody can read.
        if not event.isAutoRepeat():
            # Fullscreen has first say, and its Esc is unchanged: when it is
            # active, Esc leaves fullscreen. Only after it has declined does the
            # stream-only Esc get a look in, which is what keeps the precedence
            # right - fullscreen out first, pictures back second.
            if self.fullscreen.handle_key(int(event.key())):
                event.accept()
                return
            if self._stream_only_key(int(event.key())):
                event.accept()
                return
        super().keyPressEvent(event)

    def _stream_only_key(self, key: int) -> bool:
        """Esc back to the pictures when the tab bar is hidden, else leave it be.

        In stream-only mode there is no tab bar to click: the gear takes the
        operator to Settings or Logs, and this is how he gets back. Whether this
        key belongs to it, having acted if it does - the same shape as
        `FullscreenLive.handle_key`, and read the same way, from the window's own
        key handler rather than as a Qt shortcut, so a key that is held for
        steering can never have its release swallowed.

        It never fires in fullscreen: `handle_key` runs first and has already
        taken Esc when fullscreen is active, so this is only ever the ordinary
        window that is showing only its pictures, on a page that is not the
        pictures. On the Live page it does nothing, so Esc there is still free
        for whatever the pictures make of it.
        """
        if key != LEAVE_KEY:
            return False
        if self.fullscreen.active() or not self.fullscreen.stream_only():
            return False
        if self.tabs.currentWidget() is self.live:
            return False
        index = self.tabs.indexOf(self.live)
        if index < 0:
            return False
        self.tabs.setCurrentIndex(index)
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Close the window. Deliberately does not stop the children: recording
        outlives the interface, which is the point of running it separately."""
        # First, before anything slower and before anything that can fail: the
        # window is the shape it is right now, and everything below this line is
        # about letting something go.
        self._remember_geometry()
        self._timer.stop()
        self._blink.stop()
        # A save that is still restarting a child is holding a reference to this
        # window's signals. Bounded, because everything that waits here is: the
        # children outlive the window on purpose, and one that will not stop may
        # not hold the console open.
        if not self._save_pool.waitForDone(SAVE_STOP_MS):
            logger.warning("a save is still being applied; letting it finish alone")
        # The head first, and before anything slower. A window closed with an
        # arrow key down owes the camera a stop, and a stop that is not
        # delivered leaves it slewing towards its own end stop with nobody
        # watching. Bounded inside, and guarded here because the Live tab may be
        # a label saying why it could not be built.
        shutdown = getattr(self.live, "shutdown", None)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the camera would not be brought to rest")
        # The radio is read on a thread of its own; let it go rather than leave
        # it logging in behind a console nobody is looking at.
        close_radio = getattr(self._radio, "close", None)
        if close_radio is not None:
            try:
                close_radio()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the radio reader would not close")
        self._close_index()
        if self.events is not None:
            try:
                self.events.close()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the movement events would not close")
            self.events = None
        super().closeEvent(event)
