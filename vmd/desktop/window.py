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

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.live import LiveTab, WrappedNote
from vmd.desktop.logs import LogBuffer, LogsTab, attach
from vmd.desktop.playback import PlaybackTab
from vmd.desktop.settings_tab import SettingsTab
from vmd.desktop.style import (
    MONO,
    PALETTE,
    SIZE_BAND,
    SIZE_HEADING,
    SIZE_TITLE,
    SPACE_ROOM,
    SPACE_SNUG,
    SPACE_STEP,
    SPACE_WIDE,
    WEIGHT_HEADING,
    WEIGHT_VALUE,
    state_colour,
    state_glyph,
)
from vmd.desktop.video import VideoPane
from vmd.radio.panel import STALE_AFTER_SECONDS
from vmd.settings import Settings, load_settings, save_settings
from vmd.storage.index import SegmentIndex

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

# What the dot dims to on the off beat, rather than going out. A dot that
# vanishes is indistinguishable from no dot at all for as long as it is away,
# which would put the operator back to "am I looking at the wrong moment?" -
# the exact doubt this indicator exists to remove. It dims; it never leaves.
DIM_ALPHA = 0.30


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


def _link_state(link: dict) -> str:
    """The link, in the bands `vmd/radio/panel.py` reads the signal against.

    The thresholds are that module's - it is where they are explained and where
    they were chosen - so the chip and the panel below it can never disagree
    about whether the same reading is healthy.
    """
    from vmd.radio.panel import SIGNAL_HEALTHY_DBM, SIGNAL_MARGINAL_DBM

    if link.get("checking"):
        return "muted"
    signal = link.get("signal_dbm")
    if not isinstance(signal, (int, float)) or isinstance(signal, bool):
        return "muted"
    age = link.get("age_seconds")
    stale = isinstance(age, (int, float)) and age >= LINK_STALE_SECONDS
    if signal < SIGNAL_MARGINAL_DBM:
        return "alarm"
    if signal < SIGNAL_HEALTHY_DBM:
        return "warn"
    # A reading nobody has taken for a while may not be drawn in the colour
    # that means "the link is fine right now", which is the one thing it cannot
    # say. The panel below applies the same rule to the same reading.
    return "muted" if stale else "ok"


