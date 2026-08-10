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
        self._buffer = attach(LogBuffer())

        try:
            settings = load_settings(self._settings_path)
        except Exception:  # noqa: BLE001 - an unreadable file is a Settings tab job
            logger.exception("the settings could not be read; using the defaults")
            settings = Settings()

        def build_live() -> QWidget:
            tab = LiveTab(ptz=ptz, make_pane=make_pane, local_url=services.local_url)
            # Built here rather than after the tabs are assembled so that a
            # stream that cannot be shown fails this tab and nothing else.
            tab.apply(settings)
            return tab

        def build_playback() -> QWidget:
            self._index = SegmentIndex(index_path)
            return PlaybackTab(index=self._index, pane=make_pane("playback"))

        def build_settings() -> QWidget:
            tab = SettingsTab(settings_path=self._settings_path)
            tab.load()
            return tab

        self.live = self._tab("Live", build_live)
        self.playback = self._tab("Playback", build_playback)
        self.settings_tab = self._tab("Settings", build_settings)
        self.logs = self._tab("Logs", lambda: LogsTab(self._buffer))

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
            parts.append("recording" if state.get("recording") else "NOT recording")
            parts.append(f"streaming: {state.get('streaming')}")

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
        if self._index is not None:
            try:
                self._index.close()
            except Exception:  # noqa: BLE001 - closing must not fail a close
                logger.exception("the segment index would not close")
        super().closeEvent(event)
