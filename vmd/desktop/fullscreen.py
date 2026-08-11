"""Fullscreen live: the pictures on the whole screen, and the way back out.

He asked for it in one sentence - "I want a full screen version of the app that
presents only the live without all the side info, and the PTZ still works in
fullscreen" - and then said why: "most of the time I don't care about all these
numbers, I will want mainly to watch the stream on a big screen."

**This is a mode, not a window.** Everything here hides chrome on the console
that is already open and asks it to fill the screen. Nothing is reparented and
nothing is rebuilt. That is not a convenience, it is the whole design, and the
reason is in `vmd/desktop/live.py`: the panes hand libVLC an HWND, and a widget
that changes top-level window is given a new native window by Qt while libVLC
goes on drawing into the old one. What the operator gets from that is the worst
failure this console has - a black rectangle, a frame counter still counting,
and the word `playing` in green beside it. A second window with the pictures
moved into it is exactly that bug, so there is no second window.

**The way out is never hidden.** Two keys and a button, and the button says
which key: `Esc` and `F11` both leave, `F11` also enters, and the button sits in
the row above the pictures in both modes. An operator who cannot find his way
out of a fullscreen console at three in the morning is a fault, not a
preference, and he has no second machine to fix it from.

The keys are read from `ConsoleWindow.keyPressEvent` rather than bound as Qt
shortcuts, which is the rule `vmd/desktop/live.py` already states for the number
keys: a shortcut takes the key out of the ordinary delivery, and this window
steers a camera with keys that are HELD. Nothing here may ever be in a position
to swallow the release of an arrow.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal

logger = logging.getLogger(__name__)

# The keys that toggle the mode and the key that only ever leaves it. Escape
# leaves and never enters: it is the key everybody presses to get out of
# something, and a console that went fullscreen because somebody dismissed a
# thought would be the opposite of what it is for.
TOGGLE_KEY = int(Qt.Key.Key_F11)
LEAVE_KEY = int(Qt.Key.Key_Escape)


class FullscreenLive(QObject):
    """The console with everything but the pictures taken away.

    It owns four things and no more: the status band, the tab bar, the Live
    tab's own side column, and the shape of the window. Each of them is put back
    exactly as it was found - an operator who came out of fullscreen into a
    window with no tabs would have lost Settings and Logs, which on this machine
    are the only tools there are.

    The Live tab is asked rather than told: every tab in this console may be a
    label saying why it could not be built, and a mode that raised on one of
    those would take the window down with it.
    """

    #: Entered (True) or left (False), for anything that draws itself
    #: differently in the two.
    changed = Signal(bool)

    def __init__(self, window, tabs, band, live, parent: QObject | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self._tabs = tabs
        self._band = band
        self._live = live
        self._active = False
        # What to put back on the way out: which page was open, and whether the
        # window was maximised. Restoring the page is what makes `F11` from the
        # Logs tab a round trip rather than a one-way door.
        self._was_page = 0
        self._was_maximised = False

    def active(self) -> bool:
        return self._active

    def set_active(self, wanted: bool) -> None:
        """Enter or leave, from a control that knows which it wants."""
        if wanted:
            self.enter()
        else:
            self.leave()

    def toggle(self) -> None:
        self.set_active(not self._active)

    def handle_key(self, key: int) -> bool:
        """Whether this key belongs to the mode, having acted on it if it does.

        Called from the window's key handler, so that the keys arrive by the
        same route as every other key on this console and cannot pre-empt one
        that is being held down.
        """
        if key == TOGGLE_KEY:
            self.toggle()
            return True
        if key == LEAVE_KEY and self._active:
            self.leave()
            return True
        return False

    # ------------------------------------------------------------------- in
    def enter(self) -> None:
        if self._active:
            return
        self._was_page = self._tabs.currentIndex()
        self._was_maximised = bool(self._window.isMaximized())
        # The pictures are the point of the mode, so the pictures are what is on
        # screen when it starts - whichever page the operator happened to be on.
        page = self._tabs.indexOf(self._live)
        if page >= 0:
            self._tabs.setCurrentIndex(page)
        self._active = True
        self._dress()
        self._window.showFullScreen()
        self._take_the_keyboard()
        self.changed.emit(True)

    # ------------------------------------------------------------------ out
    def leave(self) -> None:
        if not self._active:
            return
        self._active = False
        self._dress()
        if self._was_maximised:
            self._window.showMaximized()
        else:
            self._window.showNormal()
        if 0 <= self._was_page < self._tabs.count():
            self._tabs.setCurrentIndex(self._was_page)
        self._take_the_keyboard()
        self.changed.emit(False)

    # --------------------------------------------------------------- the work
    def _dress(self) -> None:
        """Show or hide everything that is not a picture.

        One method for both directions, because the two lists have to be the
        same list: a thing hidden on the way in and forgotten on the way out is
        a console the operator cannot get back.
        """
        showing = not self._active
        self._band.setVisible(showing)
        bar = getattr(self._tabs, "tabBar", None)
        if bar is not None:
            bar().setVisible(showing)
        # The Live tab hides its own side column, because what counts as side
        # info is its business and not this object's. A tab that could not be
        # built has no such method, and then this mode is simply a bigger
        # version of whatever went wrong - which is still better than a window
        # that will not open.
        fullscreen = getattr(self._live, "set_fullscreen", None)
        if fullscreen is None:
            return
        try:
            fullscreen(self._active)
        except Exception:  # noqa: BLE001 - the mode must not cost the window
            logger.exception("the Live tab would not change to fullscreen")

    def _take_the_keyboard(self) -> None:
        """Put the keyboard back on the pictures.

        The arrow keys steer, and they are read by the Live tab itself. Hiding
        and showing widgets moves the focus, so a mode change that did not say
        where the keyboard belongs would leave the operator's next arrow key
        going nowhere - which on a fullscreen console with nothing else on the
        screen looks exactly like a camera that has stopped answering.
        """
        try:
            self._live.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:  # noqa: BLE001 - the mode changed either way
            logger.exception("the keyboard could not be put back on the pictures")
