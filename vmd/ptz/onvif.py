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


@dataclass(frozen=True)
class Profile:
    """One media profile: a picture the camera can send, and a lens behind it.

    `source` is the video source token, and it is the field that matters on this
    camera. A multi-spectral head has two sensors behind two lenses on one
    gimbal, and the camera presents them as separate video sources - which is
    the only thing in the whole ONVIF answer that reliably says "these two
    pictures are different lenses" rather than "these two pictures are the same
    lens at two bitrates". Names lie about that constantly: `MainStream` and
    `SubStream` are one lens, and a camera calling its profiles `Profile_1` and
    `Profile_2` has told you nothing at all.
    """

    token: str
    name: str = ""
    source: str = ""
    # Whether this profile carries a PTZ configuration, which is what decides
    # whether it can be zoomed at all. A profile without one answers an
    # AbsoluteMove with a fault - and on a multi-spectral head it is common for
    # only one of the two pictures to have PTZ, so a zoom bar pointed at the
    # other is a control that can never work no matter what is done to it.
    #
    # `None` means the camera did not say, which is not the same as "no": some
    # firmware leaves the configuration out of GetProfiles and still accepts
    # PTZ. Absent is treated as "might", present-and-false as "cannot".
    ptz: bool | None = None

    def can_zoom(self) -> bool:
        """Whether it is worth pointing a zoom control at this profile."""
        return self.ptz is not False


# Words a camera puts in a profile or source name when it means one lens or the
# other. Deliberately short: this is a hint used before falling back to the
# video sources, and a long list of guesses is a long list of ways to point the
# thermal zoom at the visible lens.
THERMAL_WORDS = ("thermal", "therm", "ir", "tir", "lwir")
VISIBLE_WORDS = ("visible", "visual", "vis", "optical", "rgb", "day", "colour", "color")


@dataclass
class PtzCapability:
    """What this camera turned out to support."""

    available: bool = False
    reason: str = ""
    profile: str = ""
    auth: str = ""
    supports_home: bool = False
    services: dict[str, str] = field(default_factory=dict)
    profiles: list[Profile] = field(default_factory=list)
    # Whether the camera will accept "go to this zoom" rather than only "keep
    # zooming while I ask". Absent from a great many cameras, which is why the
    # zoom control has a second way of working and why this is discovered
    # rather than assumed.
    absolute_zoom: bool = False

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "profile": self.profile,
            "auth": self.auth,
            "supports_home": self.supports_home,
            "absolute_zoom": self.absolute_zoom,
            "profiles": [
                {"token": p.token, "name": p.name, "source": p.source} for p in self.profiles
            ],
        }


def read_profiles(text: str) -> list[Profile]:
    """The profiles in a GetProfiles answer, in the order the camera listed them.

    Parsed with regular expressions for the same reason the rest of this file
    is: there is no XML library worth the dependency for four messages, and the
    shape here is fixed by the ONVIF schema rather than by a vendor.

    What is deliberately NOT done is any cleverness about namespace prefixes.
    Cameras answer with `trt:Profiles`, `tt:Profiles` or bare `Profiles`
    depending on the firmware, so the prefix is skipped rather than matched.
    """
    profiles: list[Profile] = []
    blocks = re.findall(r"<(?:\w+:)?Profiles\b(.*?)</(?:\w+:)?Profiles>", text, re.DOTALL)
    # Whether this answer mentions PTZ configurations at all. A camera that
    # never mentions them has not said that its profiles lack PTZ - it has said
    # nothing - and reading silence as "cannot zoom" would take the zoom away
    # from every camera whose firmware leaves the section out.
    says_ptz = "PTZConfiguration" in text
    for block in blocks:
        token = _first(r'token="([^"]+)"', block)
        if not token:
            continue
        profiles.append(
            Profile(
                token=token,
                name=_first(r"<(?:\w+:)?Name>(.*?)</(?:\w+:)?Name>", block) or "",
                source=_first(
                    r"<(?:\w+:)?VideoSourceConfiguration\b.*?"
                    r"<(?:\w+:)?SourceToken>(.*?)</(?:\w+:)?SourceToken>",
                    block,
                )
                or "",
                ptz=("PTZConfiguration" in block) if says_ptz else None,
            )
        )
    if profiles:
        return profiles
    # A camera that answered in one line without the closing tags this expects.
    # One profile is still better than none: it is what the console did before
    # any of this existed, and it steers.
    token = _first(r'token="([^"]+)"', text)
    return [Profile(token=token)] if token else []


