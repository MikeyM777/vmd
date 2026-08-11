"""Which lens a zoom command goes to.

The camera is one gimbal carrying two sensors: a thermal one and a visible one,
each behind its own lens. The console had a single zoom, addressed to whichever
media profile `GetProfiles` happened to list first, so zooming "the camera"
zoomed one of the two and nobody had decided which.

What makes this worth a file of its own is how the failure looks. Sending the
thermal zoom to the visible lens raises nothing, logs nothing and reports
nothing: the camera accepts the command and carries it out on the other picture.
What the operator sees is the picture he is watching not responding, which is
indistinguishable from a command that never arrived over the radio link - and
this project has already spent a day chasing one of those.

So the matching is a pure function with a name, and it is pinned here against
the answers real cameras give: names that say which lens it is, names that say
nothing, main-and-sub pairs of the same lens, and single-sensor cameras where
the honest answer is that both controls move the same glass.
"""

from __future__ import annotations

import pytest

from vmd.ptz.onvif import (
    OnvifPtz,
    Profile,
    PtzCapability,
    match_profiles,
    read_profiles,
)

STREAMS = ["thermal", "visible"]


def answer(body: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )


# A dual-sensor answer in the shape these cameras really send it: namespace
# prefixes on every element, two video sources, and a main and a sub stream for
# each sensor.
DUAL = answer(
    '<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
    ' xmlns:tt="http://www.onvif.org/ver10/schema">'
    '<trt:Profiles token="Profile_1" fixed="true"><tt:Name>MainStream</tt:Name>'
    "<tt:VideoSourceConfiguration token=\"VSC_1\"><tt:Name>VSC1</tt:Name>"
    "<tt:SourceToken>VideoSource_1</tt:SourceToken></tt:VideoSourceConfiguration>"
    "</trt:Profiles>"
    '<trt:Profiles token="Profile_2" fixed="true"><tt:Name>SubStream</tt:Name>'
    "<tt:VideoSourceConfiguration token=\"VSC_1\"><tt:Name>VSC1</tt:Name>"
    "<tt:SourceToken>VideoSource_1</tt:SourceToken></tt:VideoSourceConfiguration>"
    "</trt:Profiles>"
    '<trt:Profiles token="Profile_3" fixed="true"><tt:Name>MainStream2</tt:Name>'
    "<tt:VideoSourceConfiguration token=\"VSC_2\"><tt:Name>VSC2</tt:Name>"
    "<tt:SourceToken>VideoSource_2</tt:SourceToken></tt:VideoSourceConfiguration>"
    "</trt:Profiles>"
    "</trt:GetProfilesResponse>"
)

NAMED = answer(
    '<GetProfilesResponse xmlns="http://www.onvif.org/ver10/media/wsdl">'
    '<Profiles token="p-vis"><Name>Visible light</Name>'
    "<VideoSourceConfiguration><SourceToken>src0</SourceToken>"
    "</VideoSourceConfiguration></Profiles>"
    '<Profiles token="p-ir"><Name>Thermal</Name>'
    "<VideoSourceConfiguration><SourceToken>src1</SourceToken>"
    "</VideoSourceConfiguration></Profiles>"
    "</GetProfilesResponse>"
)

ONE = answer(
    '<GetProfilesResponse xmlns="http://www.onvif.org/ver10/media/wsdl">'
    '<Profiles token="only"><Name>mainstream</Name>'
    "<VideoSourceConfiguration><SourceToken>src0</SourceToken>"
    "</VideoSourceConfiguration></Profiles>"
    "</GetProfilesResponse>"
)


# ------------------------------------------------------------------- reading


def test_every_profile_is_read_with_its_name_and_its_video_source() -> None:
    profiles = read_profiles(DUAL)
    assert [p.token for p in profiles] == ["Profile_1", "Profile_2", "Profile_3"]
    assert [p.source for p in profiles] == ["VideoSource_1", "VideoSource_1", "VideoSource_2"]
    assert profiles[0].name == "MainStream"


def test_a_namespace_prefix_does_not_hide_a_profile() -> None:
    """Cameras answer with `trt:Profiles`, `tt:Profiles` or bare `Profiles`
    depending on the firmware, and a parser that matched one of those would find
    nothing on two thirds of the cameras in the world."""
    assert len(read_profiles(NAMED)) == 2
    assert len(read_profiles(DUAL)) == 3


