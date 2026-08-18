"""Start the console: `python -m vmd.desktop`, or double-click VMD.exe.

Everything except making a QApplication and running it is a plain function that
can be called without a display - `parse_args`, `build_wiring`, `pane_factory`.
That is deliberate: the failures this file has to survive (no settings file, no
go2rtc, no libVLC) all happen on a field laptop, and none of them should have to
wait for someone to open a window to find out about.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from vmd.desktop.logs import LogBuffer, attach
from vmd.desktop.services import ConsoleServices, DetectorProcess, RecorderProcess
from vmd.desktop.style import stylesheet
from vmd.desktop.video import DEFAULT_DELAY_MS, PaneState, VideoPane, VlcVideoPane
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
    parser.add_argument(
        "--screen",
        type=int,
        default=None,
        metavar="N",
        help="open on screen N, counting from 1 (for one console per monitor)",
    )
    return parser.parse_args(argv)


def place_on_screen(window, number: int | None, screens: list) -> bool:
    """Open this console on the monitor its shortcut named. Says whether it did.

    There are two of these running on one desktop now, one camera each, on two
    monitors - and which window lands on which monitor cannot be left to
    whichever Windows opens first. The shortcut for each camera says which
    screen it belongs on, and this puts it there.

    It beats the remembered geometry deliberately: `_restore_geometry` has
    already run by the time this is called, so a window dragged somewhere odd
    yesterday still comes back where the installation says it belongs. Without
    `--screen` nothing here happens at all and the memory is the whole answer.

    A number naming a screen that is not there is a warning and nothing else. A
    monitor that did not wake up must not cost the operator the console - and a
    window placed off the end of the desktop is a console nobody can find.
    """
    if number is None:
        return False
    if not 1 <= number <= len(screens):
        logger.warning(
            "this console was told to open on screen %s, but this machine has "
            "%s; opening where it was left instead",
            number,
            len(screens),
        )
        return False
    window.setGeometry(screens[number - 1])
    return True


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

    def show(self, url: str, at_seconds: float = 0.0) -> None:  # noqa: A003
        logger.debug(
            "not showing %s from %.1f s in: this pane could not be built", url, at_seconds
        )

    def stop(self) -> None:
        return None

    @property
    def state(self) -> PaneState:
        # Never "failed": the Live tab restarts a failed pane, and rebuilding
        # this one would fail again every two seconds, forever.
        return "stopped"


class PaneOptions:
    """The one thing about a video pane that the operator can change while the
    console is open: how far behind the camera the picture runs.

    An object rather than a number, because `make_pane(name)` is built once when
    the window is and called again every time a save rebuilds the wall. Held by
    value, the delay a pane was built with would be the delay the console
    started with, for ever - and an operator who moved the setting, pressed
    Save, watched the pictures visibly restart and saw no change would
    reasonably conclude the setting does nothing. It did not do nothing before;
    it did not exist. Doing nothing quietly would be worse.
    """

    def __init__(
        self,
        delay_ms: int = DEFAULT_DELAY_MS,
        flip: bool = False,
        boxes: bool = False,
    ) -> None:
        self.delay_ms = int(delay_ms)
        self.flip = bool(flip)
        self.boxes = bool(boxes)


def pane_factory(
    build: Callable[..., VideoPane] | None = None,
    options: PaneOptions | None = None,
) -> Callable[[str], VideoPane]:
    """A `make_pane(name)` for the window, which never raises."""
    build = build or VlcVideoPane
    options = options if options is not None else PaneOptions()

    # Asked once, of the signature, rather than by trying it and catching
    # TypeError: a TypeError raised from inside a real pane's constructor would
    # be indistinguishable from one raised by the call itself, and the fallback
    # would quietly build a second pane on top of a half-built first.
    takes_delay = _accepts_delay(build)

    def make_pane(name: str) -> VideoPane:
        try:
            return (
                build(delay_ms=options.delay_ms, flip=options.flip, boxes=options.boxes)
                if takes_delay
                else build()
            )
        except Exception as exc:  # noqa: BLE001 - a console with no video still helps
            logger.exception("the video pane for %s could not be built", name)
            return BrokenPane(f"{name}: {exc}")

    return make_pane


def _accepts_delay(build: Callable[..., VideoPane]) -> bool:
    """Whether this pane can be told how far behind to run.

    Fakes in the tests take nothing, and so did every pane before the delay
    became a setting. A pane that cannot be told simply is not told.
    """
    try:
        return "delay_ms" in inspect.signature(build).parameters
    except (TypeError, ValueError):
        # Builtins and C-implemented callables have no signature to read. Not
        # something this console ever passes, but a pane is not worth an
        # exception on the way to drawing one.
        return False


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
    # Before the services, because the services hold the loop that keeps the
    # camera's bitrate inside what the link is carrying - and that needs both of
    # them: the radio to read and the camera to write to. One object each, not
    # two: a second RadioService would log in to the radio a second time, and a
    # second PtzService would hold a second connection to a camera that hands
    # out very few of them.
    ptz = PtzService(settings)
    radio = RadioService(settings)
    services = ConsoleServices(
        settings=settings,
        settings_path=settings_path,
        streaming=streaming,
        recorder=RecorderProcess(settings_path),
        # Built whether or not detection is enabled: ConsoleServices decides
        # whether to supervise it, and building it costs nothing but an object.
        detector=DetectorProcess(settings_path),
        ptz=ptz,
        radio=radio,
    )
    return Wiring(
        settings_path=settings_path,
        index_path=Path(settings.storage.root) / "segments.db",
        # Beside the segment index, because the two are reclaimed together.
        events_path=Path(settings.storage.root) / "events.db",
        services=services,
        ptz=ptz,
        radio=radio,
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

    # Shared with the window, which writes the saved delay into it before the
    # Live tab rebuilds its panes. One object, so the two cannot disagree.
    panes = PaneOptions(
        settings.live_delay_ms, flip=settings.flip_video, boxes=settings.show_boxes
    )

    window = ConsoleWindow(
        settings_path=wiring.settings_path,
        services=wiring.services,
        ptz=wiring.ptz,
        radio=wiring.radio,
        index_path=wiring.index_path,
        make_pane=pane_factory(options=panes),
        events_path=wiring.events_path,
        log_buffer=log_buffer,
        panes=panes,
    )
    # After the window is built, because building it is what restores the
    # remembered geometry, and before it is shown, so it never appears on one
    # monitor and jumps to another.
    # The command line first, then the settings file, then neither - which
    # leaves the remembered window as the whole answer. The command line wins so
    # that a console can be put on the other screen once without editing
    # anything, which is what somebody standing at the machine will want.
    place_on_screen(
        window,
        args.screen if args.screen is not None else settings.screen,
        [screen.availableGeometry() for screen in app.screens()],
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
