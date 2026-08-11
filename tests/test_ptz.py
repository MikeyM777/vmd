"""PTZ over ONVIF, against a camera that answers the way real ones do."""

from __future__ import annotations

import base64
import hashlib
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vmd.ptz.onvif import OnvifPtz, PtzError
from vmd.ptz.service import PtzService
from vmd.settings import CameraSettings, Settings

USER, PASSWORD = "admin", "s3cret"

PROFILES = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>
<GetProfilesResponse xmlns="http://www.onvif.org/ver10/media/wsdl">
<Profiles token="Profile_1" fixed="true"><Name>mainstream</Name></Profiles>
</GetProfilesResponse></s:Body></s:Envelope>"""

NODES = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>
<GetNodesResponse xmlns="http://www.onvif.org/ver20/ptz/wsdl">
<PTZNode token="node0"><HomeSupported>true</HomeSupported></PTZNode>
</GetNodesResponse></s:Body></s:Envelope>"""

OK = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body/></s:Envelope>"""

FAULT = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><s:Fault>
<s:Reason><s:Text>Sender not Authorized</s:Text></s:Reason>
</s:Fault></s:Body></s:Envelope>"""


class FakeCamera(BaseHTTPRequestHandler):
    """Answers ONVIF. Accepts only the WS-Security UsernameToken, which is what
    forces the client to fall through digest and basic first - the real
    behaviour of several cameras, and the thing worth testing."""

    requests: list[tuple[str, str]] = []
    accept_wsse = True

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
        type(self).requests.append((self.path, body))

        if "Security" not in body:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Digest realm="onvif", nonce="abc", qop="auth"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not self._token_is_valid(body):
            self._reply(400, FAULT)
            return

        if "GetProfiles" in body:
            self._reply(200, PROFILES)
        elif "GetNodes" in body:
            self._reply(200, NODES)
        elif "GetVideoEncoderConfigurationOptions" in body:
            self._reply(200, ENCODER_OPTIONS)
        elif "GetVideoEncoderConfigurations" in body:
            self._reply(200, ENCODERS)
        else:
            self._reply(200, OK)

    def _token_is_valid(self, body: str) -> bool:
        nonce = re.search(r"<Nonce[^>]*>(.*?)</Nonce>", body)
        created = re.search(r"<Created[^>]*>(.*?)</Created>", body)
        digest = re.search(r"<Password[^>]*>(.*?)</Password>", body)
        if not (nonce and created and digest):
            return False
        expected = hashlib.sha1(
            base64.b64decode(nonce.group(1)) + created.group(1).encode() + PASSWORD.encode()
        ).digest()
        return base64.b64encode(expected).decode() == digest.group(1)

    def _reply(self, status: int, payload: str) -> None:
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/soap+xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def camera() -> Iterator[tuple[str, int]]:
    FakeCamera.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCamera)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[0], server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def connected(camera: tuple[str, int]) -> OnvifPtz:
    host, port = camera
    ptz = OnvifPtz(host, USER, PASSWORD, port=port)
    ptz.connect()
    return ptz


def last_body(name: str) -> str:
    for path, body in reversed(FakeCamera.requests):
        if name in body:
            return body
    raise AssertionError(f"the camera was never sent a {name}")


def test_it_finds_the_profile_and_reports_ready(camera: tuple[str, int]) -> None:
    capability = connected(camera).capability
    assert capability.available is True
    assert capability.profile == "Profile_1"
    assert capability.supports_home is True
    assert capability.auth == "wsse"


def test_move_sends_the_speeds_the_operator_asked_for(camera: tuple[str, int]) -> None:
    connected(camera).move(0.5, -0.25, 0.0)
    body = last_body("ContinuousMove")
    assert 'x="0.500"' in body
    assert 'y="-0.250"' in body
    assert "Profile_1" in body


def test_speeds_are_clamped_to_what_onvif_allows(camera: tuple[str, int]) -> None:
    connected(camera).move(9.0, -9.0, 5.0)
    body = last_body("ContinuousMove")
    assert 'x="1.000"' in body and 'y="-1.000"' in body
    assert 'Zoom x="1.000"' in body