def test_a_terse_answer_still_yields_the_profile_the_console_used_to_use() -> None:
    """Some cameras answer with the token and little else. One profile is what
    the console had before any of this, and it steers."""
    terse = answer('<GetProfilesResponse token="Profile_1" xmlns="x"/>')
    assert [p.token for p in read_profiles(terse)] == ["Profile_1"]


def test_an_answer_with_no_profiles_at_all_is_empty_and_not_a_guess() -> None:
    assert read_profiles(answer("<GetProfilesResponse/>")) == []


# ------------------------------------------------------------------ matching


def test_a_camera_that_names_its_lenses_is_believed() -> None:
    chosen = match_profiles(STREAMS, read_profiles(NAMED))
    assert chosen == {"thermal": "p-ir", "visible": "p-vis"}


def test_a_camera_that_names_nothing_is_split_by_its_video_sources() -> None:
    """`MainStream`, `SubStream` and `MainStream2` say nothing about lenses. The
    video sources say everything: two of them, so two sensors, and the sub
    stream is the same glass as the main one rather than a third lens."""
    chosen = match_profiles(STREAMS, read_profiles(DUAL))
    assert chosen == {"thermal": "Profile_1", "visible": "Profile_3"}
    assert "Profile_2" not in chosen.values(), "the sub stream is not a second lens"


def test_a_single_sensor_camera_says_both_controls_move_the_same_glass() -> None:
    """Rather than leaving one zoom control mysteriously dead. That is the truth
    about such a camera, and it is better shown than hidden."""
    chosen = match_profiles(STREAMS, read_profiles(ONE))
    assert chosen == {"thermal": "only", "visible": "only"}


def test_no_profiles_means_nothing_is_matched_rather_than_something_guessed() -> None:
    assert match_profiles(STREAMS, []) == {}


def test_one_lens_is_never_given_to_two_streams_when_there_are_two_lenses() -> None:
    chosen = match_profiles(STREAMS, read_profiles(DUAL))
    assert len(set(chosen.values())) == 2


def test_a_word_inside_another_word_is_not_a_thermal_lens() -> None:
    """"ir" is inside "third", "wire" and "direct". A profile called `Direct` is
    not the infrared sensor, and it is listed FIRST here on purpose: a match
    that looked for "ir" anywhere in the text would take it and stop, and every
    thermal zoom on this camera would go to the visible lens for ever.
    """
    profiles = [
        Profile(token="a", name="Direct", source="src0"),
        Profile(token="b", name="Thermal IR", source="src1"),
    ]
    assert match_profiles(["thermal"], profiles)["thermal"] == "b"


def test_a_name_carrying_both_words_does_not_win_the_thermal_slot() -> None:
    """`IR-cut visible` is the visible lens describing its filter. A keyword
    match that ignored the other lens's words would hand it to the thermal."""
    profiles = [
        Profile(token="v", name="IR-cut visible", source="src0"),
        Profile(token="t", name="Thermal", source="src1"),
    ]
    assert match_profiles(STREAMS, profiles) == {"thermal": "t", "visible": "v"}


def test_a_stream_the_operator_named_something_else_is_still_placed() -> None:
    """He can call his streams anything. "front" and "gate" carry no lens words,
    so they fall through to the video sources - which is the whole reason the
    sources are the second step rather than the only one."""
    chosen = match_profiles(["front", "gate"], read_profiles(DUAL))
    assert set(chosen) == {"front", "gate"}
    assert len(set(chosen.values())) == 2


# ----------------------------------------------------------- what is sent out


class Recorder(OnvifPtz):
    """An OnvifPtz that writes its SOAP down instead of posting it.

    The alternative is another fake HTTP camera, and there is one of those in
    `test_ptz.py` already. What is being checked here is the XML - which profile
    a command was addressed to, and what was in it - and that is the one thing a
    transport adds nothing to.
    """

    def __init__(self) -> None:
        super().__init__("10.0.0.5", "admin", "pw")
        self.sent: list[tuple[str, str]] = []
        self.capability = PtzCapability(
            available=True,
            reason="ready",
            profile="Profile_1",
            profiles=[Profile(token="Profile_1"), Profile(token="Profile_3")],
            absolute_zoom=True,
        )

    def _post(self, path: str, body: str, expect: str = "") -> str:
        self.sent.append((path, body))
        return answer(f'<{expect or "Ok"} xmlns="x"/>')


