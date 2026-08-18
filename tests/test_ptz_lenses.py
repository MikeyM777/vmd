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

from vmd.ptz.lenses import (
    CREEP_SPEED,
    RETRY_AFTER_SECONDS,
    SETTLING_EVERY,
    SETTLING_SECONDS,
    Lenses,
)
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


class ByAddress(FakeCamera):
    """A camera that serves each profile at its own RTSP path.

    Listed deliberately in the OPPOSITE order to the operator's views, which is
    the whole fault: `match_profiles` pairs the two lists in order, and on his
    camera the orders disagree.
    """

    def __init__(self, serving: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.serving = serving or {"p-ir": "/ch0", "p-vis": "/ch2"}
        self.asked_for_addresses: list[str] = []

    def stream_uri(self, profile: str) -> str:
        self.asked_for_addresses.append(profile)
        path = self.serving.get(profile, "")
        return f"rtsp://10.0.0.7:554{path}" if path else ""


HIS_URLS = {
    "thermal": "rtsp://10.0.0.7:554/ch2",
    "visible": "rtsp://10.0.0.7:554/ch0",
}


def test_the_camera_is_asked_which_profile_serves_which_picture() -> None:
    """The fault he reported: "the visible camera slider controls the thermal
    camera, and the thermal slider controls the visible camera."

    The pairing was inferred from profile names and the order the camera
    happened to list things in, and on his camera that order is the opposite of
    the order he listed his views in. He typed one address per picture, copied
    off the camera - so the camera can be asked which profile serves it, and
    `/ch2` matches `/ch2` whatever anything is called.
    """
    # Named so that the name-matching guess gets it BACKWARDS, exactly as his
    # camera does. Only the addresses can put it right.
    camera = ByAddress(serving={"p-ir": "/ch0", "p-vis": "/ch2"})
    lenses = Lenses(camera, STREAMS, urls=HIS_URLS)
    assert lenses.find() is True

    assert lenses.token("thermal") == "p-vis", "the sliders are still crossed"
    assert lenses.token("visible") == "p-ir"

    lenses.go_to("thermal", 0.9)
    assert camera.zoomed == [(0.9, "p-vis")]


def test_the_addresses_are_asked_for_once_and_not_on_every_beat() -> None:
    """Two or three round trips on a link measured at 88% of its airtime is a
    fair price once. It is not a fair price every two seconds."""
    camera = ByAddress()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, urls=HIS_URLS, clock=clock)
    for _ in range(50):
        clock.tick(2.0)
        lenses.poll()
    assert len(camera.asked_for_addresses) == 2, camera.asked_for_addresses


def test_one_address_serving_two_profiles_decides_nothing() -> None:
    """A main and a sub stream of the same picture. Picking either would be a
    coin toss dressed up as evidence, so the names keep the last word."""
    camera = ByAddress(serving={"p-ir": "/ch2", "p-vis": "/ch2"})
    lenses = Lenses(camera, STREAMS, urls=HIS_URLS)
    assert lenses.find() is True
    assert lenses.token("thermal") == "p-ir", "a tie was treated as an answer"


def test_the_zoom_goes_to_the_profile_that_can_zoom_on_that_sensor() -> None:
    """The profile serving the picture is not always the one that can zoom it:
    a main and a sub stream are one lens, and only one may carry PTZ."""
    camera = ByAddress(serving={"p-sub": "/ch2", "p-main": "/ch0"})
    camera._profiles = [
        Profile(token="p-sub", name="sub", source="src1", ptz=False),
        Profile(token="p-main", name="main", source="src1", ptz=True),
    ]
    lenses = Lenses(camera, ["thermal"], urls={"thermal": "rtsp://10.0.0.7:554/ch2"})
    assert lenses.find() is True
    assert lenses.token("thermal") == "p-main", "pointed at a profile with no PTZ"


def test_a_camera_that_will_not_give_addresses_falls_back_to_the_names() -> None:
    """Nothing to correct with is not a reason to have no answer at all."""
    lenses = Lenses(FakeCamera(), STREAMS, urls=HIS_URLS)
    assert lenses.find() is True
    assert lenses.token("thermal") == "p-ir"


def test_what_he_picked_by_hand_still_beats_the_address() -> None:
    """Evidence beats inference, and he beats both. He can see which picture
    answers."""
    camera = ByAddress()
    lenses = Lenses(camera, STREAMS, urls=HIS_URLS, chosen={"thermal": "p-ir"})
    assert lenses.find() is True
    assert lenses.token("thermal") == "p-ir"