class StatusChip(QFrame):
    """One thing about the system, in the size it deserves.

    A glyph, a word and a border, in that order of what carries the meaning.
    DESIGN.md: colour never says anything on its own, so the glyph says the same
    thing for anyone who cannot tell green from amber, and the sentence beside
    it says it in words for everyone else.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusChip")
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_ROOM, SPACE_SNUG, SPACE_ROOM, SPACE_SNUG)
        row.setSpacing(SPACE_STEP)
        self._glyph = QLabel("")
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # A WrappedNote, because the longest of these is a whole sentence -
        # "go2rtc is not installed - run install.bat" - and it is the one that
        # must not be cut in half.
        self._words = WrappedNote("")
        row.addWidget(self._glyph)
        row.addWidget(self._words, 1)
        # Set by the recording dot, which draws its own glyph rather than the
        # one the state would give it. None means "whatever the state says".
        self._own_glyph: tuple[str, str] | None = None
        self.show_state("", "muted")

    def text(self) -> str:
        return self._words.text()

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
        self._words.setText(text)
        # The border tints toward the state, as DESIGN.md has it, and stays the
        # ordinary line colour when there is nothing to report.
        edge = PALETTE["line"] if state in ("ok", "muted") else colour
        self.setStyleSheet(
            f"QFrame#statusChip {{ background: {PALETTE['surface']}; "
            f"border: 1px solid {edge}; }}"
        )


class StatusBand(QFrame):
    """The health of the whole system, across the top of every tab.

    This is the same sentence the status bar used to carry in eleven pixels of
    grey at the bottom of the window - whether footage is reaching the disk,
    whether there are pictures, whether anything is watching them, and whether
    the radio link is up. It is the most important thing on the screen and it
    was the least prominent, which is the wrong way round for a console someone
    is standing in front of all day.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBand")
        self.setStyleSheet(
            f"QFrame#statusBand {{ background: {PALETTE['bg']}; "
            f"border-bottom: 1px solid {PALETTE['line']}; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_ROOM, SPACE_STEP, SPACE_ROOM, SPACE_STEP)
        row.setSpacing(SPACE_SNUG)
        name = QLabel("VMD")
        name.setStyleSheet(
            f"background: transparent; color: {PALETTE['muted']}; "
            f"font-family: {MONO}; font-size: {SIZE_TITLE}px; "
            f"font-weight: {WEIGHT_HEADING};"
        )
        name.setContentsMargins(0, 0, SPACE_WIDE, 0)
        row.addWidget(name)
        self._row = row
        self._chips: list[StatusChip] = []

    def chips(self) -> list[str]:
        """What each chip is saying, for the window and for the tests."""
        return [chip.text() for chip in self._chips if chip.isVisibleTo(self)]

    def recording_glyph(self) -> str:
        """The dot itself, for the tests: a circle beats a bar."""
        return self._chips[0].glyph() if self._chips else ""

    def recording_colour(self) -> str:
        """What colour the dot is being drawn in right now."""
        return self._chips[0].glyph_colour() if self._chips else ""

    def show_recording(self, recording: bool, bright: bool) -> None:
        """The dot that says whether the perimeter is being recorded.

        Two states, and neither of them is "nothing there": a pulsing circle
        while footage is reaching the disk, and a still bar when it is not. What
        separates them across a room is the movement, not the colour - so a
        console that is not recording cannot be mistaken for one whose dot
        happened to be on its dim beat when somebody looked.
        """
        if not self._chips:
            return
        if recording:
            self._chips[0].set_glyph(
                "●", PALETTE["alarm"] if bright else _dimmed(PALETTE["alarm"])
            )
        else:
            self._chips[0].set_glyph("■", PALETTE["alarm"])

    def show_parts(self, parts: list[tuple[str, str]]) -> None:
        """Draw one chip per part. The number of them varies: a services object
        that cannot be asked anything answers with one sentence, not three."""
        while len(self._chips) < len(parts):
            chip = StatusChip()
            self._row.addWidget(chip, 1)
            self._chips.append(chip)
        for index, chip in enumerate(self._chips):
            if index < len(parts):
                chip.show_state(*parts[index])
                chip.setVisible(True)
            else:
                chip.setVisible(False)


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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VMD")
        self.resize(1440, 900)

        self._settings_path = Path(settings_path)
        self._services = services
        self._ptz = ptz
        self._radio = radio
        self._index: SegmentIndex | None = None
        # Handed in by `main`, which attaches it before the services are
        # started so that what they say while starting is not lost. One is made
        # here only for a window built without one - a test, or anything that
        # constructs the console directly.
        self._buffer = attach(log_buffer if log_buffer is not None else LogBuffer())
        # One store, read by Live and by Playback: two connections to one file
        # would be two answers to the same question. Opened before the tabs and
        # outside their factories, so that a database which will not open costs
        # detection rather than the Live tab.
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
                events=self.events,
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
            )
            # Built here rather than after the tabs are assembled so that a
            # stream that cannot be shown fails this tab and nothing else.
            tab.apply(settings)
            tab.view_changed.connect(self.view_changed)
            return tab

        def build_playback() -> QWidget:
            self._index = SegmentIndex(index_path)
            return PlaybackTab(
                index=self._index, pane=make_pane("playback"), events=self.events
            )

        def build_settings() -> QWidget:
            tab = SettingsTab(settings_path=self._settings_path)
            tab.load()
            return tab

        self.live = self._tab("Live", build_live)
        self.playback = self._tab("Playback", build_playback)
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
        self.tabs.addTab(self.playback, "Playback")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.logs, "Logs")

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
        self.band.show_parts(self.status_parts())
        self._show_recording()

    @staticmethod
    def _open_events(events_path: str | Path | None):
        """The movement events, or None and a line in the log.

        The import is here rather than at the top of the file for the same
        reason the failure is swallowed: `vmd.detect` pulls in the detector's
        stack, and the console has to open on a laptop where the detector was
        never installed, where its database has been corrupted, or where the
        disk holding it is not mounted. None of those is a reason to lose the
        window - the Settings and Logs tabs behind it are how they get fixed.
        """
        if events_path is None:
            return None
        try:
            from vmd.detect.events import EventStore

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
        self.band.show_parts(self.status_parts())
        self._show_recording()

    def _show_recording(self) -> None:
        """Point the dot at the truth, and run the timer only while it moves."""
        recording = self.recording_now()
        self.band.show_recording(recording, self._bright)
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

        Each part separately: a camera that will not take the change must not
        cost the radio, and none of them may throw back into the Save button.
        """
        problems: list[str] = []
        for what, target in (
            ("the streaming server", self._services),
            ("the camera", self._ptz),
            ("the radio", self._radio),
            ("the pictures", self.live),
        ):
            apply = getattr(target, "apply", None)
            if apply is None:
                continue
            try:
                answered = apply(settings)
            except Exception:  # noqa: BLE001 - the file is saved either way
                logger.exception("%s would not take the saved settings", what)
                # Only the children are reported back to the operator. The
                # camera and the radio are at the far end of a radio link and
                # answer when they feel like it; the save itself succeeded and
                # the next heartbeat asks them again. A child that would not
                # restart is different: nothing asks it again, and what is
                # running is not what was saved.
                if target is self._services:
                    problems.append("the child processes would not take the saved settings")
            else:
                # The services answer with the plain sentences describing what
                # could not be applied; the others answer with nothing.
                if isinstance(answered, list):
                    problems.extend(str(problem) for problem in answered)
        self._report_save(problems)
        self.band.show_parts(self.status_parts())
        self._show_recording()

    def _report_save(self, problems: list[str]) -> None:
        """Say, under the button that was just pressed, what did not take effect.

        The file really was written, so "Saved." is not a lie - but on its own
        it is the wrong half of the truth when a child would not restart, and
        the operator has no terminal, walks away, and believes the system is
        running what they typed.
        """
        if not problems:
            return
        report = getattr(self.settings_tab, "report_after_save", None)
        if report is None:
            return
        try:
            report("Saved, but " + "; ".join(problems) + ".")
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
        return " · ".join(text for text, _state in self.status_parts())

    def status_parts(self) -> list[tuple[str, str]]:
        """The same sentences the status line has always carried, each with the
        state it is reporting.

        Split out so the band across the top can draw each one as a chip of its
        own with a glyph and a colour, instead of the whole thing being one grey
        sentence in a footer. The words are unchanged and `status_text` still
        joins them exactly as before: what changed is that the console now knows
        which of them is the bad one.
        """
        parts: list[tuple[str, str]] = []

        try:
            state = self._services.state()
        except Exception:  # noqa: BLE001
            logger.exception("the services could not say what they are doing")
            parts.append(("the services could not be asked what they are doing", "alarm"))
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
            parts.append(
                (
                    recording.get("reason")
                    or ("recording" if is_recording else "NOT recording"),
                    "ok" if is_recording else "alarm",
                )
            )
            streaming = state.get("streaming")
            parts.append((f"streaming: {streaming}", _streaming_state(streaming)))
            # `.get` twice: the services are handed in, and a state without a
            # word about detection must produce a status line, not a KeyError
            # that costs the operator the recording state as well.
            detection = state.get("detection") or {}
            parts.append(
                (
                    f"detection: {detection.get('reason', 'unknown')}",
                    _detection_state(detection),
                )
            )

        try:
            link = self._radio.status()
        except Exception:  # noqa: BLE001
            logger.exception("the radio could not be asked about the link")
            parts.append(("link unknown", "alarm"))
        else:
            parts.append((self._link_words(link), _link_state(link)))

        return parts

    def recording_now(self) -> bool:
        """Whether footage is reaching the disk, for the dot that says so.

        Its own reading rather than a flag set inside `status_parts`, so that a
        services object which cannot be asked anything leaves the dot saying
        "not recording" - which is the truth, and is the safe way round. A dot
        that keeps blinking because nobody could be asked is exactly the lie
        this indicator exists to stop telling.
        """
        try:
            return bool(self._services.state().get("recording"))
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
            return "link -"
        age = link.get("age_seconds")
        if isinstance(age, (int, float)) and age >= LINK_STALE_SECONDS:
            return f"link {signal} dBm ({age:.0f} s ago)"
        return f"link {signal} dBm"

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Close the window. Deliberately does not stop the children: recording
        outlives the interface, which is the point of running it separately."""
        self._timer.stop()
        self._blink.stop()
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
        if self._index is not None:
            try:
                self._index.close()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the segment index would not close")
        if self.events is not None:
            try:
                self.events.close()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the movement events would not close")
            self.events = None
        super().closeEvent(event)