def test_a_zoom_is_addressed_to_the_lens_it_belongs_to() -> None:
    camera = Recorder()
    camera.zoom_to(0.75, profile="Profile_3")
    _path, body = camera.sent[-1]
    assert "AbsoluteMove" in body
    assert "<ProfileToken>Profile_3</ProfileToken>" in body
    assert 'x="0.750"' in body


def test_a_zoom_carries_no_pan_or_tilt_with_it() -> None:
    """The head is shared between the two lenses. An AbsoluteMove carrying a
    PanTilt would slew the gimbal every time somebody touched a zoom slider, and
    the OTHER picture would come off whatever it was pointed at."""
    camera = Recorder()
    camera.zoom_to(0.4, profile="Profile_1")
    _path, body = camera.sent[-1]
    assert "PanTilt" not in body


@pytest.mark.parametrize("asked,sent", [(2.0, "1.000"), (-1.0, "0.000")])
def test_a_zoom_outside_the_lens_travel_is_brought_back_to_it(asked, sent) -> None:
    camera = Recorder()
    camera.zoom_to(asked)
    assert f'x="{sent}"' in camera.sent[-1][1]


def test_a_command_with_no_profile_named_still_goes_to_the_first_one() -> None:
    """Every command the console sent before there were two lenses. Pan and tilt
    still work this way: one gimbal, and it does not matter which profile asks."""
    camera = Recorder()
    camera.move(0.5, 0.0)
    assert "<ProfileToken>Profile_1</ProfileToken>" in camera.sent[-1][1]


def test_letting_go_of_a_zoom_button_does_not_halt_a_pan_being_held() -> None:
    """They are separate motors on one head, and the operator can be holding an
    arrow key while he lets go of the zoom."""
    camera = Recorder()
    camera.stop(pan_tilt=False, zoom=True)
    body = camera.sent[-1][1]
    assert "<PanTilt>false</PanTilt>" in body
    assert "<Zoom>true</Zoom>" in body


def test_stop_still_stops_everything_when_nobody_says_otherwise() -> None:
    """The default has to stay what it was: a stop that was not carried out is a
    head left slewing with no key held."""
    camera = Recorder()
    camera.stop()
    body = camera.sent[-1][1]
    assert "<PanTilt>true</PanTilt>" in body and "<Zoom>true</Zoom>" in body


def test_the_position_of_one_lens_is_asked_of_that_lens() -> None:
    camera = Recorder()
    camera.position(profile="Profile_3")
    assert "<ProfileToken>Profile_3</ProfileToken>" in camera.sent[-1][1]


# ------------------------------------------------- can it be sent anywhere at all


def test_absolute_zoom_is_discovered_and_not_assumed() -> None:
    """A camera that lists no absolute zoom space answers an AbsoluteMove with a
    fault - which on the console is a zoom slider reporting failure at the
    moment somebody is trying to see something. Asked once, remembered, and the
    control changes what its buttons do instead."""

    class Camera(Recorder):
        def __init__(self, nodes: str) -> None:
            super().__init__()
            self._nodes = nodes
            self.capability = PtzCapability()

        def _post(self, path: str, body: str, expect: str = "") -> str:
            if "GetProfiles" in body:
                return ONE
            if "GetNodes" in body:
                return self._nodes
            return answer(f'<{expect or "Ok"} xmlns="x"/>')

    with_space = answer(
        '<GetNodesResponse xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
        '<PTZNode token="n"><SupportedPTZSpaces><AbsoluteZoomPositionSpace>'
        "<URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</URI>"
        "</AbsoluteZoomPositionSpace></SupportedPTZSpaces></PTZNode>"
        "</GetNodesResponse>"
    )
    without = answer(
        '<GetNodesResponse xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
        '<PTZNode token="n"><HomeSupported>true</HomeSupported></PTZNode>'
        "</GetNodesResponse>"
    )
    assert Camera(with_space).connect().absolute_zoom is True
    assert Camera(without).connect().absolute_zoom is False


def test_what_the_camera_can_do_travels_with_the_rest_of_its_answer() -> None:
    """`as_dict` is what reaches the console, and a capability the console never
    sees is a capability the zoom control cannot use."""
    said = PtzCapability(
        available=True, profiles=[Profile(token="a", name="Thermal")], absolute_zoom=True
    ).as_dict()
    assert said["absolute_zoom"] is True
    assert said["profiles"] == [{"token": "a", "name": "Thermal", "source": ""}]
