"""The command sender, now that there are two things to command.

`PtzCommands` is a latest-value mailbox: the operator taps four arrows while one
command is on the wire, and replaying all four would have the head performing a
gesture that finished seconds ago, so only the last one is sent. That was right
while there was one thing to send.

There are two now. The camera turned out to be two lenses on one gimbal, and the
operator pans with the arrow keys while dragging a zoom slider. One shared
latest-value slot would have each of those throwing the other away - whichever
he touched last would happen and the other would silently not, which looks
exactly like a command lost over the radio link. That is the failure this
project has spent the most time chasing, and it would have been reintroduced
here by an optimisation that used to be correct.

So there is a mailbox per lane. What must survive that change, and is tested
below at least as hard as the new behaviour, is the guarantee the file was
written for:

    a stop is never dropped, and never waits behind anything avoidable.

Nothing here sleeps. Every wait is on an event with a bound, so a regression
fails rather than hangs the suite.
"""

from __future__ import annotations

import threading

import pytest

from vmd.ptz.service import PtzCommands, PtzService, ZoomHandle
from vmd.settings import Settings

WAIT = 5.0


class Gated:
    """A camera whose first command blocks until the test lets it go.

    Blocking is the whole point: coalescing can only be observed while
    something is on the wire, and on a real camera that window is a two-second
    round trip over the radio link. Here it is an event, so the test is
    deterministic and costs nothing.
    """

    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.in_flight = threading.Event()
        self.release = threading.Event()
        self.hold_first = True
        self._lock = threading.Lock()

    def _record(self, command: tuple) -> dict:
        with self._lock:
            self.sent.append(command)
            first = len(self.sent) == 1
        if first and self.hold_first:
            self.in_flight.set()
            self.release.wait(WAIT)
        return {"ok": True}

    def move(self, pan, tilt, zoom):
        return self._record(("move", pan, tilt, zoom))

    def stop(self):
        return self._record(("stop",))

    def home(self):
        return self._record(("home",))

    def zoom(self, stream, where):
        return self._record(("zoom", stream, where))

    def zoom_hold(self, stream, speed):
        return self._record(("zoom_hold", stream, speed))

    def zoom_poll(self):
        self._record(("zoom_poll",))

    def zoom_position(self, stream):
        return 0.25


@pytest.fixture
def gated():
    camera = Gated()
    commands = PtzCommands(camera, name="test")
    yield camera, commands
    camera.release.set()
    commands.close()


# ------------------------------------------------------- the lanes are separate


def test_a_zoom_does_not_throw_away_the_pan_waiting_behind_it(gated) -> None:
    """The regression a single mailbox would have introduced. He steers with the
    arrow keys and zooms with the slider, and both are true at once."""
    camera, commands = gated
    commands.move(1.0, 0.0, 0.0)
    assert camera.in_flight.wait(WAIT)

    commands.move(0.0, 1.0, 0.0)
    commands.zoom("thermal", 0.8)
    camera.release.set()
    assert commands.wait_until_idle(WAIT)

    assert ("move", 0.0, 1.0, 0.0) in camera.sent
    assert ("zoom", "thermal", 0.8) in camera.sent


def test_a_pan_does_not_throw_away_the_zoom_waiting_behind_it(gated) -> None:
    camera, commands = gated
    commands.zoom("visible", 0.1)
    assert camera.in_flight.wait(WAIT)

    commands.zoom("visible", 0.9)
    commands.move(1.0, 0.0, 0.0)
    camera.release.set()
    assert commands.wait_until_idle(WAIT)

    assert ("zoom", "visible", 0.9) in camera.sent
    assert ("move", 1.0, 0.0, 0.0) in camera.sent


def test_within_one_lane_only_the_last_thing_asked_for_is_sent(gated) -> None:
    """The behaviour that must not be lost: four arrows tapped while one command
    is on the wire is one gesture, not four."""
    camera, commands = gated
    commands.move(1.0, 0.0, 0.0)
    assert camera.in_flight.wait(WAIT)

    for tilt in (0.2, 0.4, 0.6):
        commands.move(0.0, tilt, 0.0)
    camera.release.set()
    assert commands.wait_until_idle(WAIT)

    moves = [command for command in camera.sent if command[0] == "move"]
    assert moves == [("move", 1.0, 0.0, 0.0), ("move", 0.0, 0.6, 0.0)], camera.sent


def test_the_two_zoom_controls_do_not_throw_each_others_commands_away(gated) -> None:
    """Both lenses share the zoom lane, and that is deliberate - they share one
    camera connection. What must not happen is one of them being dropped, so
    the lane holds the latest per lens rather than the latest overall.

    This is the one place the lane rule is not simply "last wins", and it is
    here because the two sliders are two different intentions about two
    different pictures.
    """
    camera, commands = gated
    commands.move(1.0, 0.0, 0.0)
    assert camera.in_flight.wait(WAIT)

    commands.zoom("thermal", 0.3)
    commands.zoom("visible", 0.7)
    camera.release.set()
    assert commands.wait_until_idle(WAIT)

    zooms = [command for command in camera.sent if command[0] == "zoom"]
    assert ("zoom", "visible", 0.7) in zooms
    assert ("zoom", "thermal", 0.3) in zooms, (
        "one lens's zoom was thrown away by the other's"
    )


