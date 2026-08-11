"""Moving the camera: ONVIF PTZ, spoken directly over HTTP.

No ONVIF library. They are large, they pull in SOAP stacks and WSDL parsing, and
this machine has no internet to install them on. What PTZ actually needs is four
messages, and the XML for those fits on a page.

Authentication is offered three ways because cameras disagree: HTTP digest,
HTTP basic, and the WS-Security UsernameToken that ONVIF itself specifies. The
first one that is accepted is remembered for the rest of the session, so the
cost is paid once.

Everything here is best-effort by design. A camera that will not move must
produce a sentence on screen, never an exception that stops the console.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# How long one SOAP call may take. The camera is one radio hop away on a private
# link - milliseconds of round trip, not seconds - so a camera that has not begun
# to answer in this long is not answering at all, and six seconds was only ever
# spending the operator's time confirming it.
#
# It still has to be generous enough for the camera's own SOAP parsing while it
# is encoding two streams, which is why it is seconds rather than milliseconds.
# Note what this does *not* multiply by: the three authentication styles are
# tried one after another only when the camera refuses a login, which it does
# immediately. A timeout raises PtzError from the first attempt and stops there,
# so an unreachable camera costs this once per call, not three times.
# 2.0 was wrong, and it was wrong for a reason worth writing down: it was chosen
# from "the camera is one hop away, so a reply is milliseconds". It is one hop
# away over a 15 km radio link carrying video, and when that link is busy an
# ONVIF reply takes seconds. Every arrow key reported "cannot reach the camera:
# timed out" against a camera that was answering perfectly well, just not inside
# two seconds. The operator's steering stopped working and mine kept passing,
# because nothing here has a radio link in it.
TIMEOUT = 8.0

MEDIA = "http://www.onvif.org/ver10/media/wsdl"
PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
DEVICE = "http://www.onvif.org/ver10/device/wsdl"

WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
PASSWORD_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)


class PtzError(Exception):
    """The camera could not be moved, with a sentence explaining why."""


@dataclass
class PtzCapability:
    """What this camera turned out to support."""

    available: bool = False
    reason: str = ""
    profile: str = ""
    auth: str = ""
    supports_home: bool = False
    services: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "profile": self.profile,
            "auth": self.auth,
            "supports_home": self.supports_home,
        }


def _security_header(username: str, password: str) -> str:
    """The ONVIF UsernameToken. Digest, never the plaintext variant."""
    nonce = os.urandom(16)
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = hashlib.sha1(nonce + created.encode("utf-8") + password.encode("utf-8")).digest()
    return (
        f'<s:Header><Security xmlns="{WSSE}" s:mustUnderstand="1">'
        f'<UsernameToken><Username>{_xml(username)}</Username>'
        f'<Password Type="{PASSWORD_DIGEST}">{base64.b64encode(digest).decode()}</Password>'
        f'<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        f'<Created xmlns="{WSU}">{created}</Created>'
        "</UsernameToken></Security></s:Header>"
    )


def _xml(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _envelope(body: str, header: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"{header}<s:Body>{body}</s:Body></s:Envelope>"
    ).encode("utf-8")


def _first(pattern: str, text: str) -> str | None:
    found = re.search(pattern, text, re.DOTALL)
    return found.group(1).strip() if found else None


def _check_answer(text: str, expect: str) -> None:
    """Raise unless this body is the camera saying it did the thing.

    Three things arrive with a 200 and none of them is success:

    * a SOAP Fault, which is how ONVIF refuses, and which many cameras send
      with a 200 rather than a 500;
    * the camera's own web page, because its HTTP server answers every path it
      does not recognise;
    * an empty envelope from something that is not the camera at all.

    A `<CommandResponse/>` element named after the request is the only thing
    that distinguishes the camera having carried the command out.
    """
    if re.search(r"<[^>]*Fault[^>]*>", text):
        raise PtzError(_fault(text) or "the camera refused the command")
    if expect and expect not in text:
        raise PtzError(
            "the camera answered but did not acknowledge the command, so there "
            "is no telling whether it was carried out"
        )


def _fault(text: str) -> str | None:
    """The camera's own words when it refuses, rather than a status code."""
    for pattern in (r"<[^>]*Text[^>]*>(.*?)</[^>]*Text>", r"<[^>]*faultstring[^>]*>(.*?)</"):
        message = _first(pattern, text)
        if message:
            return re.sub(r"<[^>]+>", "", message).strip()
    return None


