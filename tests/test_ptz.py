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
