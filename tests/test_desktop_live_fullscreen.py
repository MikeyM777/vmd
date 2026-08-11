"""The fullscreen live mode, and the zoom bar under every picture.

Both come from the operator, in his own words: "I want to build a full screen
version of the app that presents only the live without all the side info, and
the PTZ still works in fullscreen", and "under the camera live, in a slick and
elegant way, a slider with + - buttons to zoom in and out for each camera."

Three of the tests here are not about the feature at all. They are about the two
ways this console has already been broken, and they exist so that a future
rearrangement of the Live tab cannot break them again quietly:

* **The picture must survive the mode change.** The panes hand libVLC an HWND.
  Moving one between two layouts, or into a window of its own, destroys that
  native window and gives the pane a new one - and what the operator gets is a
  black rectangle with a healthy frame counter and the word `playing` in green.
  So fullscreen is the same widget, in the same window, with the side column
  hidden, and `test_the_picture_surface_survives_entering_and_leaving_fullscreen`
  is what holds it to that.

* **The keyboard must survive it.** Steering is read off the Live tab, and focus
  moves when widgets are hidden and shown. A fullscreen console whose arrow keys
  go nowhere is worse than no fullscreen at all.

* **Nothing is laid over the pictures.** The zoom bars go UNDER each picture,
  never on it, for the reason written at the top of `vmd/desktop/live.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QSplitter, QWidget

from vmd.desktop.live import LiveTab
from vmd.desktop.video import FakeVideoPane
from vmd.desktop.window import ConsoleWindow
from vmd.desktop.zoombar import CREEP, UNKNOWN_CAPTION
from vmd.settings import CameraSettings, Settings, StreamSettings, save_settings

# The same bounded wait every assertion about the camera in this suite uses: PTZ
# commands leave the console on a thread of their own, so "what the camera was
# told" is a question with a moment's delay in it.
COMMAND_TIMEOUT = 10.0


class FakePtz:
    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def apply(self, settings) -> None: ...

    def status(self) -> dict:
        return {"available": True, "reason": "ready"}

    def move(self, pan: float, tilt: float, zoom: float) -> dict:
        self.commands.append(("move", pan, tilt, zoom))
        return {"ok": True}

    def stop(self) -> dict:
        self.commands.append(("stop",))
        return {"ok": True}

    def home(self) -> dict:
        self.commands.append(("home",))
        return {"ok": True}


class FakeZoom:
    """The lens, with its own answer per stream and its own memory of orders.

    Shaped exactly like the object `vmd/ptz/` is being written to hand in:
    `go_to(name, where)`, `creep(name, speed)` and `position(name)`. What makes
    it useful here is that what it REPORTS and what it was TOLD are two separate
    things, which is the difference a zoom readout has to keep.
    """

    def __init__(self, **positions: float | None) -> None:
        self.positions: dict[str, float | None] = dict(positions)
        self.told: list[tuple[str, float]] = []
        self.crept: list[tuple[str, float]] = []
        self.refuses = False

    def go_to(self, name: str, where: float) -> None:
        self.told.append((name, where))

    def creep(self, name: str, speed: float) -> None:
        self.crept.append((name, speed))

    def position(self, name: str) -> float | None:
        if self.refuses:
            raise RuntimeError("the camera did not answer")
        return self.positions.get(name)


class FakeServices:
    disk = None

    def apply(self, settings) -> None: ...

    def start(self) -> None: ...

    def tick(self) -> list[str]:
        return []

    def stop(self) -> None: ...

    def local_url(self, name: str) -> str:
        return f"rtsp://127.0.0.1:8554/{name}"

    def state(self) -> dict:
        return {
            "recording": True,
            "streaming": "streaming",
            "restarts": {},
            "detection": {"enabled": False, "running": False, "reason": "off"},
        }


class FakeRadio:
    def apply(self, settings) -> None: ...

    def status(self) -> dict:
        return {"connected": True, "age_seconds": 1.0, "signal_dbm": -63.0}


class WidgetPane(QWidget):
    """A pane that is a real widget and asks for a window handle, as libVLC's
    does. The handle is the whole point: it is what a reparent destroys."""

    def __init__(self) -> None:
        super().__init__()
        self.url: str | None = None
        self.restarts = 0
        self.at_seconds = 0.0
        self._state = "stopped"

    @property
    def state(self) -> str:
        return self._state

    def show(self, url: str, at_seconds: float = 0.0) -> None:  # noqa: A003
        if self.url is not None:
            self.restarts += 1
        self.url = url
        self.at_seconds = float(at_seconds)
        self._state = "connecting"
        self.attach()

    def attach(self) -> int:
        # What `VlcVideoPane._attach_surface` does.
        return int(self.winId())

    def stop(self) -> None:
        self.url = None
        self._state = "stopped"

    def pretend_playing(self) -> None:
        self._state = "playing"


def settings_with(*names: str) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name=name, url=f"rtsp://10.0.0.2/{name}", enabled=True)
                for name in names
            ],
        )
    )


def live_tab(qtbot, *names: str, zoom=None, pane=FakeVideoPane):
    """A Live tab on its own, with fakes for everything it reaches out to."""
    ptz = FakePtz()
    panes: dict = {}

    def make_pane(name: str):
        panes[name] = pane()
        return panes[name]

    tab = LiveTab(
        ptz=ptz,
        make_pane=make_pane,
        local_url=lambda name: f"rtsp://127.0.0.1:8554/{name}",
        zoom=zoom,
        executor=lambda work: work(),
    )
    qtbot.addWidget(tab)
    tab.apply(settings_with(*names))
    return tab, ptz, panes


def console(qtbot, tmp_path: Path, zoom=None, pane=FakeVideoPane) -> ConsoleWindow:
    """The real window, on the two streams this camera actually has."""
    path = tmp_path / "settings.json"
    settings = settings_with("thermal", "visible")
    settings.storage.root = tmp_path / "recordings"
    save_settings(settings, path)
    window = ConsoleWindow(
        settings_path=path,
        services=FakeServices(),
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: pane(),
        events_path=None,
    )
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    qtbot.waitExposed(window)
    return window


def settle() -> None:
    """Let Qt actually apply a window state change before it is measured."""
    for _ in range(3):
        QApplication.processEvents()


# ------------------------------------------------------------ the fullscreen mode


def test_fullscreen_shows_the_pictures_and_none_of_the_side_column(
    qtbot, tmp_path: Path
) -> None:
    """"Most of the time I don't care about all these numbers, I will want
    mainly to watch the stream on a big screen." So: the pictures, the chooser
    above them and the zoom bars under them, and nothing else at all."""
    window = console(qtbot, tmp_path)
    live = window.live

    window.fullscreen.enter()
    settle()

    assert window.isFullScreen()
    assert window.fullscreen.active()
    assert live.is_fullscreen()
    assert not live.side_visible(), "the side column is what he asked to be rid of"
    assert not window.band.isVisible(), "the status band is side info too"
    assert not window.tabs.tabBar().isVisible()
    for name in live.stream_names():
        assert live._frames[name].isVisible(), f"{name} is the point of the mode"


def test_leaving_fullscreen_puts_the_side_column_back(qtbot, tmp_path: Path) -> None:
    """The way back has to leave the console exactly as it was found. An
    operator who came out of fullscreen into a window with no tabs would have
    lost Settings and Logs, which are the only tools on this machine."""
    window = console(qtbot, tmp_path)
    live = window.live

    window.fullscreen.enter()
    settle()
    window.fullscreen.leave()
    settle()

    assert not window.isFullScreen()
    assert not window.fullscreen.active()
    assert not live.is_fullscreen()
    assert live.side_visible()
    assert window.band.isVisible()
    assert window.tabs.tabBar().isVisible()


def test_the_way_out_of_fullscreen_is_a_button_that_says_so(
    qtbot, tmp_path: Path
) -> None:
    """"Of course I want to be able to exit the fullscreen mode." An operator
    who cannot find his way out of a fullscreen console at three in the morning
    is a fault, not a preference - so the button is on the screen the whole
    time, it names the key as well, and pressing it is enough."""
    window = console(qtbot, tmp_path)
    live = window.live
    button = live.fullscreen_button()

    assert button.isVisible()
    window.fullscreen.enter()
    settle()

    assert button.isVisible(), "the way out may not be hidden by the mode"
    assert "Esc" in button.text(), button.text()
    assert "fullscreen" in button.text().lower(), button.text()

    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    settle()
    assert not window.fullscreen.active()


def test_escape_and_f11_leave_fullscreen_from_the_picture(
    qtbot, tmp_path: Path
) -> None:
    """Both keys, and both pressed where the operator's hands actually are -
    on the Live tab, which is what holds the keyboard while he is steering. The
    keys travel up to the window unhandled; nothing here is a Qt shortcut,
    because a shortcut that swallowed an arrow release would leave the head
    slewing with nobody watching."""
    window = console(qtbot, tmp_path)

    qtbot.keyClick(window.live, Qt.Key.Key_F11)
    settle()
    assert window.fullscreen.active(), "F11 has to get the operator in"

    qtbot.keyClick(window.live, Qt.Key.Key_Escape)
    settle()
    assert not window.fullscreen.active(), "Escape has to get him out"

    qtbot.keyClick(window.live, Qt.Key.Key_F11)
    settle()
    qtbot.keyClick(window.live, Qt.Key.Key_F11)
    settle()
    assert not window.fullscreen.active(), "F11 has to get him out too"


def test_steering_still_works_in_fullscreen(qtbot, tmp_path: Path) -> None:
    """The one thing that is easy to lose here and expensive to lose in the
    field: focus moves when widgets are hidden, and a fullscreen console whose
    arrow keys go nowhere is a camera the operator cannot point."""
    window = console(qtbot, tmp_path)
    live = window.live

    window.fullscreen.enter()
    settle()

    qtbot.keyPress(live, Qt.Key.Key_Right)
    assert live.wait_for_camera(COMMAND_TIMEOUT)
    assert window._ptz.commands[-1] == ("move", 0.5, 0.0, 0.0)
    qtbot.keyRelease(live, Qt.Key.Key_Right)
    assert live.wait_for_camera(COMMAND_TIMEOUT)
    assert window._ptz.commands[-1] == ("stop",)

    # And the tab is the thing the keyboard is actually on, rather than the
    # keys having reached it by luck through some other focused widget.
    assert live.hasFocus() or live.isAncestorOf(QApplication.focusWidget())


def test_dragging_a_picture_still_steers_in_fullscreen(qtbot, tmp_path: Path) -> None:
    """The other half of "the PTZ still works in fullscreen": the mouse. It is
    read off the panes through an event filter, and a pane that had been moved
    into another window would no longer be filtered by this tab."""
    window = console(qtbot, tmp_path, pane=WidgetPane)
    live = window.live
    window.fullscreen.enter()
    settle()

    picture = live._panes["thermal"]
    right = QPoint(int(picture.width() * 0.97), int(picture.height() * 0.5))
    qtbot.mousePress(picture, Qt.MouseButton.LeftButton, pos=right)
    assert live.wait_for_camera(COMMAND_TIMEOUT)
    assert window._ptz.commands[-1][0] == "move"
    assert window._ptz.commands[-1][1] > 0.0, "a drag at the right edge pans right"
    qtbot.mouseRelease(picture, Qt.MouseButton.LeftButton, pos=right)


def test_the_view_chooser_is_the_same_one_and_still_chooses(
    qtbot, tmp_path: Path
) -> None:
    """"Like the regular view, the option to choose all / vis / thermal." The
    same control, not a second one built for this mode - two choosers would be
    two answers to "which view is on the wall"."""
    window = console(qtbot, tmp_path)
    live = window.live
    chooser = live.views

    window.fullscreen.enter()
    settle()

    assert live.views is chooser
    assert chooser.isVisible()
    assert chooser.labels() == ["All", "thermal", "visible"]
    live.show_view("thermal")
    assert live.shown_streams() == ["thermal"]
    live.show_view("")
    assert live.shown_streams() == ["thermal", "visible"]


def test_the_pictures_can_be_given_different_amounts_of_room(
    qtbot, tmp_path: Path
) -> None:
    """"Being able to resize the windows because the thermal is a smaller window
    than the vis." A splitter, and a share that survives the mode change - a
    console that reset it every time he went fullscreen would make him do it
    again every time."""
    window = console(qtbot, tmp_path)
    live = window.live
    assert isinstance(live._wall, QSplitter)
    assert live._wall.count() == 2

    live._wall.setSizes([300, 900])
    settle()
    assert live._wall.sizes()[0] < live._wall.sizes()[1]

    window.fullscreen.enter()
    settle()
    assert live._wall.sizes()[0] < live._wall.sizes()[1], (
        "the thermal was given back room the operator took away from it"
    )
    window.fullscreen.leave()
    settle()
    assert live._wall.sizes()[0] < live._wall.sizes()[1]


def test_the_picture_surface_survives_entering_and_leaving_fullscreen(
    qtbot, tmp_path: Path
) -> None:
    """This test is the reason the mode is built the way it is.

    The panes hand libVLC a window handle. Qt destroys and recreates that native
    window when a widget changes top-level window - so a fullscreen mode built
    as a SECOND window with the panes moved into it leaves libVLC drawing into a
    handle that no longer exists. What the operator sees is the worst failure
    this console has: a black rectangle, a frame counter that keeps counting,
    and the word `playing` in green.

    So: the same widget, in the same window, with the side column hidden. What
    that buys is asserted here - the pane is the same object, under the same
    parent, on the same native window, inside the same TOP-LEVEL window, and it
    was never told to start again.

    The top-level is the assertion that does the work, and it is here because
    the obvious one does not: Qt 6 on Windows re-parents a native child with
    `SetParent` rather than by destroying it, so a pane moved into a window of
    its own keeps the very handle this test was first written to watch. What
    changes, and what libVLC's surface actually hangs off, is which window that
    handle belongs to - so that is what is measured.
    """
    window = console(qtbot, tmp_path, pane=WidgetPane)
    live = window.live
    for pane in live._panes.values():
        assert pane.attach(), "the pane never asked for a window handle"

    before = {
        name: (
            pane,
            pane.parentWidget(),
            pane.window(),
            int(pane.internalWinId()),
            pane.restarts,
        )
        for name, pane in live._panes.items()
    }

    window.fullscreen.enter()
    settle()
    for name, (_pane, _parent, top, handle, _restarts) in before.items():
        now = live._panes[name]
        assert now.window() is top, (
            f"{name} was moved into another top-level window on the way IN; "
            "libVLC's surface belongs to the window it was given, and what the "
            "operator gets is a black picture that still reports playing"
        )
        assert int(now.internalWinId()) == handle, f"{name} was given a new handle"

    window.fullscreen.leave()
    settle()

    for name, (pane, parent, top, handle, restarts) in before.items():
        now = live._panes[name]
        assert now is pane, f"{name} was rebuilt"
        assert now.parentWidget() is parent, f"{name} was moved into another widget"
        assert now.window() is top, f"{name} came back into a different window"
        assert int(now.internalWinId()) == handle, (
            f"{name} was given a new window handle; libVLC is still drawing into "
            "the old one, and the picture is black while the state says playing"
        )
        assert now.restarts == restarts, f"{name} was restarted by the mode change"
        assert now.url is not None


def test_nothing_is_laid_over_the_pictures_in_fullscreen(
    qtbot, tmp_path: Path
) -> None:
    """The rule from `vmd/desktop/live.py`, asked again in the new mode.

    Once a pane asks for a window handle every widget on the tab becomes a
    native window of its own, and a native window over the video can hide it
    outright whatever its stylesheet says. The zoom bars and the way out of
    fullscreen therefore sit under and above the pictures, never on them.
    """
    window = console(qtbot, tmp_path, pane=WidgetPane)
    live = window.live
    window.fullscreen.enter()
    settle()

    def ancestors(widget) -> set:
        seen = set()
        parent = widget.parentWidget()
        while parent is not None:
            seen.add(parent)
            parent = parent.parentWidget()
        return seen

    covered: list[str] = []
    for name, picture in live._panes.items():
        if not picture.isVisible():
            continue
        above = ancestors(picture)
        for other in live.findChildren(QWidget):
            if other is picture or other in above or not other.isVisible():
                continue
            if picture in ancestors(other):
                continue
            here = picture.rect().translated(picture.mapToGlobal(QPoint(0, 0)))
            there = other.rect().translated(other.mapToGlobal(QPoint(0, 0)))
            if here.intersects(there):
                covered.append(f"{type(other).__name__} covers {name}")
    assert not covered, covered


def test_a_console_whose_live_tab_would_not_build_still_opens(
    qtbot, tmp_path: Path
) -> None:
    """Every tab in this window may be a label saying why it could not be built,
    and the fullscreen mode may not be the thing that changes that. It is asked
    of the tab, and a tab that cannot answer simply is not put into it."""
    path = tmp_path / "settings.json"
    save_settings(settings_with("thermal"), path)

    def refuses(name: str):
        raise RuntimeError("libVLC is not installed")

    window = ConsoleWindow(
        settings_path=path,
        services=FakeServices(),
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=refuses,
        events_path=None,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    window.fullscreen.enter()
    settle()
    window.fullscreen.leave()
    settle()
    assert not window.fullscreen.active()
    assert window.tabs.tabBar().isVisible()


# ---------------------------------------------------------------- the zoom bars


def test_every_picture_has_a_zoom_bar_under_it(qtbot) -> None:
    """"Under the camera live, a slider with + - buttons to zoom in and out for
    each camera." One per picture, in the picture's own frame, so that which
    slider belongs to which lens is not something anybody has to work out."""
    tab, _ptz, _panes = live_tab(
        qtbot, "thermal", "visible", zoom=FakeZoom(), pane=WidgetPane
    )
    tab.resize(900, 600)
    tab.show()
    qtbot.waitExposed(tab)

    for name in ("thermal", "visible"):
        bar = tab.zoom_bar(name)
        assert bar is not None, f"{name} has no zoom"
        assert bar.name() == name
        assert bar.isVisible()
        pane = tab._panes[name]
        assert bar.mapToGlobal(QPoint(0, 0)).y() >= pane.mapToGlobal(
            QPoint(0, pane.height())
        ).y(), "the bar belongs under the picture, never over it"


def test_the_zoom_bars_are_under_the_pictures_in_fullscreen_too(
    qtbot, tmp_path: Path
) -> None:
    """"Do it also for the regular non-fullscreen view" - which is to say he
    asked for it in fullscreen first. It is the mode where the numbers in the
    side column are gone, so the one readout left has to be there."""
    window = console(qtbot, tmp_path)
    live = window.live
    window.fullscreen.enter()
    settle()
    for name in live.stream_names():
        bar = live.zoom_bar(name)
        assert bar is not None and bar.isVisible()


def test_each_bar_asks_for_its_own_lens_and_not_the_other(qtbot) -> None:
    """The fault this replaces: one zoom control on a camera with two lenses,
    sent to whichever profile the device handed back first."""
    zoom = FakeZoom(thermal=0.2, visible=0.2)
    tab, _ptz, _panes = live_tab(qtbot, "thermal", "visible", zoom=zoom)
    tab.refresh()

    tab.zoom_bar("thermal").slider().setValue(90)
    assert zoom.told == [("thermal", 0.9)]
    assert tab.zoom_bar("visible").position() == 0.2, "the other lens moved"


def test_holding_a_button_on_a_camera_with_no_readout_creeps_and_stops(
    qtbot,
) -> None:
    """A camera that will not say where its zoom is can still be zoomed, and the
    stop at the end of the hold is the half that must never be lost."""
    zoom = FakeZoom()
    tab, _ptz, _panes = live_tab(qtbot, "thermal", zoom=zoom)
    tab.refresh()

    out, into = tab.zoom_bar("thermal").buttons()
    into.pressed.emit()
    into.released.emit()
    assert zoom.crept == [("thermal", CREEP), ("thermal", 0.0)]


def test_the_bar_draws_what_the_camera_reports_and_never_what_it_was_told(
    qtbot,
) -> None:
    """The shortcut this whole control exists to refuse. A slider drawn from the
    command that was sent is right until the first command that does not arrive,
    and looks right for ever afterwards - which is exactly the state the
    operator needs to be able to tell apart from a lens at its stop."""
    zoom = FakeZoom(thermal=0.20)
    tab, _ptz, _panes = live_tab(qtbot, "thermal", zoom=zoom)
    tab.refresh()
    assert tab.zoom_bar("thermal").position() == pytest.approx(0.20)

    # Asked for, and then asked for again on the next heartbeat. Both, because
    # the lie this refuses is a readout that jumps to where the lens was told to
    # go and stays there until something else corrects it - and the two seconds
    # between heartbeats is exactly the window in which the operator looks.
    tab.zoom_bar("thermal").go_to.emit("thermal", 0.90)
    assert zoom.told == [("thermal", 0.90)], "the order never reached the lens"
    assert tab.zoom_bar("thermal").position() == pytest.approx(0.20), (
        "the bar moved itself to where it had asked the lens to go"
    )
    tab.refresh()
    assert tab.zoom_bar("thermal").position() == pytest.approx(0.20), (
        "the bar believed a command instead of the camera"
    )

    # And it follows the camera when the camera actually moves.
    zoom.positions["thermal"] = 0.90
    tab.refresh()
    assert tab.zoom_bar("thermal").position() == pytest.approx(0.90)


def test_a_console_with_no_zoom_yet_draws_the_bars_honestly(qtbot) -> None:
    """Nothing is wired to the lenses on a console started without them, and a
    slider sitting at zero would be saying the lens is fully wide."""
    tab, _ptz, _panes = live_tab(qtbot, "thermal", zoom=None)
    tab.refresh()
    bar = tab.zoom_bar("thermal")
    assert bar.position() is None
    assert bar.caption() == UNKNOWN_CAPTION
    assert not bar.isEnabled(), "a control that reaches nothing may not look live"


def test_a_camera_that_will_not_say_where_its_zoom_is_costs_only_the_readout(
    qtbot,
) -> None:
    """The rule every reading on this tab follows: what cannot be read costs
    that reading and nothing else. The pictures are not downstream of the zoom."""
    zoom = FakeZoom(thermal=0.4)
    tab, _ptz, panes = live_tab(qtbot, "thermal", zoom=zoom)
    tab.refresh()
    assert tab.zoom_bar("thermal").position() == pytest.approx(0.4)

    zoom.refuses = True
    tab.refresh()
    assert tab.zoom_bar("thermal").position() is None
    assert tab.zoom_bar("thermal").caption() == UNKNOWN_CAPTION
    assert panes["thermal"].url is not None, "the picture went with the readout"


def test_a_camera_that_stops_answering_is_said_once_and_not_every_heartbeat(
    qtbot, caplog
) -> None:
    """This runs every two seconds for months, and the Logs tab holds five
    hundred lines. A reading that fails on every beat would evict everything
    that explains the fault inside four minutes - the lesson `_say_it_failed`
    already learnt one level up."""
    zoom = FakeZoom(thermal=0.4)
    tab, _ptz, _panes = live_tab(qtbot, "thermal", zoom=zoom)
    zoom.refuses = True
    with caplog.at_level("WARNING"):
        for _ in range(20):
            tab.refresh()
    said = [record for record in caplog.records if "zoom" in record.getMessage().lower()]
    assert len(said) == 1, [record.getMessage() for record in said]


def test_the_zoom_bars_go_with_the_streams_they_belong_to(qtbot) -> None:
    """A Save that removes a stream removes its lens with it. A bar left behind
    is a control pointing at a camera view that no longer exists."""
    tab, _ptz, _panes = live_tab(qtbot, "thermal", "visible", zoom=FakeZoom())
    assert tab.zoom_bar("visible") is not None

    tab.apply(settings_with("thermal"))
    assert tab.zoom_bar("visible") is None
    assert tab.zoom_bar("thermal") is not None
    tab.refresh()
