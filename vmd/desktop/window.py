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

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QWidget

from vmd.desktop.live import LiveTab
from vmd.desktop.logs import LogBuffer, LogsTab, attach
from vmd.desktop.playback import PlaybackTab
from vmd.desktop.settings_tab import SettingsTab
from vmd.desktop.video import VideoPane
from vmd.settings import Settings, load_settings
from vmd.storage.index import SegmentIndex

logger = logging.getLogger(__name__)

HEARTBEAT_MS = 2000


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
            )
            # Built here rather than after the tabs are assembled so that a
            # stream that cannot be shown fails this tab and nothing else.
            tab.apply(settings)
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
        self.setCentralWidget(self.tabs)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.heartbeat)
        self._timer.start(HEARTBEAT_MS)

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
        self.statusBar().showMessage(self.status_text())

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
        self.statusBar().showMessage(self.status_text())

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
        parts: list[str] = []

        try:
            state = self._services.state()
        except Exception:  # noqa: BLE001
            logger.exception("the services could not say what they are doing")
            parts.append("the services could not be asked what they are doing")
        else:
            # The recorder's own sentence, which says whether it died and was
            # restarted rather than only whether it is up - the treatment
            # detection has had from the start. `.get` twice, because the
            # services are handed in and one that answers only yes or no must
            # still produce a status line.
            recording = state.get("recording_state") or {}
            parts.append(
                recording.get("reason")
                or ("recording" if state.get("recording") else "NOT recording")
            )
            parts.append(f"streaming: {state.get('streaming')}")
            # `.get` twice: the services are handed in, and a state without a
            # word about detection must produce a status line, not a KeyError
            # that costs the operator the recording state as well.
            detection = state.get("detection") or {}
            parts.append(f"detection: {detection.get('reason', 'unknown')}")

        try:
            link = self._radio.status()
        except Exception:  # noqa: BLE001
            logger.exception("the radio could not be asked about the link")
            parts.append("link unknown")
        else:
            signal = link.get("signal_dbm")
            parts.append(f"link {signal} dBm" if signal is not None else "link -")

        return " · ".join(parts)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Close the window. Deliberately does not stop the children: recording
        outlives the interface, which is the point of running it separately."""
        self._timer.stop()
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
