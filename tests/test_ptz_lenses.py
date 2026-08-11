"""Per-lens zoom, and the traffic it is allowed to put on the link.

Two separate promises are tested here and the second is the one that would have
been quietly broken.

The first is ordinary: the thermal zoom reaches the thermal lens, a camera that
cannot be sent to a position is nudged instead of lied to, and letting go of a
zoom button does not halt a pan the operator is still holding.

The second is about the link. This camera sits at the far end of a Ubiquiti hop
measured at 88% of its airtime while carrying the video, with ONVIF replies
taking two seconds. A zoom readout refreshed on the console's two-second
heartbeat is two more SOAP round trips every two seconds, for ever, to redraw a
number that only changes when somebody touches the zoom - the console degrading
the picture it exists to show. Nothing about that failure is visible in a
screenshot or in a green test suite, which is exactly why it is pinned here: the
tests below count the calls.
"""

from __future__ import annotations

from vmd.ptz.lenses import CREEP_SPEED, SETTLING_EVERY, SETTLING_SECONDS, Lenses
from vmd.ptz.onvif import Profile, PtzCapability, PtzError

STREAMS = ["thermal", "visible"]

TWO_LENSES = [
    Profile(token="p-ir", name="Thermal", source="src1"),
    Profile(token="p-vis", name="Visible light", source="src0"),
]
ONE_LENS = [Profile(token="only", name="mainstream", source="src0")]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class FakeCamera:
    """A camera that writes down what it was asked, and answers plausibly."""

    host = "10.0.0.7"

    def __init__(self, profiles=TWO_LENSES, absolute: bool = True, zoom: float | None = 0.3):
        self._profiles = list(profiles)
        self.capability = PtzCapability(available=True, absolute_zoom=absolute)
        self.zoomed: list[tuple[float, str]] = []
        self.moved: list[tuple[float, float, float, str]] = []
        self.stopped: list[tuple[str, bool, bool]] = []
        self.asked: list[str] = []
        self.listed = 0
        self.zoom = zoom

    def profiles(self):
        self.listed += 1
        return list(self._profiles)

    def zoom_to(self, where, profile=None):
        self.zoomed.append((where, profile))

    def move(self, pan, tilt, zoom=0.0, profile=None):
        self.moved.append((pan, tilt, zoom, profile))

    def stop(self, profile=None, pan_tilt=True, zoom=True):
        self.stopped.append((profile, pan_tilt, zoom))

    def position(self, profile=None):
        self.asked.append(profile)
        return {"pan": 0.1, "tilt": 0.2, "zoom": self.zoom}


# ------------------------------------------------------- the right lens


def test_each_picture_is_zoomed_through_its_own_lens() -> None:
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS)
    lenses.go_to("thermal", 0.8)
    lenses.go_to("visible", 0.2)
    assert camera.zoomed == [(0.8, "p-ir"), (0.2, "p-vis")]


def test_a_single_sensor_camera_says_both_controls_move_one_lens() -> None:
    lenses = Lenses(FakeCamera(profiles=ONE_LENS), STREAMS)
    assert lenses.find() is True
    assert lenses.shared() is True


def test_a_camera_with_two_lenses_is_not_reported_as_sharing_one() -> None:
    assert Lenses(FakeCamera(), STREAMS).shared() is False


def test_a_camera_that_lists_nothing_is_a_reason_rather_than_a_crash() -> None:
    lenses = Lenses(FakeCamera(profiles=[]), STREAMS)
    assert lenses.find() is False
    assert "no media profiles" in lenses.reason
    assert lenses.go_to("thermal", 0.5)["ok"] is False


def test_a_camera_that_cannot_be_reached_is_asked_again_next_time() -> None:
    """The radio link goes down and comes back. A console that gave up on the
    first refusal would need restarting to notice the camera returned."""

    class Deaf(FakeCamera):
        def __init__(self) -> None:
            super().__init__()
            self.up = False

        def profiles(self):
            self.listed += 1
            if not self.up:
                raise PtzError("cannot reach 192.168.1.251 after 8 s")
            return TWO_LENSES

    camera = Deaf()
    lenses = Lenses(camera, STREAMS)
    assert lenses.find() is False
    camera.up = True
    assert lenses.find() is True
    assert camera.listed == 2


def test_a_camera_that_answered_is_not_asked_what_it_has_again() -> None:
    """It does not grow a third lens. Every extra GetProfiles is a round trip on
    a link that has none to spare."""
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS)
    for _ in range(10):
        lenses.find()
        lenses.go_to("thermal", 0.5)
    assert camera.listed == 1


# ------------------------------------------------- how the lens is driven


def test_a_camera_that_cannot_be_sent_anywhere_refuses_rather_than_guesses() -> None:
    """Creeping for a guessed length of time and calling the result 70% is the
    invented figure this whole control exists to avoid."""
    lenses = Lenses(FakeCamera(absolute=False), STREAMS)
    answer = lenses.go_to("thermal", 0.7)
    assert answer["ok"] is False
    assert "cannot be told where" in answer["reason"]


