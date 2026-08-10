"""Reading the Ubiquiti radio, so the link stops being invisible.

Every bandwidth problem this system has had was a link problem, and the console
could say nothing about the link at all - the panel showed dashes because
nothing ever read the radio. This talks to airOS the way its own web interface
does: a form login that sets a cookie, then status.cgi, which returns JSON.

Read-only. Nothing here changes a radio setting; a console that can reconfigure
the link it depends on is a console that can cut itself off.
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TIMEOUT = 6.0


class RadioError(Exception):
    """The radio could not be read, with a sentence saying why."""


@dataclass
class LinkStatus:
    """What the radio says about the link, as far as it will say it."""

    connected: bool
    reason: str = ""
    signal_dbm: int | None = None
    noise_dbm: int | None = None
    ccq: float | None = None
    tx_mbps: float | None = None
    rx_mbps: float | None = None
    tx_capacity_mbps: float | None = None
    rx_capacity_mbps: float | None = None
    distance_m: int | None = None
    uptime_s: int | None = None
    device: str = ""

    def as_dict(self) -> dict:
        return {
            "connected": self.connected,
            "reason": self.reason,
            "signal_dbm": self.signal_dbm,
            "noise_dbm": self.noise_dbm,
            "ccq": self.ccq,
            "tx_mbps": self.tx_mbps,
            "rx_mbps": self.rx_mbps,
            "tx_capacity_mbps": self.tx_capacity_mbps,
            "rx_capacity_mbps": self.rx_capacity_mbps,
            "distance_m": self.distance_m,
            "uptime_s": self.uptime_s,
            "device": self.device,
        }


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def parse_status(payload: dict) -> LinkStatus:
    """Pull what matters out of airOS status JSON.

    Defensive on purpose: the shape differs between airOS versions and between
    models, and a missing field must read as unknown rather than as zero. A
    console that reports 0 dBm because it could not find the field is worse than
    one that reports nothing.
    """
    wireless = payload.get("wireless") or {}
    host = payload.get("host") or {}
    throughput = wireless.get("throughput") or {}
    polling = wireless.get("polling") or {}

    signal = _int(wireless.get("signal"))
    # Some builds report only rssi (a positive number above the noise floor).
    noise = _int(wireless.get("noisef") or wireless.get("noise"))
    if signal is None and wireless.get("rssi") is not None and noise is not None:
        signal = noise + _int(wireless.get("rssi"))

    return LinkStatus(
        connected=bool(wireless.get("essid")) or _int(wireless.get("count")) not in (None, 0),
        signal_dbm=signal,
        noise_dbm=noise,
        ccq=_number(wireless.get("ccq")),
        # throughput is in kbps in airOS; rates are the negotiated link speed.
        tx_mbps=(_number(throughput.get("tx")) or 0) / 1000 if throughput.get("tx") is not None else None,
        rx_mbps=(_number(throughput.get("rx")) or 0) / 1000 if throughput.get("rx") is not None else None,
        tx_capacity_mbps=_number(polling.get("dl_capacity") or wireless.get("txrate")),
        rx_capacity_mbps=_number(polling.get("ul_capacity") or wireless.get("rxrate")),
        distance_m=_int(wireless.get("distance")),
        uptime_s=_int(host.get("uptime")),
        device=str(host.get("hostname") or host.get("devmodel") or ""),
    )


class AirOsRadio:
    """One Ubiquiti radio, read over its web interface."""

    def __init__(self, host: str, username: str, password: str) -> None:
        self.host = host.strip()
        self.username = username
        self.password = password
        self._opener: urllib.request.OpenerDirector | None = None
        self._scheme = "https"

    def _build_opener(self) -> urllib.request.OpenerDirector:
        # airOS ships a self-signed certificate for an address that is not in
        # it. Verifying it would fail on every radio ever deployed, and there is
        # nothing to gain: this is a cable to a device on the operator's desk.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(jar),
        )

    def _login(self) -> urllib.request.OpenerDirector:
        if not self.host:
            raise RadioError("no radio address set")
        errors = []
        for scheme in ("https", "http"):
            opener = self._build_opener()
            data = urllib.parse.urlencode(
                {"username": self.username, "password": self.password, "uri": "/"}
            ).encode()
            try:
                request = urllib.request.Request(
                    f"{scheme}://{self.host}/login.cgi",
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                opener.open(request, timeout=TIMEOUT).read()
            except urllib.error.HTTPError as exc:
                if exc.code not in (401, 403):
                    errors.append(f"{scheme}: {exc.code}")
                    continue
                raise RadioError("the radio refused the username or password") from exc
            except (urllib.error.URLError, OSError) as exc:
                errors.append(f"{scheme}: {exc}")
                continue
            self._scheme = scheme
            self._opener = opener
            return opener
        raise RadioError(f"cannot reach {self.host} ({'; '.join(errors) or 'no answer'})")

    def status(self) -> LinkStatus:
        """Read the link, logging in first if needed."""
        for attempt in (1, 2):
            opener = self._opener or self._login()
            try:
                with opener.open(
                    f"{self._scheme}://{self.host}/status.cgi", timeout=TIMEOUT
                ) as response:
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                # The session expired; log in again once before giving up.
                self._opener = None
                if attempt == 2:
                    raise RadioError(f"the radio answered {exc.code}") from exc
                continue
            except (urllib.error.URLError, OSError) as exc:
                self._opener = None
                raise RadioError(f"cannot reach {self.host}: {exc}") from exc

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                # A login page instead of JSON means the session was not accepted.
                self._opener = None
                if attempt == 2:
                    raise RadioError("the radio did not return status; check the login")
                continue
            return parse_status(payload)
        raise RadioError("the radio did not return status")