def match_profiles(streams: list[str], profiles: list[Profile]) -> dict[str, str]:
    """Which profile belongs to which of the operator's streams.

    The problem this solves is small and the cost of getting it wrong is not.
    The console knows its pictures as "thermal" and "visible" because that is
    what the operator called them in Settings. The camera knows them as opaque
    tokens. Sending the thermal zoom to the visible lens is not an error
    anything reports - the picture the operator is watching simply does not
    respond, which looks exactly like a lost command.

    So, in order of how much each step can be trusted:

    1. **The name says which it is.** `thermal`, `ir`, `visible`, `optical`. When
       a camera bothers to say, believe it.
    2. **The video sources say how many lenses there are.** Two distinct sources
       are two sensors, and the streams left over are paired with them in the
       order both were listed - which is the order every dual-sensor camera
       presents them in, and the order the operator listed his streams in.
    3. **There is only one lens.** Then every stream maps to it and the two zoom
       controls will move the same glass. That is the truth about such a camera
       and it is better shown than hidden.

    Returns a name -> profile token map, leaving out any stream it could not
    place. A missing entry is a zoom control that says it does not know which
    lens it belongs to, which beats one that quietly moves the wrong one.
    """
    if not profiles:
        return {}

    # Only profiles that can actually be zoomed are candidates, when the camera
    # said which those are. This is the difference between a control that is
    # pointed at the wrong lens and one that is pointed at something that is not
    # a lens at all: a profile with no PTZ configuration answers every zoom with
    # a fault, for ever, and on a multi-spectral head it is common for only one
    # of the two pictures to have PTZ.
    #
    # If that leaves nothing - every profile refuses PTZ - the filter is dropped
    # rather than returning an empty map, because a bar pointed at a profile
    # that probably will not work still beats a bar pointed at nothing, and the
    # console says which of those it is either way.
    zoomable = [profile for profile in profiles if profile.can_zoom()]
    profiles = zoomable or profiles

    chosen: dict[str, str] = {}
    taken: set[str] = set()

    def words_of(profile: Profile) -> str:
        return f"{profile.name} {profile.source} {profile.token}".lower()

    for stream in streams:
        wanted = (
            THERMAL_WORDS
            if any(word in stream.lower() for word in THERMAL_WORDS)
            else VISIBLE_WORDS
            if any(word in stream.lower() for word in VISIBLE_WORDS)
            else ()
        )
        if not wanted:
            continue
        # The other lens's words must NOT appear, or "visible" matches a profile
        # called "IR-cut visible" on the thermal head.
        against = VISIBLE_WORDS if wanted is THERMAL_WORDS else THERMAL_WORDS
        for profile in profiles:
            if profile.token in taken:
                continue
            said = words_of(profile)
            if any(_word_in(word, said) for word in wanted) and not any(
                _word_in(word, said) for word in against
            ):
                chosen[stream] = profile.token
                taken.add(profile.token)
                break

    # Whatever is left, paired by lens. One profile per distinct video source,
    # first listed first, because a camera that offers a main and a sub stream
    # per sensor lists them that way and the second of a pair is the same glass.
    per_source: list[Profile] = []
    seen_sources: set[str] = set()
    for profile in profiles:
        key = profile.source or profile.token
        if key in seen_sources:
            continue
        seen_sources.add(key)
        per_source.append(profile)

    spare = [profile for profile in per_source if profile.token not in taken]
    for stream in streams:
        if stream in chosen:
            continue
        if spare:
            profile = spare.pop(0)
            chosen[stream] = profile.token
            taken.add(profile.token)
        elif len(per_source) == 1:
            # One lens, two pictures of it. Say so by mapping both rather than
            # by leaving one control mysteriously dead.
            chosen[stream] = per_source[0].token
    return chosen