def test_the_operator_can_say_which_lens_a_view_drives() -> None:
    """The fault he reported: "only the vis is zooming". Whatever the cause -
    the guess was backwards, or the thermal profile has no PTZ - no rule written
    here can be right on every camera, and a wrong guess is silent: the camera
    accepts the command and carries it out somewhere else.

    So he can overrule it. He can see the picture respond; nothing in this file
    can.
    """
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS, chosen={"thermal": "p-vis", "visible": "p-ir"})
    lenses.go_to("thermal", 0.8)
    assert camera.zoomed == [(0.8, "p-vis")], "the choice was ignored"


def test_a_view_with_no_choice_made_still_gets_the_worked_out_answer() -> None:
    """The override is per view, and an empty one is not a choice."""
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS, chosen={"thermal": "", "visible": ""})
    assert lenses.find() is True
    assert lenses.token("thermal") == "p-ir"


def test_a_chosen_profile_this_camera_does_not_have_is_dropped() -> None:
    """What a settings file carried over from a different camera looks like.
    Sending that token would be a zoom that faults for a reason nothing
    explains, so the guess is used instead."""
    camera = FakeCamera()
    lenses = Lenses(camera, STREAMS, chosen={"thermal": "p-from-another-camera"})
    assert lenses.find() is True
    assert lenses.token("thermal") == "p-ir"


def test_the_profiles_the_camera_offers_can_be_read_for_the_form() -> None:
    """The Settings tab has to list them for him to choose from."""
    lenses = Lenses(FakeCamera(), STREAMS)
    assert lenses.offered() == []
    lenses.find()
    assert [profile.token for profile in lenses.offered()] == ["p-ir", "p-vis"]


def test_a_camera_that_lists_nothing_is_a_reason_rather_than_a_crash() -> None:
    lenses = Lenses(FakeCamera(profiles=[]), STREAMS)
    assert lenses.find() is False
    assert "no media profiles" in lenses.reason
    assert lenses.go_to("thermal", 0.5)["ok"] is False


