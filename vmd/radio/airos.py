"""Reading the Ubiquiti radio, so the link stops being invisible.

Every bandwidth problem this system has had was a link problem, and the console
could say nothing about the link at all - the panel showed dashes because
nothing ever read the radio. This talks to airOS the way its own web interface
does: a form login that sets a cookie, then status.cgi, which returns JSON.

This has now been pointed at a real radio - `lighttpd/1.4.54` airOS, by
`spike/probe_radio.py` - and two things came back from it.

The **session flow is right**: the GET of the login page set a cookie, the POST
carried it and was answered, and the cold POST without it was refused with
"Missing session id". That part is no longer a guess.

The **403 was not the password being refused, and it was not a mystery either**.
The radio answered the login itself with HTTP 200 and the words `Invalid
credentials.` in the body, set no new session cookie, and only then refused
status.cgi with 403 - and this file, judging the login on its status code alone,
believed it was in and reported the later 403 as something that "need not mean
the password is wrong". It did mean that, and the radio had said so. A login is
now checked against what came back rather than against its code: see
`check_login`, and the third case in `_refusal` that passes the radio's own words
on to the operator.

401 and 403 are still reported as the different things they are, because airOS
answers 403 for several reasons that are not the password: a POST without the
session cookie, a POST that does not look like it came from the login page, a
lockout after repeated tries.

Read-only. Nothing here changes a radio setting; a console that can reconfigure
the link it depends on is a console that can cut itself off.
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

TIMEOUT = 6.0

# How much of a login answer is read before deciding what it says. A login page
# is tens of kilobytes of HTML and nothing after this changes the verdict.
LOGIN_BODY_LIMIT = 8000

# The longest run of a device's own words that counts as a sentence about the
# login. Past this it is a page, not an answer, and quoting it on the link panel
# would be quoting a form at the operator.
SAID_LIMIT = 200

# And the longest quotation that reaches the operator, once it is a sentence.
QUOTE_LIMIT = 120

# Where an airOS API answer puts its refusal. Order is the order they are read.
REFUSAL_KEYS = ("error", "message", "detail", "reason")

# Answers that say nothing. A login that replies "ok" has not named a failure,
# and a check that read it as one would break every firmware that does this.
NOT_A_REFUSAL = ("", "ok", "success", "true", "1", "none", "forbidden", "unauthorized")

LOGIN_PATH = "/login.cgi"
# airOS 8 logs in through its own API rather than the form. python-airos, the
# library behind the Home Assistant integration, posts JSON here and falls back
# to login.cgi on a 404, which is the order this file uses in reverse: the form
# is what this console was written against, so it is tried first and this is the
# last thing tried before giving up.
API_LOGIN_PATH = "/api/auth"

# Headers of a login response that say something a reader can act on. Kept here
# rather than in the spike so the two cannot drift apart.
TELLING_HEADERS = (
    "Set-Cookie",
    "Location",
    "WWW-Authenticate",
    "Server",
    "X-CSRF-ID",
    "Content-Type",
)

REDACTED = "***"


class RadioError(Exception):
    """The radio could not be read, with a sentence saying why."""


class LoginRefused(RadioError):
    """The radio answered the login and said, in its own words, that it was not
    accepted - whatever HTTP code it wrapped that in.

    Carries the words, because they are the most useful sentence the radio has:
    `Invalid credentials.` is what a non-technical operator needs, and it is what
    the console spent a morning failing to pass on.
    """

    def __init__(self, said: str = "") -> None:
        super().__init__(said or "the radio did not accept the login")
        self.said = said


def password_forms(password: str) -> list[str]:
    """Every form this program can write a password in on the way out.

    Four encodings, longest first, and each of them has been the one that leaked:

    * **as typed.** The obvious one, and the only one the first version masked.
    * **percent-encoded**, `safe=""` on purpose: the default leaves "/" alone,
      which would leave half of a password containing a slash on the screen. A
      form login is posted encoded, so a radio that echoes what it was sent
      shows `p%40ss`, not `p@ss`.
    * **form-encoded**, which is the same thing except that a space becomes `+`
      rather than `%20`. `urlencode` uses this one; `quote` does not.
    * **JSON-escaped.** `_login_api` posts `json.dumps(...)`, which writes `"` as
      `\\"`, `\\` as `\\\\` and every non-ASCII character as `\\uXXXX`. A password
      containing any of those left this program **fully intact** through the API
      login: the string that landed in the output was not the string being
      searched for. Both `ensure_ascii` settings are covered, because a radio
      that echoes JSON of its own may have written it either way, and the
      `\\uXXXX` escapes are covered in upper case too - the hex case is the
      encoder's choice and not the password's.

    And one more, because a report is written by whatever is to hand:

    * **Python-escaped**, which is what `repr()` and `%r` write. Backslashes are
      doubled and the quote character in use is escaped. `spike/probe_radio.py`
      prints every value the radio sent with `!r`, so a password echoed back
      inside one arrives in the report as `a b"c\\\\d` - readable, and not the
      string anybody is searching for.

    Longest first so that a password which is a prefix of its own encoding
    cannot half-mask the longer one: `\\` would otherwise eat the `\\\\` that
    JSON wrote for it and leave a stray backslash behind.
    """
    escaped = json.dumps(password)[1:-1]
    doubled = password.replace("\\", "\\\\")
    forms = {
        password,
        urllib.parse.quote(password, safe=""),
        urllib.parse.quote_plus(password),
        escaped,
        json.dumps(password, ensure_ascii=False)[1:-1],
        re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: "\\u" + m.group(1).upper(), escaped),
        # repr() picks its own quote character, so both of its answers count.
        doubled,
        doubled.replace("'", "\\'"),
    }
    return sorted((form for form in forms if form), key=len, reverse=True)


def redact(text: str, password: str) -> str:
    """Hide the password in every form this program could have written it.

    Not belt and braces: masking one encoding and printing another is printing
    the password, and that has now happened twice here - once percent-encoded,
    once JSON-escaped. See `password_forms` for what "every form" means and why
    each one is in the list.
    """
    if not password:
        return text
    for form in password_forms(password):
        text = text.replace(form, REDACTED)
    return text


def refusal_words(body: str) -> str:
    """What the radio called its own refusal, if the answer names one.

    Two shapes, because the radio answers in two: `/api/auth` refuses in JSON -
    `{"error":"Invalid credentials."}` - and `login.cgi` refuses in the body of
    an otherwise ordinary page, as plain text.

    Deliberately not a search for an English string. This firmware is one build
    of many and the next one may refuse in another language: what is matched is
    the SHAPE of an answer that names something - a JSON field meant for a
    reason, or a body short enough to be a sentence rather than a page. A whole
    login form re-served is not a quote, and returns nothing.
    """
    text = (body or "")[:LOGIN_BODY_LIMIT]
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in REFUSAL_KEYS:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return _quoted(value)
            # JSON that names nothing is not a refusal, and reading its braces as
            # a sentence would make one up.
            return ""
    said = " ".join(re.sub(r"<[^>]+>", " ", text).split())
    if len(said) > SAID_LIMIT or said.strip(" .!").lower() in NOT_A_REFUSAL:
        return ""
    return _quoted(said)


def _quoted(said: str) -> str:
    """A device's own words, collapsed and cut to something a status line can
    carry. Never trusted to be short: this is text from a device."""
    words = " ".join(said.split())
    return words[:QUOTE_LIMIT] + "..." if len(words) > QUOTE_LIMIT else words


def login_accepted(body: str, headers) -> bool:
    """Whether an answer to a login POST is evidence that a login happened.

    Three signals, any one of which is enough, and none of which is a word:

    * a `Set-Cookie` - the radio issued a session. On the real device the POST
      that worked came back with a fresh `AIROS_...` value and the one that was
      refused came back with none;
    * a `Location` - it redirected, which is what a login does;
    * a meta refresh in the page, which is that same redirect written in HTML.
      It is what the observed firmware actually sends.

    Anything else is not evidence either way, and this returns False so that the
    body gets a say. It is not "the login failed" - see `check_login`.
    """
    if headers is None:
        headers = {}
    if headers.get("Set-Cookie") or headers.get("Location"):
        return True
    head = (body or "")[:LOGIN_BODY_LIMIT].lower()
    return "http-equiv" in head and "refresh" in head


def check_login(body: str, headers) -> None:
    """Raise if the radio answered the login and refused it.

    **HTTP 200 is not evidence of a login.** The device this was first pointed at
    answers a wrong password with 200, sets no new cookie, and says `Invalid
    credentials.` in the body - and the console, judging on the code alone,
    believed it was in. The failure then surfaced as an unexplained 403 from
    status.cgi and the operator was told the radio "refused the request, which
    need not mean the password is wrong", when the radio had already said in
    those words that it was.

    Nothing is claimed without evidence, in either direction: a refusal is
    declared only when the body names one AND the answer carries none of the
    marks of a login that took. A firmware that says nothing either way is left
    exactly as it was, with status.cgi as the thing that finds out.
    """
    if login_accepted(body, headers):
        return
    said = refusal_words(body)
    if said:
        raise LoginRefused(said)


class _HiddenFields(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() != "input":
            return
        found = {name.lower(): (value or "") for name, value in attrs}
        if found.get("type", "").lower() != "hidden":
            return
        name = found.get("name")
        if name:
            self.fields[name] = found.get("value", "")


def hidden_fields(html: str) -> dict[str, str]:
    """Every hidden input on a page, by name.

    This is where a CSRF token lives. Several airOS builds put one in the login
    form and refuse - with 403 - a POST that does not send it back, whatever the
    password is. Whether the owner's radio is one of them is not known here;
    what is known is that sending them back costs nothing on a radio that does
    not want them, and that not sending them is unrecoverable on one that does.

    Never raises: a page that is not a login page, or not HTML at all, is worth
    an empty answer and a login attempt without a token, not an exception on the
    way to reading a link.
    """
    parser = _HiddenFields()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML is not a reason to give up
        logger.debug("the login page could not be parsed for hidden fields", exc_info=True)
    return parser.fields


@dataclass
class LinkStatus:
    """What the radio says about the link, as far as it will say it."""

    connected: bool
    reason: str = ""
    signal_dbm: int | None = None
    # What the OTHER radio hears. A point-to-point link can be strong one way
    # and weak the other - a dish knocked out of alignment at one end does
    # exactly that - and until this was read the asymmetry was invisible.
    remote_signal_dbm: int | None = None
    noise_dbm: int | None = None
    # The raw airOS field, kept as it came for the probe's report. `ccq` does
    # not exist on the firmware that was measured; `quality_percent` is the one
    # to read, and it is normalised to 0-100 whichever field filled it.
    ccq: float | None = None
    quality_percent: float | None = None
    # Airtime: the share of the medium's TIME that is spent, which is what
    # actually fills a wireless link. See `parse_status`.
    airtime_percent: float | None = None
    rx_airtime_percent: float | None = None
    tx_airtime_percent: float | None = None
    tx_mbps: float | None = None
    rx_mbps: float | None = None
    tx_capacity_mbps: float | None = None
    rx_capacity_mbps: float | None = None
    uptime_s: int | None = None
    device: str = ""

    def as_dict(self) -> dict:
        return {
            "connected": self.connected,
            "reason": self.reason,
            "signal_dbm": self.signal_dbm,
            "remote_signal_dbm": self.remote_signal_dbm,
            "noise_dbm": self.noise_dbm,
            "ccq": self.ccq,
            "quality_percent": self.quality_percent,
            "airtime_percent": self.airtime_percent,
            "rx_airtime_percent": self.rx_airtime_percent,
            "tx_airtime_percent": self.tx_airtime_percent,
            "tx_mbps": self.tx_mbps,
            "rx_mbps": self.rx_mbps,
            "tx_capacity_mbps": self.tx_capacity_mbps,
            "rx_capacity_mbps": self.rx_capacity_mbps,
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


def _capacity(polled, rate) -> float | None:
    """The negotiated capacity in Mb/s, from whichever field reported it.

    `polling.*_capacity` is kbps and `txrate`/`rxrate` are already Mb/s. That
    was an assumption when it was written and it is now CONFIRMED: the radio
    that was read reports `dl_capacity: 194400` beside a `throughput.rx` of
    `10692` for 10.7 Mb/s of camera video, so both are kbps and the ratio
    between them is the one airMAX claims.
    """
    kbps = _number(polled)
    if kbps:
        return kbps / 1000.0
    return _number(rate)


def _mbps(kbps) -> float | None:
    """kbps as Mb/s, and nothing at all when the field is absent.

    Explicitly not `or 0`: a throughput of nought is a quiet link and a missing
    throughput is a radio that did not say, and they may not read the same.
    """
    number = _number(kbps)
    return None if number is None else number / 1000.0


def _percent(value) -> float | None:
    """A figure the radio already reports as a percentage, left on that scale."""
    return _number(value)


def _station(wireless: dict) -> tuple[dict, bool]:
    """The one station entry that matters, and whether the list was there.

    On a point-to-point link `wireless.sta` holds exactly one entry - the radio
    at the other end - and on this firmware it is where the signal lives. The
    second half of the answer is what tells an EMPTY list apart from a firmware
    that has no such list at all: the first is the link being down, and the
    second is only a shape this file has not met.
    """
    stations = wireless.get("sta")
    if not isinstance(stations, list):
        return {}, False
    first = stations[0] if stations else None
    return (first if isinstance(first, dict) else {}), True


def _above_noise(source: dict, noise: int | None) -> int | None:
    """A signal worked out from rssi, which is a count of dB above the noise."""
    rssi = _int(source.get("rssi"))
    floor = _int(source.get("noisefloor"))
    if floor is None:
        floor = noise
    if rssi is None or floor is None:
        return None
    return floor + rssi


def _quality(wireless: dict, station: dict) -> float | None:
    """Link quality as a percentage, from whichever figure this build reports.

    There is no `ccq` on airOS 8.7.11. airMAX reports `dl_avg_linkscore` and
    `ul_avg_linkscore` instead, and those are ALREADY 0-100 - run through the
    divide-by-ten that `ccq` needs they would report a perfect link as 10%.

    The worse of the two directions is the one kept. A link that scores 100 down
    and 40 up is a link with a problem, and a panel showing the average would be
    reporting 70% of a problem it should be naming.
    """
    scores = [
        _number(station.get(key))
        for key in ("dl_avg_linkscore", "ul_avg_linkscore", "dl_linkscore", "ul_linkscore")
        if station.get(key) is not None
    ]
    scores = [score for score in scores if score is not None]
    if scores:
        return min(scores)
    ccq = _number(wireless.get("ccq"))
    if ccq is None:
        return None
    # airOS reports ccq on a 0-1000 scale on the builds that have it at all,
    # and "985%" is not something anyone can read. The scale is decided here
    # rather than in the panel: which field was read is the parser's business.
    return ccq / 10.0 if ccq > 100 else ccq


def parse_status(payload: dict) -> LinkStatus:
    """Pull what matters out of airOS status JSON.

    Written from general knowledge of airOS, and then POINTED AT A REAL RADIO -
    a NanoStation 5AC loco on v8.7.11, in `sta-ptp` mode, the station end of the
    15 km hop this console watches. It reported the signal as unknown, and the
    reason is the shape of the answer:

    * **The signal of a station is not at `wireless.signal`.** That key does not
      exist on this firmware. The per-station entry carries it - `sta[0].signal`
      - because from a station's point of view there is one other radio and that
      is what "the signal" means. An access point reports differently, so both
      are read, in that order.
    * **`sta` may be empty**, and that is not "unknown": it is the radio being
      up with nothing associated to it, which is the link being DOWN. It is
      reported as such, in words, because it is the one state the operator has
      to be told about and a blank signal would have hidden it.
    * **There is no `ccq`.** `dl_avg_linkscore` / `ul_avg_linkscore` are what
      airMAX reports instead, on a 0-100 scale rather than 0-1000.
    * **`polling.use` is the figure that mattered most and was not read at all.**
      See below.
    * **`wireless.distance` is 0 and `sta[0].distance` is 1** on a link that is
      really 15 km, so neither of them is metres and nothing here can say what
      they are. No distance is reported: "Distance: 1 m" on a 15 km path is not
      a smaller error than showing nothing, it is a worse one.

    Defensive on purpose: the shape differs between airOS versions and between
    models, and a missing field must read as unknown rather than as zero. A
    console that reports 0 dBm because it could not find the field is worse than
    one that reports nothing.
    """
    wireless = payload.get("wireless") or {}
    host = payload.get("host") or {}
    throughput = wireless.get("throughput") or {}
    polling = wireless.get("polling") or {}
    station, has_station_list = _station(wireless)
    remote = station.get("remote") or {}

    noise = _int(wireless.get("noisef") or wireless.get("noise"))
    if noise is None:
        noise = _int(station.get("noisefloor"))

    signal = _int(wireless.get("signal"))
    if signal is None:
        signal = _int(station.get("signal"))
    # Some builds report only rssi (a positive number above the noise floor),
    # at either level.
    if signal is None:
        signal = _above_noise(wireless, noise)
    if signal is None:
        signal = _above_noise(station, noise)

    remote_signal = _int(remote.get("signal"))
    if remote_signal is None:
        remote_signal = _above_noise(remote, None)

    # A station list that exists and is empty is the whole answer: the radio is
    # up, the essid is still configured, and there is nothing on the far end.
    associated = station or not has_station_list
    connected = bool(associated) and (
        bool(wireless.get("essid")) or _int(wireless.get("count")) not in (None, 0)
    )
    reason = (
        ""
        if connected or associated
        else "the radio is up but nothing is associated to it: the link is down"
    )

    return LinkStatus(
        connected=connected,
        reason=reason,
        signal_dbm=signal,
        remote_signal_dbm=remote_signal,
        noise_dbm=noise,
        ccq=_number(wireless.get("ccq")),
        quality_percent=_quality(wireless, station),
        # Airtime, and it is the headline. `polling.use` is the percentage of
        # the medium's TIME that is spent, which is what a wireless link runs
        # out of - not bits per second. His link was at 88% while the panel,
        # comparing throughput against an airMAX capacity estimate, reported it
        # as 13% used and looking healthy.
        airtime_percent=_percent(polling.get("use")),
        rx_airtime_percent=_percent(polling.get("rx_use")),
        tx_airtime_percent=_percent(polling.get("tx_use")),
        # kbps, confirmed on the device rather than assumed. See `_capacity`.
        tx_mbps=_mbps(throughput.get("tx")),
        rx_mbps=_mbps(throughput.get("rx")),
        # `dl` and `ul` are the LINK's directions and not this radio's: the
        # downlink runs from the access point to the station. So on a station
        # the downlink is what arrives - which the radio itself confirms, with
        # rx carrying 10.7 Mb/s at 73% of the airtime against tx's 15%, and the
        # far end reporting that it is transmitting that same stream. Read the
        # other way round, the 194 Mb/s estimate sits beside the 0.2 Mb/s of PTZ
        # traffic and the two figures are swapped.
        #
        # A radio that does not say which end it is keeps the old reading: an
        # access point is the other side of the same sentence, and guessing
        # would break the one shape that already worked.
        **_capacities(wireless, polling),
        uptime_s=_int(host.get("uptime")),
        device=str(host.get("hostname") or host.get("devmodel") or ""),
    )


def _capacities(wireless: dict, polling: dict) -> dict:
    """The two capacity figures, named from the end that is reporting them."""
    down = _capacity(polling.get("dl_capacity"), wireless.get("rxrate"))
    up = _capacity(polling.get("ul_capacity"), wireless.get("txrate"))
    if str(wireless.get("mode") or "").lower().startswith("sta"):
        return {"rx_capacity_mbps": down, "tx_capacity_mbps": up}
    # An access point, or a radio that did not say. The downlink is what it
    # sends, which is the reading this file has always had.
    return {
        "tx_capacity_mbps": _capacity(polling.get("dl_capacity"), wireless.get("txrate")),
        "rx_capacity_mbps": _capacity(polling.get("ul_capacity"), wireless.get("rxrate")),
    }


def _telling(headers) -> dict[str, str]:
    """The response headers worth reporting, and only those.

    Set-Cookie is kept whole and repeated headers are joined, because a radio
    that sets two cookies and a client that keeps one is a bug that reads as a
    password problem.
    """
    kept: dict[str, str] = {}
    if headers is None:
        return kept
    for name in TELLING_HEADERS:
        values = headers.get_all(name) if hasattr(headers, "get_all") else None
        if values is None:
            value = headers.get(name)
            values = [value] if value else []
        if values:
            kept[name] = ", ".join(str(value) for value in values)
    return kept


def _words(attempt: LoginAttempt, limit: int = 60) -> str:
    """What the radio called its own refusal, if it said anything but the number.

    airOS builds differ in how much they explain themselves, and "403 CSRF token
    missing" would end this investigation on the spot. Bounded, because a status
    line built from a device's own text is a device's own text - it is quoted
    here, redacted, and never trusted to be short.
    """
    said = " ".join(attempt.detail.split())
    if not said or said.lower() in ("forbidden", "unauthorized", "ok", "none"):
        return ""
    if len(said) > limit:
        said = said[:limit] + "..."
    return f" ({said})"


def _error_body(exc: urllib.error.HTTPError) -> str:
    """The body of a refusal, bounded, or nothing.

    An HTTPError is a response and can be read like one - and on this radio the
    /api/auth refusal puts the only useful sentence there. Guarded: a body that
    will not read is not a reason to lose the code that came with it.
    """
    try:
        return exc.read(LOGIN_BODY_LIMIT).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - the status code is the fallback
        logger.debug("the refusal's body could not be read", exc_info=True)
        return ""


def _carried(headers: dict[str, str], token: str | None) -> dict[str, str]:
    """What a successful login leaves behind that later requests must repeat.

    The cookie is the cookie jar's business. This is the other half: airOS 8
    hands back an X-CSRF-ID and expects it on everything afterwards, and a
    status.cgi sent without it is refused by a session that is perfectly valid.
    """
    csrf = headers.get("X-CSRF-ID") or token
    return {"X-CSRF-ID": csrf} if csrf else {}


def login_fields(username: str, password: str, hidden: dict[str, str] | None = None) -> dict:
    """The form a login POST carries, built on whatever the login page asked for.

    The radio's own hidden fields go first and are then overwritten with the
    credentials, so a token the page supplied survives and a `username` the page
    happened to pre-fill does not. `uri` is only supplied when the page did not:
    airOS uses it as the page to land on after the login, and the radio's own
    idea of that is better than a guess.
    """
    fields = {
        name: value
        for name, value in (hidden or {}).items()
        if name.lower() not in ("username", "password")
    }
    fields["username"] = username
    fields["password"] = password
    fields.setdefault("uri", "/")
    return fields


@dataclass
class LoginAttempt:
    """One try at logging in, and what came back. Nothing here is a guess."""

    scheme: str
    flow: str
    url: str
    status: int | None = None
    detail: str = ""
    headers: dict = field(default_factory=dict)
    # Whether the radio itself said this login was not accepted, as opposed to
    # this end having decided so from a status code. The strongest thing any of
    # these attempts can carry, and the only one that quotes the device.
    refused: bool = False

    def sentence(self) -> str:
        if self.status is not None:
            return f"{self.flow} login to {self.url} answered HTTP {self.status}"
        return f"{self.flow} login to {self.url}: {self.detail or 'no answer'}"


# What "the login" means, in the order it is tried. The names are what the
# console and the probe both report, so that "which one worked" is one word.
#
#   session - GET the login page first, keep the cookie it sets and the hidden
#             fields it carries, then POST with both, from that page's address.
#   cold    - POST with no preceding GET. What this console has always sent, and
#             what the radio in the field answered 403 to.
#   api     - the airOS 8 endpoint, which is JSON and not a form at all.
LOGIN_FLOWS = ("session", "cold", "api")


class AirOsRadio:
    """One Ubiquiti radio, read over its web interface.

    Logging in is three flows and not one, because a 403 from a real radio is
    not enough to tell you which of them it wanted. See `_login`.
    """

    def __init__(self, host: str, username: str, password: str) -> None:
        self.host = host.strip()
        self.username = username
        self.password = password
        self._opener: urllib.request.OpenerDirector | None = None
        self._scheme = "https"
        self._headers: dict[str, str] = {}
        # Which flow got in, once one has. Empty until then. The operator is
        # told this because it is the difference between a radio that wants a
        # session and one that does not, and nobody knows which this one is.
        self.login_method = ""

    def _build_opener(self) -> urllib.request.OpenerDirector:
        # airOS ships a self-signed certificate for an address that is not in
        # it. Verifying it would fail on every radio ever deployed, and there is
        # nothing to gain: this is a cable to a device on the operator's desk.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(
            # No proxy, ever. urllib otherwise honours http_proxy, https_proxy
            # and the Windows registry's proxy settings, which would post this
            # radio's password to whatever they name. The radio is a cable away
            # on the operator's desk; nothing may sit between.
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(jar),
        )

    def _login(self) -> urllib.request.OpenerDirector:
        """Get a session, by whichever of the three flows this radio wants.

        The radio in the field answers 403. The code used to read that as a
        rejected username or password and say so, which is a claim it cannot
        support: airOS answers 403 for several reasons that are not the password
        at all, and the most likely of them is that the POST arrived without the
        session cookie the radio sets on its own login page - which this console
        never fetched. So the session flow is tried first, the cold POST it has
        always sent is kept as a fallback, and which one worked is recorded.

        Nobody here has reached a real radio. That is exactly why this is a list
        of things to try and a record of what each one said, rather than one
        thing and a guess about why it failed - and why the sentence it raises
        names the code and the URL instead of naming a cause.
        """
        if not self.host:
            raise RadioError("no radio address set")
        attempts: list[LoginAttempt] = []
        for scheme in ("https", "http"):
            for flow in LOGIN_FLOWS:
                opener = self._build_opener()
                attempt = LoginAttempt(scheme=scheme, flow=flow, url=self._url(scheme, flow))
                attempts.append(attempt)
                try:
                    headers = getattr(self, f"_login_{flow}")(opener, scheme, attempt)
                except LoginRefused as exc:
                    # The radio answered and said no, in its own words. Another
                    # flow is still tried - a build that refuses the cold POST
                    # with "Missing session id" refuses it in words too - but
                    # what it said is kept, because it is the whole answer.
                    attempt.refused = True
                    attempt.detail = redact(exc.said, self.password)
                    continue
                except urllib.error.HTTPError as exc:
                    # The radio answered. Another flow may still be the one it
                    # wanted, so a 403 here is no longer the end of the road.
                    attempt.status = exc.code
                    attempt.headers = _telling(exc.headers)
                    # The body first: /api/auth refuses in JSON behind a 403, and
                    # `{"error":"Invalid credentials."}` is worth more to the
                    # operator than the word "Forbidden".
                    said = refusal_words(_error_body(exc))
                    attempt.refused = bool(said)
                    attempt.detail = redact(said or str(exc.reason or ""), self.password)
                    continue
                except (urllib.error.URLError, OSError) as exc:
                    # Nothing answered at all, so the other two flows would only
                    # spend another timeout each finding that out. An unreachable
                    # radio has to stay inside the budget the console allows it.
                    attempt.detail = redact(str(exc), self.password)
                    break
                self._scheme = scheme
                self._opener = opener
                self._headers = headers
                self.login_method = flow
                logger.info("logged in to %s over %s by the %s flow", self.host, scheme, flow)
                return opener
        raise RadioError(self._refusal(attempts))

    def _url(self, scheme: str, flow: str) -> str:
        path = API_LOGIN_PATH if flow == "api" else LOGIN_PATH
        return f"{scheme}://{self.host}{path}"

    def _login_session(
        self, opener: urllib.request.OpenerDirector, scheme: str, attempt: LoginAttempt
    ) -> dict[str, str]:
        """GET the login page, then POST from it, carrying what it gave us."""
        page_url = f"{scheme}://{self.host}{LOGIN_PATH}"
        request = urllib.request.Request(page_url, headers={"Accept": "text/html"})
        with opener.open(request, timeout=TIMEOUT) as response:
            page = response.read().decode("utf-8", "replace")
            opened = _telling(response.headers)
        attempt.headers = opened

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            # Some builds refuse a form POST that does not look like it came
            # from their own page. Both are what a browser would send.
            "Referer": page_url,
            "Origin": f"{scheme}://{self.host}",
        }
        token = opened.get("X-CSRF-ID")
        if token:
            headers["X-CSRF-ID"] = token
        data = urllib.parse.urlencode(
            login_fields(self.username, self.password, hidden_fields(page))
        ).encode()
        with opener.open(
            urllib.request.Request(page_url, data=data, headers=headers), timeout=TIMEOUT
        ) as response:
            body = response.read(LOGIN_BODY_LIMIT).decode("utf-8", "replace")
            after = _telling(response.headers)
        attempt.status = 200
        attempt.headers = {**opened, **after}
        # Only what this POST came back with. The cookie the GET set proves the
        # login page was served, not that the login was accepted, and merging the
        # two would make every refusal look like a session.
        check_login(body, after)
        return _carried(after, token)

    def _login_cold(
        self, opener: urllib.request.OpenerDirector, scheme: str, attempt: LoginAttempt
    ) -> dict[str, str]:
        """POST with no preceding GET. What this console has always sent."""
        data = urllib.parse.urlencode(login_fields(self.username, self.password)).encode()
        request = urllib.request.Request(
            f"{scheme}://{self.host}{LOGIN_PATH}",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(LOGIN_BODY_LIMIT).decode("utf-8", "replace")
            after = _telling(response.headers)
        attempt.status = 200
        attempt.headers = after
        check_login(body, after)
        return _carried(after, None)

    def _login_api(
        self, opener: urllib.request.OpenerDirector, scheme: str, attempt: LoginAttempt
    ) -> dict[str, str]:
        """The airOS 8 endpoint: JSON, and a token in a header rather than a form."""
        data = json.dumps({"username": self.username, "password": self.password}).encode()
        request = urllib.request.Request(
            f"{scheme}://{self.host}{API_LOGIN_PATH}",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with opener.open(request, timeout=TIMEOUT) as response:
            body = response.read(LOGIN_BODY_LIMIT).decode("utf-8", "replace")
            after = _telling(response.headers)
        attempt.status = 200
        attempt.headers = after
        check_login(body, after)
        return _carried(after, None)

    def _refusal(self, attempts: list[LoginAttempt]) -> str:
        """One sentence for a login that did not happen, and no claim beyond it.

        The operator reads this on the link panel and has no terminal, so it
        carries the code and the URL and says only what those support.
        """
        answered = [attempt for attempt in attempts if attempt.status is not None]
        # A 404 says "not this endpoint", which is never the interesting answer
        # when another endpoint answered something else. A radio with no login
        # page at /login.cgi and a refusal at /api/auth must report the refusal.
        answered = [attempt for attempt in answered if attempt.status != 404] or answered
        # First, because it is the only one of these that is not this end's
        # reading of a status code: the radio was asked, and it said. Whatever
        # code it wrapped that in, its own words settle the question, and they
        # are the words a non-technical operator can act on.
        named = next(
            (a for a in attempts if a.refused and a.detail),
            None,
        )
        if named is not None:
            code = f"HTTP {named.status} " if named.status is not None else ""
            return redact(
                f'the radio refused the login and said so: "{named.detail}" '
                f"({code}from {named.url}). Those are the radio's own words - "
                "check the username and the password in Settings.",
                self.password,
            )
        challenged = next((a for a in answered if a.status == 401), None)
        if challenged is not None:
            # 401 is an authentication challenge and nothing else. Here the old
            # sentence is fair, and it keeps the code and the URL anyway.
            return redact(
                "the radio refused the username or password (HTTP 401"
                f"{_words(challenged)} from {challenged.url})",
                self.password,
            )
        refused = next((a for a in answered if a.status == 403), None)
        if refused is not None:
            return redact(
                f"the radio answered HTTP 403{_words(refused)} to the login at "
                f"{refused.url}. It is reachable and it refused the request, which "
                "need not mean the password is wrong: airOS also answers 403 to a "
                "login sent without the session cookie from its own login page, to "
                "one that does not look like it came from that page, and after too "
                "many tries. All login flows were tried. Run spike/probe_radio.py "
                "against this radio and send what it prints.",
                self.password,
            )
        if answered:
            worst = answered[0]
            return redact(
                f"the radio answered HTTP {worst.status}{_words(worst)} to the login "
                f"at {worst.url}",
                self.password,
            )
        tried = "; ".join(attempt.sentence() for attempt in attempts) or "no answer"
        return redact(f"cannot reach {self.host} ({tried})", self.password)

    def status(self) -> LinkStatus:
        """Read the link, logging in first if needed."""
        for attempt in (1, 2):
            opener = self._opener or self._login()
            url = f"{self._scheme}://{self.host}/status.cgi"
            try:
                request = urllib.request.Request(url, headers=dict(self._headers))
                with opener.open(request, timeout=TIMEOUT) as response:
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                # The session expired; log in again once before giving up.
                self._opener = None
                if attempt == 2:
                    raise RadioError(
                        redact(
                            f"the radio answered HTTP {exc.code} to {url}, after a "
                            f"login that had been accepted by the {self.login_method} "
                            "flow",
                            self.password,
                        )
                    ) from exc
                continue
            except (urllib.error.URLError, OSError) as exc:
                self._opener = None
                raise RadioError(
                    redact(f"cannot reach {self.host}: {exc}", self.password)
                ) from exc

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