class OnvifPtz:
    """PTZ over ONVIF for one camera."""

    def __init__(self, host: str, username: str, password: str, port: int = 80) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.capability = PtzCapability()
        self._auth_opener: urllib.request.OpenerDirector | None = None
        self._use_wsse = False
        # The longest this camera has taken to answer, so the log carries a
        # measured figure rather than the two guesses TIMEOUT has been so far.
        self._slowest = 0.0

    # ------------------------------------------------------------------ wire

    @property
    def base(self) -> str:
        port = "" if self.port == 80 else f":{self.port}"
        return f"http://{self.host}{port}"

    def _openers(self):
        """Digest first: it is what most cameras want and the only one that does
        not put the password on the wire in the clear."""
        realm = f"{self.base}/"
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, realm, self.username, self.password)
        # An empty ProxyHandler on every one of them. Without it urllib honours
        # http_proxy, https_proxy and, on Windows, whatever proxy is configured
        # in the registry - which would send this camera's password to a machine
        # that is not the camera. The camera is at the far end of a private radio
        # link; there is no proxy between here and it, and there must not be.
        no_proxy = urllib.request.ProxyHandler({})
        yield "digest", urllib.request.build_opener(
            no_proxy, urllib.request.HTTPDigestAuthHandler(manager)
        )
        yield "basic", urllib.request.build_opener(
            no_proxy, urllib.request.HTTPBasicAuthHandler(manager)
        )
        yield "wsse", urllib.request.build_opener(no_proxy)

    def _post(self, path: str, body: str, expect: str = "") -> str:
        """One SOAP call, trying each authentication style until one is accepted.

        `expect` is the name of the response element this request must come
        back with. It exists because an HTTP 200 from a camera is not the head
        moving: SOAP carries its own refusal, and a device that will not do
        what it was asked - no PTZ right on the account, a profile token that
        is not a PTZ profile, a head on a preset tour - answers with a Fault,
        which plenty of cameras send with a 200. The camera's own web server
        also answers 200 with a login page on any path it does not recognise.
        In every one of those cases the old code returned, `_do` reported
        `ok: True`, and the console told the operator the command had been
        sent while the head sat still.
        """
        attempts = (
            [(self.capability.auth, self._auth_opener)]
            if self._auth_opener is not None
            else list(self._openers())
        )
        last_error = "no response"
        url = f"{self.base}{path}"
        for name, opener in attempts:
            header = _security_header(self.username, self.password) if name == "wsse" else ""
            request = urllib.request.Request(
                url,
                data=_envelope(body, header),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
            try:
                began = time.monotonic()
                with opener.open(request, timeout=TIMEOUT) as response:
                    text = response.read().decode("utf-8", "replace")
                took = time.monotonic() - began
                # How long this camera actually takes, said once and then only
                # when it gets slower than it has been. TIMEOUT was picked twice
                # from reasoning rather than measurement - 6 s from nothing, then
                # 2 s from "one hop away is milliseconds", which cost the
                # operator his steering for an evening. Nobody can choose that
                # number without a figure from the link it runs on, and this is
                # the only place that figure exists.
                if took > self._slowest:
                    self._slowest = took
                    logger.info(
                        "the camera answered %s in %.2f s (allowed %.0f s)",
                        path.rsplit("/", 1)[-1] or path, took, TIMEOUT,
                    )
                # Checked before the login style is remembered: a body that is
                # a fault or a web page is not evidence that this opener is the
                # one that works.
                _check_answer(text, expect)
                self.capability.auth = name
                self._auth_opener = opener
                return text
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")
                fault = _fault(text)
                if exc.code in (400, 401, 403):
                    last_error = fault or f"the camera refused the login ({exc.code})"
                    continue  # try the next authentication style
                raise PtzError(fault or f"the camera answered {exc.code}") from exc
            # Naming the address it actually tried, and the wait it gave up
            # after. "cannot reach 192.168.1.251: timed out" left the operator
            # unable to tell a camera that is off from one that is simply slower
            # than a number chosen here - and the number was the fault.
            except urllib.error.URLError as exc:
                raise PtzError(
                    f"cannot reach {url} after {TIMEOUT:.0f} s: {exc.reason}"
                ) from exc
            except OSError as exc:
                raise PtzError(f"cannot reach {url} after {TIMEOUT:.0f} s: {exc}") from exc
            except ValueError as exc:
                # urllib's basic handler raises ValueError - not HTTPError - when
                # the camera answers a Digest challenge. Unhandled, that escaped
                # as a crash instead of moving on to the next login style.
                last_error = str(exc)
                continue
        raise PtzError(last_error)

    # ------------------------------------------------------------- discovery

    def connect(self) -> PtzCapability:
        """Find the media profile PTZ commands are addressed to."""
        try:
            profiles = self._post("/onvif/media_service", f'<GetProfiles xmlns="{MEDIA}"/>')
        except PtzError as exc:
            self.capability = PtzCapability(available=False, reason=str(exc))
            return self.capability

        token = _first(r'token="([^"]+)"', profiles)
        if not token:
            self.capability = PtzCapability(
                available=False, reason="the camera returned no media profiles"
            )
            return self.capability

        self.capability.profile = token
        self.capability.available = True
        self.capability.reason = "ready"

        # A media profile is a video stream, not a motor. Every fixed camera
        # on earth answers GetProfiles, so deciding "available" on that alone
        # put the arrows on the console for a head that does not exist, and the
        # operator pressed them and watched nothing happen. GetNodes is the
        # question about the motor.
        #
        # Presets are optional; a camera without a home position is still
        # steerable.
        try:
            nodes = self._post("/onvif/ptz_service", f'<GetNodes xmlns="{PTZ}"/>')
        except PtzError as exc:
            # Not every camera answers GetNodes, and concluding "no PTZ" from
            # silence would take the arrows away from cameras that have a head.
            # Unknown is a state, and it is this one.
            self.capability.supports_home = False
            logger.debug("%s: could not list PTZ nodes: %s", self.host, exc)
            return self.capability

        self.capability.supports_home = "HomeSupported>true" in nodes.replace(" ", "")
        if "PTZNode" not in nodes:
            # It answered, and what it said was that it has no head.
            self.capability.available = False
            self.capability.reason = (
                "the camera answered but listed no PTZ head, so there is nothing "
                "here to steer"
            )
        return self.capability

    def _profile(self) -> str:
        if not self.capability.profile:
            self.connect()
        if not self.capability.profile:
            raise PtzError(self.capability.reason or "the camera has no PTZ profile")
        return self.capability.profile

    # --------------------------------------------------------------- moving

    def move(self, pan: float, tilt: float, zoom: float = 0.0) -> None:
        """Move at a speed, until told to stop.

        Speeds are -1..1, which is what ONVIF uses. Continuous movement is the
        right primitive for a joystick or a held key: the camera keeps going
        while the operator keeps asking, and stops the moment they let go.
        """
        pan, tilt, zoom = (_clamp(v) for v in (pan, tilt, zoom))
        body = (
            f'<ContinuousMove xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile())}</ProfileToken>"
            '<Velocity xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<tt:PanTilt x="{pan:.3f}" y="{tilt:.3f}"/>'
            f'<tt:Zoom x="{zoom:.3f}"/>'
            "</Velocity></ContinuousMove>"
        )
        self._post("/onvif/ptz_service", body, expect="ContinuousMoveResponse")

    def stop(self) -> None:
        body = (
            f'<Stop xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile())}</ProfileToken>"
            "<PanTilt>true</PanTilt><Zoom>true</Zoom></Stop>"
        )
        # The one command that must never be believed on faith: a stop that was
        # not carried out is a head left slewing with no key held.
        self._post("/onvif/ptz_service", body, expect="StopResponse")

    def home(self) -> None:
        body = (
            f'<GotoHomePosition xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile())}</ProfileToken>"
            "</GotoHomePosition>"
        )
        self._post("/onvif/ptz_service", body, expect="GotoHomePositionResponse")

    def position(self) -> dict | None:
        """Where the head is now, if the camera will say."""
        try:
            text = self._post(
                "/onvif/ptz_service",
                f'<GetStatus xmlns="{PTZ}"><ProfileToken>{_xml(self._profile())}'
                "</ProfileToken></GetStatus>",
            )
        except PtzError:
            return None
        pan = _first(r'PanTilt[^>]*\sx="([^"]+)"', text)
        tilt = _first(r'PanTilt[^>]*\sy="([^"]+)"', text)
        zoom = _first(r'Zoom[^>]*\sx="([^"]+)"', text)
        if pan is None and zoom is None:
            return None
        return {
            "pan": _to_float(pan),
            "tilt": _to_float(tilt),
            "zoom": _to_float(zoom),
        }


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


def _to_float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None