class Deaf(FakeCamera):
    """A camera that refuses to list anything until `up` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.up = False

    def profiles(self):
        self.listed += 1
        if not self.up:
            raise PtzError("cannot reach 192.168.1.251 after 8 s")
        return TWO_LENSES


def test_a_camera_that_cannot_be_reached_is_asked_again_later() -> None:
    """The radio link goes down and comes back. A console that gave up on the
    first refusal would need restarting to notice the camera returned."""
    camera = Deaf()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, clock=clock)
    assert lenses.find() is False
    camera.up = True
    clock.tick(RETRY_AFTER_SECONDS)
    assert lenses.find() is True
    assert camera.listed == 2


def test_a_camera_that_cannot_be_reached_is_not_asked_on_every_heartbeat() -> None:
    """The state a wrong address or a camera that is switched off leaves behind.

    `poll` runs on the console's two-second heartbeat, and every one of them
    called `find` again, which on a camera that has not answered means another
    `GetProfiles` - one login attempt per authentication style against a camera
    that is refusing, or one eight-second timeout against one that is not there,
    for as long as the console is open. That is the console putting traffic on a
    link measured at 88% of its airtime, on a schedule, to ask a question it has
    already been told the answer to - which is exactly what this module's
    docstring says it exists not to do.

    It is also the command sender's thread. While it is inside that call, the
    stop the operator owes the head when he lets go of an arrow key is sitting
    in the mailbox waiting for it.
    """
    camera = Deaf()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, clock=clock)
    for _ in range(300):  # ten minutes of heartbeats
        clock.tick(2.0)
        lenses.poll()
    allowed = int(600 / RETRY_AFTER_SECONDS) + 1
    assert camera.listed <= allowed, f"{camera.listed} attempts in ten minutes"


def test_a_camera_that_comes_back_is_noticed_without_anybody_restarting_anything() -> None:
    """The other half, and the reason the retry is slowed rather than stopped."""
    camera = Deaf()
    clock = Clock()
    lenses = Lenses(camera, STREAMS, clock=clock)
    lenses.poll()
    assert lenses.reason != "ready"

    camera.up = True
    for _ in range(int(RETRY_AFTER_SECONDS / 2.0) + 2):
        clock.tick(2.0)
        lenses.poll()
    assert lenses.reason == "ready"
    assert lenses.position("thermal") == 0.3


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


# ------------------------------------------------- the channel number decides
#
# "Swap the zoom sliders by default."
#
# They came out crossed on the real camera and this is why. His pictures are
# rtsp://.../ch1 and rtsp://.../ch2, and he lists the thermal - ch2 - first.
# When the profile names say nothing about which lens is which, `match_profiles`
# falls through to pairing the two lists in order, and the camera lists ch1
# first. So the thermal got the visible lens and every zoom went to the wrong
# glass.


# The camera as it actually is: profiles with no useful names, listed ch1 first,
# and nothing that answers GetStreamUri.
NAMELESS = [
    Profile(token="Profile_1", name="mainstream", source="VideoSource_1"),
    Profile(token="Profile_2", name="mainstream", source="VideoSource_2"),
]

HIS_CAMERA_URLS = {
    "thermal": "rtsp://192.168.1.250:554/ch2",
    "visible": "rtsp://192.168.1.250:554/ch1",
}


class NoAddresses(FakeCamera):
    """A camera that will not say which address serves which profile.

    Which is the case this falls to: the exact-address match is the best rule
    there is and plenty of firmware does not support it.
    """

    def stream_uri(self, profile: str) -> str:
        return ""


def test_the_channel_number_pairs_the_views_with_the_lenses() -> None:
    """His camera, his addresses, his listing order. Thermal is ch2 and must
    reach the ch2 lens whichever order either list happens to be in."""
    lenses = Lenses(NoAddresses(profiles=NAMELESS), STREAMS, urls=HIS_CAMERA_URLS)
    assert lenses.find()
    assert lenses.token("thermal") == "Profile_2"
    assert lenses.token("visible") == "Profile_1"


def test_without_the_channel_the_pairing_is_the_one_that_was_crossed() -> None:
    """The proof that this is the fault and not a coincidence: take the channel
    numbers out of the addresses and the old, crossed answer comes back."""
    blind = {"thermal": "rtsp://192.168.1.250:554/main", "visible": "rtsp://192.168.1.250:554/sub"}
    lenses = Lenses(NoAddresses(profiles=NAMELESS), STREAMS, urls=blind)
    assert lenses.find()
    assert lenses.token("thermal") == "Profile_1", "list order, which is what was wrong"


def test_a_name_that_says_which_lens_it_is_still_wins() -> None:
    """The order of trust is unchanged. A camera that bothers to say `Thermal`
    is believed over any arithmetic on numbers."""
    named = [
        Profile(token="Profile_1", name="Thermal", source="VideoSource_1"),
        Profile(token="Profile_2", name="Visible light", source="VideoSource_2"),
    ]
    # The channel numbers say the opposite of the names, on purpose.
    lenses = Lenses(NoAddresses(profiles=named), STREAMS, urls=HIS_CAMERA_URLS)
    assert lenses.find()
    assert lenses.token("thermal") == "Profile_1", "the name was overruled by a number"


def test_two_profiles_claiming_one_channel_are_not_guessed_at() -> None:
    """A guess with two answers is not an answer. It falls through to the step
    below rather than picking one and being silently wrong on half the
    installations that have a main and a sub stream per sensor."""
    same = [
        Profile(token="Profile_1", name="mainstream", source="VideoSource_1"),
        Profile(token="Profile_1_sub", name="substream", source="VideoSource_1"),
    ]
    lenses = Lenses(NoAddresses(profiles=same), STREAMS, urls=HIS_CAMERA_URLS)
    assert lenses.find()
    # Whatever it decides, it may not put both views on different lenses on the
    # strength of a number that appears twice.
    assert lenses.token("thermal") in {"Profile_1", "Profile_1_sub", None}


def test_an_address_with_no_channel_in_it_leaves_the_pairing_alone() -> None:
    """Nothing here may fire on a guess. One view with a number and one without
    is not enough to place either."""
    half = {"thermal": "rtsp://192.168.1.250:554/ch2", "visible": "rtsp://192.168.1.250:554/main"}
    lenses = Lenses(NoAddresses(profiles=NAMELESS), STREAMS, urls=half)
    assert lenses.find()
    assert lenses.token("thermal") == "Profile_1", "it placed one view on a half answer"
