"""Reading the Ubiquiti radio: what it says, and what it does when it will not."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vmd.radio.airos import AirOsRadio, RadioError, parse_status
from vmd.radio.service import RadioService
from vmd.settings import RadioSettings, Settings

USER, PASSWORD = "ubnt", "linkpass"

STATUS = {
    "host": {"hostname": "LOCO-north", "uptime": 84231, "devmodel": "NanoStation 5AC loco"},
    "wireless": {
        "essid": "vmd-link",
        "signal": -63,
        "noisef": -96,
        "ccq": 985,
        "txrate": 130,
        "rxrate": 117,
        "distance": 15400,
        "throughput": {"tx": 512, "rx": 4200},
        "polling": {"dl_capacity": 24000, "ul_capacity": 18000},
    },
}


class FakeRadio(BaseHTTPRequestHandler):
    logged_in = False
    accept_login = True

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if not type(self).accept_login or f"password={PASSWORD}" not in body:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        type(self).logged_in = True
        self.send_response(200)
        self.send_header("Set-Cookie", "AIROS_SESSIONID=abc; path=/")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self) -> None:  # noqa: N802
        if not type(self).logged_in:
            # airOS answers an unauthenticated status.cgi with its login page,
            # not with an error - which is why the client must notice HTML.
            payload = b"<html>login</html>"
        else:
            payload = json.dumps(STATUS).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def radio() -> Iterator[str]:
    FakeRadio.logged_in = False
    FakeRadio.accept_login = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRadio)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{server.server_address[0]}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_it_reads_the_link(radio: str) -> None:
    status = AirOsRadio(radio, USER, PASSWORD).status()
    assert status.connected is True
    assert status.signal_dbm == -63
    assert status.noise_dbm == -96
    assert status.rx_mbps == pytest.approx(4.2)
    assert status.device == "LOCO-north"
    assert status.distance_m == 15400


def test_a_missing_field_reads_as_unknown_not_zero() -> None:
    """A console that reports 0 dBm because it could not find the field is worse
    than one that reports nothing."""
    status = parse_status({"wireless": {"essid": "x"}})
    assert status.connected is True
    assert status.signal_dbm is None
    assert status.ccq is None
    assert status.rx_mbps is None


def test_rssi_only_radios_still_give_a_signal() -> None:
    status = parse_status({"wireless": {"essid": "x", "rssi": 33, "noisef": -96}})
    assert status.signal_dbm == -63


def test_a_wrong_password_says_so(radio: str) -> None:
    FakeRadio.accept_login = False
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, "wrong").status()
    assert "username or password" in str(caught.value)


def test_an_unreachable_radio_is_a_sentence_not_a_crash() -> None:
    with pytest.raises(RadioError) as caught:
        AirOsRadio("192.0.2.99:8", USER, PASSWORD).status()
    assert "cannot reach" in str(caught.value)


def test_the_service_never_raises(radio: str) -> None:
    service = RadioService(
        Settings(radio=RadioSettings(host="192.0.2.99:8", username=USER, password=PASSWORD, enabled=True))
    )
    status = service.status()
    assert status["connected"] is False
    assert status["reason"]


def test_a_radio_that_is_not_set_up_says_so() -> None:
    service = RadioService(Settings())
    assert service.status()["connected"] is False
    assert "not set up" in service.status()["reason"]


def test_the_reading_is_cached_so_every_page_does_not_log_in(radio: str) -> None:
    service = RadioService(
        Settings(radio=RadioSettings(host=radio, username=USER, password=PASSWORD, enabled=True))
    )
    first = service.status(now=100.0)
    assert first["connected"] is True
    # A second read inside the cache window must not touch the radio at all.
    FakeRadio.logged_in = False
    FakeRadio.accept_login = False
    assert service.status(now=101.0) == first
    # Past the window it goes back to the radio, and reports the new failure.
    assert service.status(now=200.0)["connected"] is False
