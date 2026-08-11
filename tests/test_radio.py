"""Reading the Ubiquiti radio: what it says, and what it does when it will not."""

from __future__ import annotations

import json
import threading
import urllib.request
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


def test_the_capacity_is_in_megabits_whichever_field_reported_it() -> None:
    """The two fields are not in the same unit, and reading them as though they
    were is what makes the one view that explains the stuttering useless.

    airOS reports the airMAX polling capacity in kbps - the same unit as the
    throughput beside it - and txrate/rxrate in Mb/s. Read as one unit, a 24 Mb/s
    link reads as 24000 and 4 Mb/s of video reads as 0.02% of the link at exactly
    the moment the picture is breaking up.
    """
    polled = parse_status(
        {"wireless": {"essid": "x", "polling": {"dl_capacity": 24000, "ul_capacity": 18000}}}
    )
    assert polled.tx_capacity_mbps == 24.0
    assert polled.rx_capacity_mbps == 18.0

    rated = parse_status({"wireless": {"essid": "x", "txrate": 130, "rxrate": 117}})
    assert rated.tx_capacity_mbps == 130.0
    assert rated.rx_capacity_mbps == 117.0

    assert parse_status({"wireless": {"essid": "x"}}).tx_capacity_mbps is None


def test_a_wrong_password_says_so(radio: str) -> None:
    FakeRadio.accept_login = False
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, "wrong").status()
    assert "username or password" in str(caught.value)


def test_an_unreachable_radio_is_a_sentence_not_a_crash() -> None:
    with pytest.raises(RadioError) as caught:
        AirOsRadio("192.0.2.99:8", USER, PASSWORD).status()
    assert "cannot reach" in str(caught.value)


# -------------------------------------------------- the reading, off the thread
#
# The reading used to be taken on whatever thread called status(), which in the
# console is the thread that draws the window, on a two-second heartbeat. An
# unreachable radio costs about 12 s of login timeouts before it says so, and
# nothing repainted while it did - so the console went blind at exactly the
# moment the link had dropped. Every wait below is bounded independently of the
# service, so a regression fails the test rather than hanging the suite.

PATIENCE = 15.0
WEDGE_CEILING = 5.0


def until(predicate, timeout: float = PATIENCE) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def service_for(host: str) -> RadioService:
    return RadioService(
        Settings(
            radio=RadioSettings(host=host, username=USER, password=PASSWORD, enabled=True)
        )
    )


def test_the_service_never_raises(radio: str) -> None:
    service = service_for("192.0.2.99:8")
    try:
        assert until(lambda: service.status().get("checking") is not True)
        status = service.status()
        assert status["connected"] is False
        assert status["reason"]
    finally:
        service.close()


def test_a_radio_that_is_not_set_up_says_so() -> None:
    service = RadioService(Settings())
    assert service.status()["connected"] is False
    assert "not set up" in service.status()["reason"]


def test_it_reads_the_link_without_the_caller_waiting(radio: str) -> None:
    service = service_for(radio)
    try:
        assert until(lambda: service.status().get("connected") is True)
        assert service.status()["signal_dbm"] == -63
    finally:
        service.close()


def test_the_reading_is_cached_so_every_heartbeat_does_not_log_in(radio: str) -> None:
    service = service_for(radio)
    try:
        assert until(lambda: service.status().get("connected") is True)
        # Inside the window it must not touch the radio at all, so a radio that
        # has started refusing logins cannot change the answer yet.
        FakeRadio.logged_in = False
        FakeRadio.accept_login = False
        for _ in range(20):
            assert service.status()["connected"] is True
        # Past the window it goes back to the radio and reports the new failure.
        assert until(lambda: service.status().get("connected") is False)
    finally:
        service.close()


def test_a_radio_that_has_not_answered_yet_says_checking_rather_than_nothing() -> None:
    """A blank reads as "the radio has nothing to report", which is what this
    console says when the link is fine. It must not be what it says when it has
    not managed to ask."""
    wedged = WedgedRadio()
    service = _service_around(wedged)
    try:
        status = service.status()
        assert status["checking"] is True
        assert "checking" in status["reason"]
        assert status["connected"] is False
    finally:
        wedged.released.set()
        service.close()


def test_reading_the_radio_does_not_hold_up_the_caller() -> None:
    import time

    wedged = WedgedRadio()
    service = _service_around(wedged)
    try:
        started = time.monotonic()
        for _ in range(10):  # twenty seconds of heartbeats
            service.status()
        elapsed = time.monotonic() - started
    finally:
        wedged.released.set()
        service.close()
    assert elapsed < 0.5, f"ten heartbeats cost {elapsed:.2f} s on a wedged radio"


def test_a_slow_reading_is_not_started_again_on_every_call() -> None:
    """A radio that is not answering costs both login timeouts before it says
    so - longer than the cache window. A cache stamped with the time the read
    started is expired the moment it is written, so every caller starts another
    one, and one slow answer becomes a permanent stream of them."""
    wedged = WedgedRadio()
    service = _service_around(wedged)
    try:
        for _ in range(20):
            service.status()
        assert wedged.entered.wait(PATIENCE)
        import time

        time.sleep(0.2)  # longer than nothing, well inside the wedge
        assert wedged.calls == 1, f"{wedged.calls} logins were started at once"
    finally:
        wedged.released.set()
        service.close()


def test_a_reading_that_has_gone_stale_carries_its_age(radio: str) -> None:
    """Never a stale value presented as current."""
    service = service_for(radio)
    try:
        assert until(lambda: service.status().get("connected") is True)
        assert service.status()["age_seconds"] >= 0.0
    finally:
        service.close()


class WedgedRadio:
    """A radio that does not answer until the test lets it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()
        self.calls = 0

    def status(self):
        self.calls += 1
        self.entered.set()
        self.released.wait(WEDGE_CEILING)
        raise RadioError("cannot reach 10.0.0.9")


def _service_around(radio_object) -> RadioService:
    """A service whose radio is the object handed in, wired the way apply does."""
    from vmd.background import BackgroundValue
    from vmd.radio.service import CACHE_SECONDS, _reader

    service = RadioService(Settings())
    service.radio = radio_object
    service._reading = BackgroundValue(
        read=_reader(radio_object), stale_after=CACHE_SECONDS, name="the radio"
    )
    return service


def test_the_radio_is_never_reached_through_a_proxy(monkeypatch) -> None:
    """urllib honours http_proxy and, on Windows, the registry's proxy settings.

    The radio is a cable away on the operator's desk. A proxy variable picked up
    from the environment would post its password to whatever that names, and on
    an air-gapped machine that is traffic that should not exist at all.
    """
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    opener = AirOsRadio("10.0.0.9", USER, PASSWORD)._build_opener()
    # An empty ProxyHandler registers no methods, so build_opener leaves it out
    # of `handlers` entirely - what matters is that it displaced the default one
    # that would have been built from the environment.
    routed = [
        handler.proxies
        for handler in opener.handlers
        if isinstance(handler, urllib.request.ProxyHandler) and handler.proxies
    ]
    assert routed == [], f"the radio would be reached through {routed}"
