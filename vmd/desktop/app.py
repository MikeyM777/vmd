"""Start the console: `python -m vmd.desktop`, or double-click VMD.exe.

Everything except making a QApplication and running it is a plain function that
can be called without a display - `parse_args`, `build_wiring`, `pane_factory`.
That is deliberate: the failures this file has to survive (no settings file, no
go2rtc, no libVLC) all happen on a field laptop, and none of them should have to
wait for someone to open a window to find out about.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from vmd.desktop.logs import LogBuffer, attach
from vmd.desktop.services import ConsoleServices, DetectorProcess, RecorderProcess
from vmd.desktop.style import stylesheet
from vmd.desktop.video import PaneState, VideoPane, VlcVideoPane
from vmd.desktop.window import ConsoleWindow
from vmd.ptz.service import PtzService
from vmd.radio.service import RadioService
from vmd.settings import Settings, SettingsError, load_settings
from vmd.streaming.go2rtc import Go2rtcService, find_binary

logger = logging.getLogger("vmd.desktop")


def default_settings_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "settings.json"
    return Path("settings.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vmd", description="VMD console")
    parser.add_argument("--settings", default=str(default_settings_path()))
    parser.add_argument(
        "--no-services",
        action="store_true",
        help="do not start go2rtc or the recorder",
    )
    return parser.parse_args(argv)


class BrokenPane(QLabel):
    """What a video pane becomes when it cannot be built.

    libVLC is a separate installation with its own architecture and version, and
    when it is missing or mismatched `VlcVideoPane` raises on construction. That
    must not take the console with it: the operator still needs Settings to fix
    the address and Logs to read why, and both are behind the same window.

    It answers the whole VideoPane protocol so that nothing calling it has to
    know: showing is a no-op, and it is honest about never playing anything.
    """

    def __init__(self, reason: str, parent: QWidget | None = None) -> None:
        super().__init__(f"No video here: {reason}", parent)
        self.setWordWrap(True)
        self.setMargin(16)

    def show(self, url: str) -> None:  # noqa: A003 - the protocol's name
        logger.debug("not showing %s: this pane could not be built", url)

    def stop(self) -> None:
        return None

    @property
    def state(self) -> PaneState:
        # Never "failed": the Live tab restarts a failed pane, and rebuilding
        # this one would fail again every two seconds, forever.
        return "stopped"


def pane_factory(build: Callable[[], VideoPane] | None = None) -> Callable[[str], VideoPane]:
    """A `make_pane(name)` for the window, which never raises."""
    build = build or VlcVideoPane

    def make_pane(name: str) -> VideoPane:
        try:
            return build()
        except Exception as exc:  # noqa: BLE001 - a console with no video still helps
            logger.exception("the video pane for %s could not be built", name)
            return BrokenPane(f"{name}: {exc}")

    return make_pane


@dataclass
class Wiring:
    """Everything the window is handed, built from settings and nothing else."""

    settings_path: Path
    index_path: Path
    events_path: Path
    services: ConsoleServices
    ptz: PtzService
    radio: RadioService


def build_wiring(
    settings: Settings, settings_path: str | Path, with_services: bool = True
) -> Wiring:
    """Assemble the console's parts. Starts nothing and opens no window."""
    settings_path = Path(settings_path)
    streaming = None
    if with_services:
        streaming = Go2rtcService(
            settings,
            config_path=settings_path.parent / "go2rtc.json",
            binary=find_binary(),
        )
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(settings_path),
        # Built whether or not detection is enabled: ConsoleServices decides
        # whether to supervise it, and building it costs nothing but an object.
        detector=DetectorProcess(settings_path),
    )
    return Wiring(
        settings_path=settings_path,
        index_path=Path(settings.storage.root) / "segments.db",
        # Beside the segment index, because the two are reclaimed together.
        events_path=Path(settings.storage.root) / "events.db",
        services=services,
        ptz=PtzService(settings),
        radio=RadioService(settings),
    )


def start_logging() -> LogBuffer:
    """Put the Logs tab's buffer on the root logger before anything can log.

    First, and before the services are started, because the buffer used to be
    attached inside `ConsoleWindow.__init__` - which runs after `services.start()`
    - and everything said in between went nowhere at all. That is not a quiet
    stretch: it is where "a streaming server is already running; adopting it",
    "recorder: adopted from an earlier run", "go2rtc is not installed - run
    install.bat", "go2rtc exited immediately" and "could not start the recorder"
    are said. Every one of them is a message the Logs tab exists for, on a
    machine whose operator has no terminal and no second screen.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return attach(LogBuffer())


def load_or_default(settings_path: Path) -> Settings:
    """The settings, or the defaults and a line in the log saying why not.

    Refusing to open was the old answer, and it is unrecoverable for the person
    this is built for: the only tool that can fix settings.json is the Settings
    tab, which is inside the console that just refused to open, and the operator
    has no terminal and no second machine. It became more likely the day
    `StreamSettings.url` started validating its scheme, when one bad saved
    address could stop the console starting at all.

    The defaults have no streams, so nothing is started against them: the window
    opens on a console that is doing nothing, with the reason in the Logs tab
    and the Settings tab ready to be corrected and saved.
    """
    try:
        return load_settings(settings_path)
    except SettingsError:
        logger.exception(
            "the settings file %s could not be read; opening with the defaults so "
            "that it can be corrected in the Settings tab and saved",
            settings_path,
        )
        return Settings()


def main(argv: list[str] | None = None) -> int:
    log_buffer = start_logging()
    args = parse_args(argv)
    settings_path = Path(args.settings)
    settings = load_or_default(settings_path)

    wiring = build_wiring(settings, settings_path, with_services=not args.no_services)
    if not args.no_services:
        # Before the window, not after: go2rtc chooses its ports as it starts,
        # and the panes are pointed at those ports when the Live tab is built.
        wiring.services.start()

    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())

    window = ConsoleWindow(
        settings_path=wiring.settings_path,
        services=wiring.services,
        ptz=wiring.ptz,
        radio=wiring.radio,
        index_path=wiring.index_path,
        make_pane=pane_factory(),
        events_path=wiring.events_path,
        log_buffer=log_buffer,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