def test_holding_a_zoom_button_keeps_that_lens_moving() -> None:
    camera = FakeCamera(absolute=False)
    lenses = Lenses(camera, STREAMS)
    lenses.creep("visible", 1.0)
    assert camera.moved == [(0.0, 0.0, CREEP_SPEED, "p-vis")]


def test_zooming_the_other_way_is_the_same_speed_the_other_way() -> None:
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS)
    lenses.creep("thermal", -0.9)
    assert camera.moved == [(0.0, 0.0, -CREEP_SPEED, "p-ir")]


def test_a_zoom_never_carries_a_pan_or_tilt_with_it() -> None:
    """One gimbal behind both lenses. A zoom that slewed the head would take the
    OTHER picture off whatever it was pointed at."""
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS)
    lenses.creep("thermal", 1.0)
    pan, tilt, _zoom, _profile = camera.moved[-1]
    assert (pan, tilt) == (0.0, 0.0)


def test_letting_go_of_the_zoom_does_not_stop_a_pan_being_held() -> None:
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS)
    lenses.creep("thermal", 0.0)
    assert camera.stopped == [("p-ir", False, True)]


def test_a_camera_that_refuses_a_command_says_why_instead_of_raising() -> None:
    """The pictures are not downstream of the zoom. A camera that will not move
    must produce a sentence, never an exception that reaches the window."""

    class Refuses(FakeCamera):
        def zoom_to(self, where, profile=None):
            raise PtzError("cannot reach 192.168.1.251 after 8 s")

    answer = Lenses(Refuses(), STREAMS).go_to("thermal", 0.5)
    assert answer["ok"] is False and "cannot reach" in answer["reason"]


# ------------------------------------------------- what it costs the link


def test_the_lens_is_read_once_at_the_start_so_the_slider_begins_somewhere_true() -> None:
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS, clock=Clock())
    lenses.poll()
    assert camera.asked == ["p-ir", "p-vis"]
    assert lenses.position("thermal") == 0.3


def test_a_link_nobody_is_touching_is_not_polled_at_all() -> None:
    """The one that would have gone unnoticed. A readout refreshed on the
    console's heartbeat is two SOAP round trips every two seconds, for ever, on
    a link already at 88% of its airtime - to redraw a number that has not
    changed."""
    camera = FakeCamera()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, clock=clock)
    lenses.poll()
    before = len(camera.asked)
    for _ in range(300):  # ten minutes of heartbeats
        clock.tick(2.0)
        lenses.poll()
    assert len(camera.asked) == before, f"{len(camera.asked) - before} calls for nothing"


def test_a_lens_that_was_just_commanded_is_followed_until_it_settles() -> None:
    """It is travelling, and the slider has to arrive where the lens arrived."""
    camera = FakeCamera()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, clock=clock)
    lenses.poll()
    camera.asked.clear()

    lenses.go_to("thermal", 0.9)
    camera.zoom = 0.9
    for _ in range(int(SETTLING_SECONDS / SETTLING_EVERY) + 2):
        clock.tick(SETTLING_EVERY)
        lenses.poll()
    assert camera.asked, "a lens that was just moved was never read back"
    assert all(profile == "p-ir" for profile in camera.asked), camera.asked
    assert lenses.position("thermal") == 0.9


def test_the_following_stops_when_the_lens_has_had_time_to_arrive() -> None:
    camera = FakeCamera()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, clock=clock)
    lenses.poll()
    lenses.go_to("visible", 0.6)
    for _ in range(20):
        clock.tick(SETTLING_EVERY)
        lenses.poll()
    settled = len(camera.asked)
    for _ in range(50):
        clock.tick(2.0)
        lenses.poll()
    assert len(camera.asked) == settled, "the console never stopped asking"


def test_drawing_the_position_never_talks_to_the_camera() -> None:
    """`position` is what the window calls on every redraw. If it asked the
    camera anything, the whole policy above would be decoration."""
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS, clock=Clock())
    lenses.poll()
    before = len(camera.asked)
    for _ in range(100):
        lenses.position("thermal")
    assert len(camera.asked) == before


def test_a_camera_that_reports_no_zoom_is_drawn_as_not_reporting_one() -> None:
    """Not as zero. A slider sitting at the wide end because the field was
    missing has told the operator the lens is fully wide."""
    lenses = Lenses(FakeCamera(zoom=None), STREAMS, clock=Clock())
    lenses.poll()
    assert lenses.position("thermal") is None


def test_a_zoom_outside_the_range_the_camera_reported_is_brought_onto_the_slider() -> None:
    lenses = Lenses(FakeCamera(zoom=1.4), STREAMS, clock=Clock())
    lenses.poll()
    assert lenses.position("visible") == 1.0