def test_stop_stops_both_axes_and_zoom(camera: tuple[str, int]) -> None:
    connected(camera).stop()
    body = last_body("Stop")
    assert "<PanTilt>true</PanTilt>" in body
    assert "<Zoom>true</Zoom>" in body


def test_home_is_sent_as_a_home_command(camera: tuple[str, int]) -> None:
    connected(camera).home()
    assert "GotoHomePosition" in last_body("GotoHomePosition")


def test_a_wrong_password_is_reported_in_the_camera_s_own_words(camera: tuple[str, int]) -> None:
    host, port = camera
    ptz = OnvifPtz(host, USER, "wrong", port=port)
    capability = ptz.connect()
    assert capability.available is False
    assert "Authorized" in capability.reason or "refused" in capability.reason


def test_an_unreachable_camera_is_a_sentence_not_a_crash() -> None:
    ptz = OnvifPtz("192.0.2.99", USER, PASSWORD, port=81)
    capability = ptz.connect()
    assert capability.available is False
    assert "cannot reach" in capability.reason


def test_the_service_never_raises_at_the_console(camera: tuple[str, int]) -> None:
    """Whatever the camera does, the console keeps running."""
    service = PtzService(Settings(camera=CameraSettings(host="192.0.2.99")))
    result = service.move(1, 0, 0)
    assert result["ok"] is False
    assert result["error"]
    assert service.stop()["ok"] is False


def test_no_camera_address_says_so(camera: tuple[str, int]) -> None:
    service = PtzService(Settings())
    assert service.status()["available"] is False
    assert "no camera address" in service.status()["reason"]
    assert service.move(0.5, 0, 0)["error"] == "no camera address set"


def test_the_password_is_never_sent_in_clear_text(camera: tuple[str, int]) -> None:
    connected(camera).move(0.1, 0.1, 0)
    for _, body in FakeCamera.requests:
        assert PASSWORD not in body, "the password went out in the clear"


def test_a_camera_with_no_profiles_is_reported_not_assumed(camera: tuple[str, int]) -> None:
    host, port = camera
    ptz = OnvifPtz(host, USER, PASSWORD, port=port)
    ptz.capability.profile = ""
    with pytest.raises(PtzError):
        OnvifPtz("192.0.2.99", USER, PASSWORD, port=81).move(0, 0, 0)


def test_status_carries_the_position_when_the_camera_reports_one(camera: tuple[str, int]) -> None:
    host, port = camera
    service = PtzService(Settings(camera=CameraSettings(host=f"{host}:{port}")))
    # The fake camera answers GetStatus with an empty body, so there is no
    # position - and the console must show that rather than invent one.
    service.camera = OnvifPtz(host, USER, PASSWORD, port=port)
    status = service.status()
    assert status["available"] is True
    assert "zoom" not in status or status["zoom"] is None


# --------------------------------------------------------------------------
# Encoder settings: reading them, and capping them so a pan cannot flood the
# link and knock the other stream out.
# --------------------------------------------------------------------------

