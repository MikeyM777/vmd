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
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TIMEOUT = 6.0

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

    def _post(self, path: str, body: str) -> str:
        """One SOAP call, trying each authentication style until one is accepted."""
        attempts = (
            [(self.capability.auth, self._auth_opener)]
            if self._auth_opener is not None
            else list(self._openers())
        )
        last_error = "no response"
        for name, opener in attempts:
            header = _security_header(self.username, self.password) if name == "wsse" else ""
            request = urllib.request.Request(
                f"{self.base}{path}",
                data=_envelope(body, header),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
            try:
                with opener.open(request, timeout=TIMEOUT) as response:
                    text = response.read().decode("utf-8", "replace")
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
            except urllib.error.URLError as exc:
                raise PtzError(f"cannot reach {self.host}: {exc.reason}") from exc
            except OSError as exc:
                raise PtzError(f"cannot reach {self.host}: {exc}") from exc
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

        # Presets are optional; a camera without a home position is still steerable.
        try:
            nodes = self._post("/onvif/ptz_service", f'<GetNodes xmlns="{PTZ}"/>')
            self.capability.supports_home = "HomeSupported>true" in nodes.replace(" ", "")
        except PtzError:
            self.capability.supports_home = False
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
        self._post("/onvif/ptz_service", body)

    def stop(self) -> None:
        body = (
            f'<Stop xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile())}</ProfileToken>"
            "<PanTilt>true</PanTilt><Zoom>true</Zoom></Stop>"
        )
        self._post("/onvif/ptz_service", body)

    def home(self) -> None:
        body = (
            f'<GotoHomePosition xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile())}</ProfileToken>"
            "</GotoHomePosition>"
        )
        self._post("/onvif/ptz_service", body)

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