# --------------------------------------------------------- the stop guarantee


def test_a_stop_is_still_never_dropped_when_a_zoom_is_waiting(gated) -> None:
    """The whole safety property of this file. A stop that does not arrive is a
    head left slewing with no key held, and adding a second lane must not have
    given it somewhere new to get lost."""
    camera, commands = gated
    commands.move(1.0, 0.0, 0.0)
    assert camera.in_flight.wait(WAIT)

    commands.zoom("thermal", 0.9)
    commands.stop()
    camera.release.set()
    assert commands.wait_until_idle(WAIT)

    assert ("stop",) in camera.sent


def test_steering_is_always_sent_before_a_zoom_that_was_asked_for_first(gated) -> None:
    """A stop may wait behind at most the one command already on the wire. If
    the zoom lane were drained first it could wait behind that as well, which on
    this link is another two seconds of a head that should have halted."""
    camera, commands = gated
    commands.move(1.0, 0.0, 0.0)
    assert camera.in_flight.wait(WAIT)

    commands.zoom("thermal", 0.9)  # asked for first...
    commands.stop()  # ...and this must still go out ahead of it
    camera.release.set()
    assert commands.wait_until_idle(WAIT)

    after = camera.sent[1:]
    assert after[0] == ("stop",), camera.sent


def test_a_stop_owed_when_the_console_closes_is_still_delivered() -> None:
    """Closing the window must not leave the head moving."""
    camera = Gated()
    camera.hold_first = False
    commands = PtzCommands(camera, name="test")
    commands.move(1.0, 0.0, 0.0)
    commands.stop()
    assert commands.close(WAIT)
    assert ("stop",) in camera.sent


def test_nothing_is_reported_idle_while_either_lane_still_owes_a_command(gated) -> None:
    camera, commands = gated
    commands.move(1.0, 0.0, 0.0)
    assert camera.in_flight.wait(WAIT)
    commands.zoom("thermal", 0.5)
    assert commands.wait_until_idle(0.2) is False
    camera.release.set()
    assert commands.wait_until_idle(WAIT)


# ------------------------------------------------- the service behind the lanes


def test_a_background_refresh_never_masks_a_failing_arrow_key(gated) -> None:
    """The console decides the camera has gone quiet from the LAST answer. A
    zoom readout refreshing successfully between two failed key presses would
    wipe that out, and the camera would look fine while nothing he pressed was
    working."""
    camera, commands = gated
    camera.hold_first = False
    commands.stop()
    assert commands.wait_until_idle(WAIT)
    said = commands.last_answer()

    commands.poll_zoom()
    assert commands.wait_until_idle(WAIT)
    assert ("zoom_poll",) in camera.sent, "the refresh never ran"
    assert commands.last_answer() == said, "a background refresh became the news"


def test_the_handle_a_zoom_bar_holds_returns_at_once(gated) -> None:
    """Every one of these is called from the thread that draws the window."""
    camera, commands = gated
    handle = ZoomHandle(commands)
    handle.go_to("thermal", 0.6)
    handle.creep("visible", 0.4)
    handle.poll()
    assert handle.position("thermal") == 0.25
    camera.release.set()
    assert commands.wait_until_idle(WAIT)
    assert ("zoom", "thermal", 0.6) in camera.sent
    assert ("zoom_hold", "visible", 0.4) in camera.sent


def test_a_console_with_no_camera_address_gets_a_sentence_and_not_a_crash() -> None:
    """The state every first run is in. This answer reaches a Qt handler, which
    is the one place in this program an exception must never arrive."""
    service = PtzService(Settings())
    assert service.zoom("thermal", 0.5)["ok"] is False
    assert service.zoom_hold("thermal", 0.4)["ok"] is False
    assert service.zoom_position("thermal") is None
    assert service.zoom_ready()["ok"] is False
    service.zoom_poll()  # must not raise


def test_reading_a_zoom_position_never_talks_to_the_camera() -> None:
    """It is called on every redraw of the window. If it crossed the link the
    console would freeze for two seconds at a time, which is the fault the whole
    command thread exists to prevent."""
    settings = Settings()
    settings.camera.host = "10.0.0.9"
    service = PtzService(settings)

    asked: list = []

    class Watched:
        def position(self, profile=None):
            asked.append(profile)
            return {"zoom": 0.5}

        def profiles(self):
            asked.append("profiles")
            return []

        capability = None

    service.lenses._camera = Watched()
    for _ in range(50):
        service.zoom_position("thermal")
    assert asked == []