ENCODERS = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>
<GetVideoEncoderConfigurationsResponse xmlns="http://www.onvif.org/ver10/media/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema">
<Configurations token="enc0"><tt:Name>visible</tt:Name><tt:UseCount>1</tt:UseCount>
<tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>3840</tt:Width><tt:Height>2160</tt:Height></tt:Resolution>
<tt:Quality>4</tt:Quality>
<tt:H264><tt:GovLength>30</tt:GovLength><tt:H264Profile>Main</tt:H264Profile></tt:H264>
<tt:RateControl><tt:FrameRateLimit>30</tt:FrameRateLimit>
<tt:BitrateLimit>16000</tt:BitrateLimit></tt:RateControl>
</Configurations>
<Configurations token="enc2"><tt:Name>thermal</tt:Name><tt:UseCount>1</tt:UseCount>
<tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>640</tt:Width><tt:Height>512</tt:Height></tt:Resolution>
<tt:Quality>5</tt:Quality>
<tt:H264><tt:GovLength>15</tt:GovLength><tt:H264Profile>Main</tt:H264Profile></tt:H264>
<tt:RateControl><tt:FrameRateLimit>30</tt:FrameRateLimit>
<tt:BitrateLimit>2000</tt:BitrateLimit></tt:RateControl>
</Configurations>
</GetVideoEncoderConfigurationsResponse></s:Body></s:Envelope>"""


def test_encoder_settings_are_read_from_the_camera(camera: tuple[str, int]) -> None:
    from vmd.ptz.encoder import parse_configurations

    configs = parse_configurations(ENCODERS)
    assert [c.token for c in configs] == ["enc0", "enc2"]
    visible = configs[0]
    assert (visible.width, visible.height) == (3840, 2160)
    assert visible.bitrate_kbps == 16000
    assert visible.fps == 30
    assert visible.gov_length == 30
    assert "3840x2160" in visible.label


def test_fitting_to_the_link_keeps_the_total_under_it() -> None:
    from vmd.ptz.encoder import fit_to_link, parse_configurations

    configs = parse_configurations(ENCODERS)
    targets = fit_to_link(configs, ceiling_kbps=5000)
    assert sum(targets.values()) <= 5000, "the streams together must fit the link"
    assert targets["enc0"] > targets["enc2"], "the larger picture gets the larger share"
    assert min(targets.values()) >= 256, "no stream may be capped into uselessness"


def test_capping_sends_every_field_back_not_just_the_bitrate(camera: tuple[str, int]) -> None:
    """ONVIF's Set is a whole-object write. Omitting resolution does not mean
    "leave it alone" - a camera that accepts it loses its resolution."""
    from vmd.ptz.encoder import CameraEncoders, parse_configurations

    ptz = connected(camera)
    config = parse_configurations(ENCODERS)[0]
    CameraEncoders(ptz).cap_bitrate(config, 2400)

    body = last_body("SetVideoEncoderConfiguration")
    assert 'token="enc0"' in body
    assert "<tt:Width>3840</tt:Width>" in body
    assert "<tt:Height>2160</tt:Height>" in body
    assert 'BitrateLimit="2400"' in body
    assert "<tt:GovLength>30</tt:GovLength>" in body
    assert "H264" in body


ENCODER_OPTIONS = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>
<GetVideoEncoderConfigurationOptionsResponse xmlns="http://www.onvif.org/ver10/media/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema">
<Options><tt:H264>
<tt:ResolutionsAvailable><tt:Width>3840</tt:Width><tt:Height>2160</tt:Height></tt:ResolutionsAvailable>
<tt:ResolutionsAvailable><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:ResolutionsAvailable>
<tt:ResolutionsAvailable><tt:Width>1280</tt:Width><tt:Height>720</tt:Height></tt:ResolutionsAvailable>
<tt:ResolutionsAvailable><tt:Width>704</tt:Width><tt:Height>576</tt:Height></tt:ResolutionsAvailable>
</tt:H264></Options>
</GetVideoEncoderConfigurationOptionsResponse></s:Body></s:Envelope>"""


def test_the_camera_is_asked_which_resolutions_it_accepts(camera: tuple[str, int]) -> None:
    """Assuming a resolution is how a camera ends up refusing, or quietly
    producing something else."""
    from vmd.ptz.encoder import CameraEncoders

    sizes = CameraEncoders(connected(camera)).options("enc0")
    assert sizes[0] == (3840, 2160), "largest first"
    assert (1280, 720) in sizes
    assert (704, 576) in sizes