def rtsp_path(url: str) -> str:
    """The part of an RTSP address that says WHICH picture, and nothing else.

    Everything before the path is about how to reach the camera and differs
    between two addresses for the same stream: the camera answers GetStreamUri
    with its own idea of its address - sometimes a different host, usually
    without the credentials the operator typed, occasionally on another port.
    What is left, `/ch2`, is the channel, and that is the whole question.

    Any query string goes too. Some firmware appends a session token that is
    different on every call, which would make one picture look like two.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    without_scheme = text.split("://", 1)[-1]
    slash = without_scheme.find("/")
    if slash < 0:
        return ""
    path = without_scheme[slash:]
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path.rstrip("/").lower()


def _word_in(word: str, said: str) -> bool:
    """Whether `word` appears in `said` as a word rather than inside another.

    Short keys make this necessary: "ir" is in "third", "wire" and "direct",
    and a profile called "Direct" is not the thermal lens.
    """
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", said) is not None


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

        found = read_profiles(profiles)
        if not found:
            self.capability = PtzCapability(
                available=False, reason="the camera returned no media profiles"
            )
            return self.capability

        self.capability.profiles = found
        self.capability.profile = found[0].token
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
        # Whether the zoom slider can send the lens somewhere, or can only ask
        # it to keep moving. ONVIF advertises the absolute spaces it accepts in
        # the node; a camera that lists no absolute zoom space and is sent an
        # AbsoluteMove answers with a fault, which on a console means an arrow
        # that reports failure at the moment somebody is trying to see
        # something. Asked once here, remembered, and the zoom control changes
        # what its buttons do rather than trying and failing.
        self.capability.absolute_zoom = "AbsoluteZoomPositionSpace" in nodes
        if "PTZNode" not in nodes:
            # It answered, and what it said was that it has no head.
            self.capability.available = False
            self.capability.reason = (
                "the camera answered but listed no PTZ head, so there is nothing "
                "here to steer"
            )
        return self.capability

    def _profile(self, profile: str | None = None) -> str:
        """The profile a command is addressed to.

        `None` means the camera's first one, which is what every command meant
        before there was more than one lens to address. Naming it explicitly is
        what lets the thermal zoom go to the thermal lens without the pan and
        tilt - one gimbal, shared by both - having to care.
        """
        if profile:
            return profile
        if not self.capability.profile:
            self.connect()
        if not self.capability.profile:
            raise PtzError(self.capability.reason or "the camera has no PTZ profile")
        return self.capability.profile

    def profiles(self) -> list[Profile]:
        """Every media profile the camera offers, discovering them if needed."""
        if not self.capability.profiles:
            self.connect()
        return list(self.capability.profiles)

    def stream_uri(self, profile: str) -> str:
        """The RTSP address this profile is served at, or "" if it will not say.

        The end of the guessing. Which profile is the thermal picture was worked
        out from the profile's NAME and its video source token - inference about
        one vendor's spelling - and on the operator's own camera it came out
        backwards: the slider under the visible picture zoomed the thermal lens
        and the slider under the thermal picture zoomed the visible one.

        This asks the camera the question directly instead. He typed an address
        into Settings, one per picture, copied from the camera itself; the
        camera can say which profile serves that address. `/ch2` matches `/ch2`.
        That is not a heuristic that can be wrong about a vendor - it is the
        camera identifying its own pictures.

        One call per profile, made once at discovery and never again. Two or
        three round trips on a link with nothing spare, in exchange for the
        zoom controls being attached to the right glass.
        """
        body = (
            f'<GetStreamUri xmlns="{MEDIA}">'
            '<StreamSetup xmlns:tt="http://www.onvif.org/ver10/schema">'
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            "</StreamSetup>"
            f"<ProfileToken>{_xml(profile)}</ProfileToken>"
            "</GetStreamUri>"
        )
        try:
            text = self._post("/onvif/media_service", body)
        except PtzError as exc:
            logger.debug("%s: no stream address for %s: %s", self.host, profile, exc)
            return ""
        return _first(r"<(?:\w+:)?Uri>(.*?)</(?:\w+:)?Uri>", text) or ""

    # --------------------------------------------------------------- moving

    def move(self, pan: float, tilt: float, zoom: float = 0.0,
             profile: str | None = None) -> None:
        """Move at a speed, until told to stop.

        Speeds are -1..1, which is what ONVIF uses. Continuous movement is the
        right primitive for a joystick or a held key: the camera keeps going
        while the operator keeps asking, and stops the moment they let go.
        """
        pan, tilt, zoom = (_clamp(v) for v in (pan, tilt, zoom))
        body = (
            f'<ContinuousMove xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile(profile))}</ProfileToken>"
            '<Velocity xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<tt:PanTilt x="{pan:.3f}" y="{tilt:.3f}"/>'
            f'<tt:Zoom x="{zoom:.3f}"/>'
            "</Velocity></ContinuousMove>"
        )
        self._post("/onvif/ptz_service", body, expect="ContinuousMoveResponse")

    def zoom_to(self, where: float, profile: str | None = None) -> None:
        """Send one lens to a zoom, 0.0 wide to 1.0 tele.

        Only the zoom is sent. An AbsoluteMove carrying a PanTilt as well would
        slew the head every time somebody touched a zoom slider, and the head is
        shared between the two lenses: zooming the thermal picture would move
        the visible one off whatever the operator had it pointed at.
        """
        where = max(0.0, min(1.0, float(where)))
        body = (
            f'<AbsoluteMove xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile(profile))}</ProfileToken>"
            '<Position xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<tt:Zoom x="{where:.3f}"/>'
            "</Position></AbsoluteMove>"
        )
        self._post("/onvif/ptz_service", body, expect="AbsoluteMoveResponse")

    def stop(self, profile: str | None = None, pan_tilt: bool = True,
             zoom: bool = True) -> None:
        body = (
            f'<Stop xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile(profile))}</ProfileToken>"
            f"<PanTilt>{'true' if pan_tilt else 'false'}</PanTilt>"
            f"<Zoom>{'true' if zoom else 'false'}</Zoom></Stop>"
        )
        # The one command that must never be believed on faith: a stop that was
        # not carried out is a head left slewing with no key held.
        #
        # Which of the two can be stopped alone matters here: letting go of a
        # zoom button must not also halt a pan the operator is still holding,
        # and they are separate motors on one head.
        self._post("/onvif/ptz_service", body, expect="StopResponse")

    def home(self) -> None:
        body = (
            f'<GotoHomePosition xmlns="{PTZ}">'
            f"<ProfileToken>{_xml(self._profile())}</ProfileToken>"
            "</GotoHomePosition>"
        )
        self._post("/onvif/ptz_service", body, expect="GotoHomePositionResponse")

    def position(self, profile: str | None = None) -> dict | None:
        """Where the head is now, if the camera will say.

        Asked per profile, because the zoom in the answer is that profile's
        lens. The pan and tilt are the same head whichever profile is asked.
        """
        try:
            text = self._post(
                "/onvif/ptz_service",
                f'<GetStatus xmlns="{PTZ}"><ProfileToken>{_xml(self._profile(profile))}'
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