def test_dropping_a_stream_out_of_4k_keeps_everything_else(camera: tuple[str, int]) -> None:
    from vmd.ptz.encoder import CameraEncoders, parse_configurations

    config = parse_configurations(ENCODERS)[0]
    assert (config.width, config.height) == (3840, 2160)

    updated = CameraEncoders(connected(camera)).apply(config, size=(1280, 720))
    assert (updated.width, updated.height) == (1280, 720)

    body = last_body("SetVideoEncoderConfiguration")
    assert "<tt:Width>1280</tt:Width>" in body
    assert "<tt:Height>720</tt:Height>" in body
    # everything the operator did not touch survives the write
    assert 'BitrateLimit="16000"' in body
    assert 'FrameRateLimit="30"' in body
    assert "<tt:GovLength>30</tt:GovLength>" in body


def test_the_service_changes_one_encoder_by_token(camera: tuple[str, int]) -> None:
    host, port = camera
    service = PtzService(
        Settings(camera=CameraSettings(host=f"{host}:{port}", username=USER, password=PASSWORD))
    )
    result = service.set_encoder("enc0", width=1280, height=720)
    assert result["ok"] is True, result.get("error")
    assert "1280x720" in result["label"]


def test_an_unknown_encoder_is_named_not_guessed(camera: tuple[str, int]) -> None:
    host, port = camera
    service = PtzService(
        Settings(camera=CameraSettings(host=f"{host}:{port}", username=USER, password=PASSWORD))
    )
    result = service.set_encoder("does-not-exist", width=1280, height=720)
    assert result["ok"] is False
    assert "does-not-exist" in result["error"]


def test_stop_and_home_with_no_camera_say_so_instead_of_raising() -> None:
    """The state every first run is in: an address has not been typed yet.

    The Live tab calls stop() on every key release and home() on Home, both from
    a Qt key handler. An exception there is not a message the operator can read,
    it is the console going down - so these must answer the same way move() does.
    """
    service = PtzService(Settings())
    assert service.stop() == {"ok": False, "error": "no camera address set"}
    assert service.home() == {"ok": False, "error": "no camera address set"}


def test_the_camera_is_never_reached_through_a_proxy(monkeypatch) -> None:
    """The same rule as the radio: no proxy may stand between us and the camera.

    urllib picks up http_proxy, https_proxy and the Windows registry unless it
    is told not to, and every one of these openers carries the camera's login.
    """
    import urllib.request

    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    for name, opener in OnvifPtz("10.0.0.5", USER, PASSWORD)._openers():
        # An empty ProxyHandler registers no methods, so build_opener leaves it
        # out of `handlers` entirely - what matters is that it displaced the
        # default one that would have been built from the environment.
        routed = [
            handler.proxies
            for handler in opener.handlers
            if isinstance(handler, urllib.request.ProxyHandler) and handler.proxies
        ]
        assert routed == [], f"the {name} login would be sent through {routed}"


def test_an_unreachable_camera_gives_up_quickly(monkeypatch) -> None:
    """How long one command costs when there is nothing at the address.

    6 s was the old figure, and with a press and a release either side of it one
    tap of an arrow key cost 12.36 s against 10.255.255.1. The camera is one
    radio hop away on a private link - milliseconds of round trip - so a camera
    that has not begun to answer in a couple of seconds is not answering.

    Measured against a socket that never answers rather than against the
    constant, so that a timeout which stopped being passed to urllib at all -
    the way this actually breaks - still fails the test. Bounded well below the
    old figure, so a return to it fails here rather than in the field.
    """
    import time
    import urllib.error
    import urllib.request

    waited: list[float] = []

    def never_answers(self, request, timeout=None, **kwargs):
        waited.append(timeout)
        time.sleep(min(timeout or 0.0, 0.05))  # the shape of it, not the wait
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", never_answers)

    started = time.monotonic()
    with pytest.raises(PtzError):
        OnvifPtz("10.255.255.1", USER, PASSWORD).move(0.5, 0.0, 0.0)
    assert time.monotonic() - started < 1.0

    assert waited, "urllib was never given a timeout at all"
    assert max(waited) <= 3.0, (
        f"a camera one hop away is given {max(waited)} s to answer; that is a "
        "frozen console every time the link drops"
    )
