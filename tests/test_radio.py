"""Reading the Ubiquiti radio: what it says, and what it does when it will not."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vmd.radio import airos
from vmd.radio.airos import (
    REDACTED,
    AirOsRadio,
    RadioError,
    hidden_fields,
    parse_status,
    redact,
)
from vmd.radio.service import RadioService
from vmd.settings import RadioSettings, Settings

USER, PASSWORD = "ubnt", "linkpass"

# ------------------------------------------------------------ the password set
#
# The redaction regression test used to run on `p@ss word/1`, which is drawn
# entirely from the alphabet `redact` already handled - so the test written to
# catch a redaction failure could not catch one. That is the original defect
# repeated: the leak was found in the first place because a password contained
# characters somebody had not thought about, and the replacement chose a
# password made only of characters somebody had.
#
# So the passwords below are chosen for the encodings they force, not for how
# they look, and every redaction test runs over all of them:
#
#   * a quote and a backslash, which JSON escapes and nothing else does;
#   * a space and URL-reserved characters, which the two percent-encodings
#     disagree about;
#   * a percent sign, so a password can be mistaken for its own encoding;
#   * non-ASCII, which `json.dumps` writes as \uXXXX by default - the form in
#     which a password left this program fully intact until it was fixed.
ADVERSARIAL_PASSWORDS = [
    "p@ss word/1",          # the old one: percent-encoded in two disagreeing forms
    'a b"c\\d',             # a quote and a backslash: JSON escapes both
    "pa#ss?q=1&r",          # URL-reserved throughout
    "pw%20x",               # already looks like its own percent-encoding
    "סיסמה",                  # non-ASCII: six \uXXXX escapes once json.dumps has had it
    'a b"c\\d/e?f&g=h%i+j#k סוד',  # all of it at once
]

# What the old test used. Kept under its own name because it is the *weak* case
# and the point is that it is no longer the only one.
TRICKY_PASSWORD = ADVERSARIAL_PASSWORDS[0]

_SHORT_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_JSON_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})|\\(["\\/bfnrt])')


def _json_unescape(text: str) -> str:
    """Undo JSON string escaping on a fragment that is not itself a JSON string.

    A radio that echoes what it was sent hands back the *escaped* password, and
    the console then quotes it. Searching the output for the password as typed
    finds nothing and proves nothing; this is how the output is read back.
    """

    def one(match: re.Match) -> str:
        if match.group(1):
            return chr(int(match.group(1), 16))
        return _SHORT_ESCAPES.get(match.group(2), match.group(2))

    return _JSON_ESCAPE.sub(one, text)


def encodings(password: str) -> dict[str, str]:
    """Every form this program can write a password in on its way to a screen.

    Four, and the fourth is the one that was missed: the airOS 8 login is posted
    as `json.dumps(...)`, which turns `"` into `\\"` and every non-ASCII
    character into `\\uXXXX`, so the string that lands in the output is not the
    string anybody was searching for.
    """
    return {
        "typed": password,
        "percent-encoded": urllib.parse.quote(password, safe=""),
        "form-encoded": urllib.parse.quote_plus(password),
        "JSON-escaped": json.dumps(password)[1:-1],
        "JSON-escaped, non-ASCII kept": json.dumps(password, ensure_ascii=False)[1:-1],
        # What `repr()` and `%r` write. `spike/probe_radio.py` prints every value
        # the radio sent with `!r`, so this is not hypothetical either.
        "Python-escaped": repr(password)[1:-1],
    }


def leaks(text: str, password: str) -> list[str]:
    """Every way the password is still readable in `text`. Empty is the only pass.

    Deliberately not `password not in text`. It asks the question from both
    ends: is any *encoding* of the password present, and does any *decoding* of
    the output yield it - the second of which catches an encoding nobody here
    thought of.
    """
    if not password:
        return []
    found: list[str] = []
    for name, form in encodings(password).items():
        if form and form in text:
            found.append(f"{name}: {form!r}")
    decoded = {
        "percent-decoded": urllib.parse.unquote(text),
        "form-decoded": urllib.parse.unquote_plus(text),
        "JSON-unescaped": _json_unescape(text),
        "JSON-unescaped then percent-decoded": urllib.parse.unquote(_json_unescape(text)),
    }
    for name, plain in decoded.items():
        if password in plain:
            found.append(f"{name}: {plain[:80]!r}")
    return found


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


# The login page a token-requiring airOS serves on GET /login.cgi: a session
# cookie in the headers and a token in a hidden field. Both have to come back on
# the POST or the radio answers 403 - which is the whole of this file's point.
SESSION_COOKIE = "AIROS_SESSIONID=s3ss10n"
TOKEN = "t0ken"
CSRF_ID = "csrf-9"
LOGIN_PAGE = (
    "<html><body><form action='/login.cgi' method='post'>"
    f"<input type='hidden' name='AIROS_TOKEN' value='{TOKEN}'>"
    "<input type='hidden' name='uri' value='/index.cgi'>"
    "<input type='text' name='username'>"
    "<input type='password' name='password'>"
    "</form></body></html>"
)


class Behaviour:
    """How the fake radio answers. Set per test; read on every request.

    Four radios, because the whole question this file exists to answer is which
    of them the owner's device is:

    * `plain` - no login page at all (GET /login.cgi is 404) and a cold POST is
      accepted. The radio the code was originally written for.
    * `token` - a login page that sets a cookie and carries a hidden token, and
      a POST without either is refused with 403 whatever the password is.
    * `forbidden` - 403 to every login, always.
    * `unauthorized` - 401 to every login: a genuine authentication challenge.
    """

    mode = "plain"
    accept_login = True
    logged_in = False
    posts: list[dict] = []
    # A radio that says back what it was sent. Not hypothetical: this project has
    # already printed a percent-encoded password once, and a device that quotes
    # the form it rejected is exactly how the console would come to repeat one.
    echo = False
    # airOS 8 hands back an X-CSRF-ID on the login and refuses everything
    # afterwards that does not repeat it - a valid session, refused with 403.
    csrf = False


class FakeRadio(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: bytes = b"", extra: tuple = (), said: str = "") -> None:
        self.send_response(code, said or None)
        for name, value in extra:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        Behaviour.posts.append(
            {
                "path": self.path,
                "body": body,
                "cookie": self.headers.get("Cookie", ""),
                "referer": self.headers.get("Referer", ""),
                "origin": self.headers.get("Origin", ""),
                "content_type": self.headers.get("Content-Type", ""),
            }
        )
        if self.path.startswith("/api/"):
            if Behaviour.echo:
                # An airOS 8 endpoint that quotes the request it rejected. The
                # body it is quoting is JSON, so what comes back is the password
                # JSON-escaped - `a b\"c\\d`, `ס...` - which is a fourth
                # encoding on top of the two the form login produces, and the
                # one that left this console with the password intact.
                self._send(
                    200,
                    json.dumps({"error": f"rejected {body}"}).encode(),
                    (("Content-Type", "application/json"),),
                )
                return
            # This fake is a login.cgi radio. The airOS 8 endpoint is not here.
            self._send(404)
            return
        said = body if Behaviour.echo else ""
        if Behaviour.mode == "unauthorized":
            self._send(401, extra=(("WWW-Authenticate", 'Basic realm="airOS"'),), said=said)
            return
        if Behaviour.mode == "forbidden":
            self._send(403, said=said)
            return
        if Behaviour.mode == "broken":
            self._send(500, said=said)
            return
        if Behaviour.mode == "token" and (
            SESSION_COOKIE not in self.headers.get("Cookie", "")
            or f"AIROS_TOKEN={TOKEN}" not in body
        ):
            # The refusal this whole change is about: the password is correct and
            # the answer is still 403, because the session was never opened.
            self._send(403)
            return
        if not Behaviour.accept_login or f"password={PASSWORD}" not in body:
            self._send(403)
            return
        Behaviour.logged_in = True
        granted = [("Set-Cookie", "AIROS_SESSIONID=abc; path=/")]
        if Behaviour.csrf:
            granted.append(("X-CSRF-ID", CSRF_ID))
        self._send(200, b"ok", tuple(granted))

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/login.cgi"):
            if Behaviour.mode in ("token", "forbidden"):
                self._send(
                    200,
                    LOGIN_PAGE.encode(),
                    (
                        ("Set-Cookie", f"{SESSION_COOKIE}; path=/"),
                        ("Content-Type", "text/html"),
                    ),
                )
            else:
                self._send(404)
            return
        if not Behaviour.logged_in:
            # airOS answers an unauthenticated status.cgi with its login page,
            # not with an error - which is why the client must notice HTML.
            self._send(200, b"<html>login</html>", (("Content-Type", "text/html"),))
            return
        if Behaviour.csrf and self.headers.get("X-CSRF-ID") != CSRF_ID:
            # The session is valid and the answer is still 403.
            self._send(403)
            return
        self._send(
            200,
            json.dumps(STATUS).encode(),
            (("Content-Type", "application/json"),),
        )

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def radio() -> Iterator[str]:
    Behaviour.mode = "plain"
    Behaviour.logged_in = False
    Behaviour.accept_login = True
    Behaviour.echo = False
    Behaviour.csrf = False
    Behaviour.posts = []
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


# ------------------------------------------------------------- the black hole
#
# 192.0.2.0/24 is TEST-NET-1: reserved by RFC 5737, never routed, and therefore
# never answered and never refused. That is exactly the case these tests are
# about - a radio that is simply not there - and it is the honest way to
# reproduce it.
#
# What it used to cost was `TIMEOUT` twice over, https then http, with nothing at
# the other end: three tests at ~12 s each, 36 s of a 165 s suite spent proving
# that a packet sent nowhere goes nowhere. The behaviour under test is error
# CLASSIFICATION - an unreachable radio must not be accused of a bad password -
# and how long the wait was before it got classified is no part of that. So the
# socket, the failure and every line of the code path are unchanged and only the
# ceiling moves. Injected rather than shortened in the source: 6 s is the right
# ceiling for a console with an operator in front of it and the wrong one here.
NOWHERE = "192.0.2.99:8"


@pytest.fixture
def nowhere(monkeypatch) -> str:
    monkeypatch.setattr(airos, "TIMEOUT", 0.25)
    return NOWHERE


def test_an_unreachable_radio_is_a_sentence_not_a_crash(nowhere: str) -> None:
    with pytest.raises(RadioError) as caught:
        AirOsRadio(nowhere, USER, PASSWORD).status()
    assert "cannot reach" in str(caught.value)


# ------------------------------------------------------------------- the login
#
# The owner pointed this at the real radio and it answered 403. The code called
# that a rejected username or password and told the operator so, which sent them
# hunting a password problem that may not exist: airOS answers 403 to a login
# posted without the session cookie it sets on its own login page, to one that
# does not look like it came from that page, and after too many attempts.
#
# Nobody here can reach the radio, so none of the following is a claim about the
# owner's device. What it fixes is what the code is entitled to say.


def test_a_radio_that_wants_its_session_cookie_is_logged_into(radio: str) -> None:
    """The strongest candidate for the 403, and the one the fix is built around.

    This radio refuses a cold POST with 403 however right the password is. It
    sets AIROS_SESSIONID on a GET of its login page and puts a token in a hidden
    field, and it accepts the POST that carries both. The old code never made
    that GET, so it could never have logged in here.
    """
    Behaviour.mode = "token"
    device = AirOsRadio(radio, USER, PASSWORD)
    status = device.status()
    assert status.signal_dbm == -63
    assert device.login_method == "session"
    posted = Behaviour.posts[-1]
    assert SESSION_COOKIE in posted["cookie"], "the POST must carry the session cookie"
    assert f"AIROS_TOKEN={TOKEN}" in posted["body"], "and the token from the form"


def test_the_login_looks_like_it_came_from_the_radios_own_page(radio: str) -> None:
    """Some builds refuse a form POST with no Referer or Origin of their own."""
    Behaviour.mode = "token"
    AirOsRadio(radio, USER, PASSWORD).status()
    posted = Behaviour.posts[-1]
    assert posted["referer"].endswith("/login.cgi")
    assert radio in posted["origin"]


def test_a_radio_that_needs_no_token_still_works(radio: str) -> None:
    """The fallback has to be real, not decorative.

    This radio has no login page at all - GET /login.cgi is a 404 - and accepts
    the cold POST the console has always sent. The token-carrying flow cannot
    even start here, so if the fallback were not tried this radio would stop
    being readable the moment the fix landed.
    """
    Behaviour.mode = "plain"
    device = AirOsRadio(radio, USER, PASSWORD)
    assert device.status().signal_dbm == -63
    assert device.login_method == "cold"


def test_a_401_is_reported_as_a_rejected_password(radio: str) -> None:
    """401 is an authentication challenge and nothing else. Saying so is fair."""
    Behaviour.mode = "unauthorized"
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, "wrong").status()
    said = str(caught.value)
    assert "username or password" in said
    assert "401" in said
    assert "login.cgi" in said


def test_a_403_is_not_reported_as_a_rejected_password(radio: str) -> None:
    """The defect. A 403 is the radio refusing the request, and the code cannot
    tell from it whether the password is wrong. It must not say that it can."""
    Behaviour.mode = "forbidden"
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, PASSWORD).status()
    said = str(caught.value)
    assert "username or password" not in said, "403 does not say that"
    assert "403" in said, "the operator needs the code"
    assert "login.cgi" in said, "and the URL it came from"
    # And a hint at what it does mean, since the operator has no terminal.
    assert "cookie" in said.lower() or "session" in said.lower()


def test_a_403_says_both_login_flows_were_tried(radio: str) -> None:
    """Otherwise the first thing anyone asks is whether it tried the other one.

    This radio serves a proper login page and still refuses every POST, so both
    flows get as far as posting and both are refused. The sentence may say they
    were tried because they were.
    """
    Behaviour.mode = "forbidden"
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, PASSWORD).status()
    assert "probe_radio" in str(caught.value), "and where the answer comes from"
    to_login = [post for post in Behaviour.posts if post["path"] == "/login.cgi"]
    assert [post for post in to_login if post["referer"]], "the session flow posted"
    assert [post for post in to_login if not post["referer"]], "and so did the cold one"


def test_a_session_that_needs_its_token_repeating_keeps_reading(radio: str) -> None:
    """airOS 8 hands back an X-CSRF-ID and refuses everything that omits it.

    A login that succeeded and a status.cgi that comes back 403 is the same
    defect one step later, and it would read as a dead link rather than as a
    header that was dropped.
    """
    Behaviour.csrf = True
    device = AirOsRadio(radio, USER, PASSWORD)
    assert device.status().signal_dbm == -63


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_the_password_is_never_in_the_message_in_any_encoding(
    radio: str, password: str
) -> None:
    """It has bitten this project twice. A password masked in one form and
    printed in another is a password that has been printed.

    This radio quotes what it was sent, which is how the console comes to repeat
    a password it never printed itself. It is quoted in two different encodings
    depending on which login it refused: the form logins are posted
    percent-encoded, so what comes back is `p%40ss+word%2F1`; the airOS 8 API
    login is posted as JSON, so what comes back is `a b\\"c\\\\d` and
    `\\u05e1\\u05d9...`, and neither of those is the string anybody searches for.

    The test asserts the radio's words DID reach the message before asserting
    that the password did not, so it cannot pass by the message being empty.
    """
    Behaviour.mode = "forbidden"
    Behaviour.echo = True
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, password).status()
    said = str(caught.value)
    assert "rejected" in said, "the radio's own words still have to reach the operator"
    assert leaks(said, password) == [], f"the password reached the operator: {said!r}"


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_redaction_covers_every_encoding_the_password_leaves_in(password: str) -> None:
    """`redact` on its own, against every form this program writes a password in.

    Four encodings and not two: the console posts a form login percent-encoded
    (twice over, since `quote` and `quote_plus` disagree about spaces) and an
    API login as JSON. A password containing `"`, `\\` or any non-ASCII
    character left `_login_api` fully intact until this was fixed, because
    `json.dumps` had already rewritten it into something the search missed.
    """
    for name, form in encodings(password).items():
        hidden = redact(f"the radio rejected {form} and said so", password)
        assert leaks(hidden, password) == [], f"{name} survived redaction: {hidden!r}"
        assert REDACTED in hidden, f"{name} was not recognised as the password at all"


def test_an_unexplained_code_is_reported_as_itself(radio: str) -> None:
    """Anything that is neither 401 nor 403 gets the number and no story."""
    Behaviour.mode = "broken"
    with pytest.raises(RadioError) as caught:
        AirOsRadio(radio, USER, PASSWORD).status()
    said = str(caught.value)
    assert "500" in said
    assert "username or password" not in said


def test_a_radio_that_never_answered_is_not_accused_of_anything(nowhere: str) -> None:
    with pytest.raises(RadioError) as caught:
        AirOsRadio(nowhere, USER, PASSWORD).status()
    assert "username or password" not in str(caught.value)


# ------------------------------------------- the firmware that was actually met
#
# Everything above this line was modelled on general knowledge of airOS. This is
# not: it is `lighttpd/1.4.54` on the owner's own radio, captured by
# spike/probe_radio.py on the first run that ever reached one.
#
#     [session] GET  /login.cgi   -> 200, Set-Cookie: AIROS_28704EA42F45=c788...
#     [session] POST /login.cgi   -> 200 (OK), body: Invalid credentials.
#     [session] GET  /status.cgi  -> 403
#     [api]     POST /api/auth    -> 403, body: {"error":"Invalid credentials."}
#
# The session flow was right - the cookie was set, the POST carried it, and the
# cold POST without it was refused with "Missing session id". The password was
# simply wrong, and the radio said so in a body behind an HTTP 200.


REAL_COOKIE = "AIROS_28704EA42F45"
REFUSED_BODY = "Invalid credentials."
# What airOS sends when the login DOES take: not a page, a redirect written in
# HTML. The thing that distinguishes it from the refusal above is not the words.
ACCEPTED_BODY = '<html><head><meta http-equiv="refresh" content="0; url=/"></head></html>'


class RealFirmware(BaseHTTPRequestHandler):
    """The owner's radio, as far as it has been observed.

    Answers a wrong password with **HTTP 200** and says so only in the body,
    sets no new session cookie when it does, and refuses status.cgi with 403
    afterwards - which is where the console used to notice, by which point it had
    already decided the login had succeeded.
    """

    sessions: set[str] = set()

    def _send(self, code: int, payload: bytes = b"", extra: tuple = ()) -> None:
        self.send_response(code)
        for name, value in extra:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _cookie(self) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == REAL_COOKIE:
                return value
        return ""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/login.cgi"):
            self._send(
                200,
                b"<html><body><form action='/login.cgi' method='post'></form></body></html>",
                (
                    ("Set-Cookie", f"{REAL_COOKIE}=c788e636; Path=/; HttpOnly"),
                    ("Content-Type", "text/html"),
                    ("Server", "lighttpd/1.4.54"),
                ),
            )
            return
        if self._cookie() not in RealFirmware.sessions:
            self._send(403, b"", (("Server", "lighttpd/1.4.54"),))
            return
        self._send(200, json.dumps(STATUS).encode(), (("Content-Type", "application/json"),))

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        right = PASSWORD in body
        if self.path.startswith("/api/"):
            # Same answer, in JSON, behind a 403.
            if right:
                self._send(200, b'{"ok":true}', (("Set-Cookie", f"{REAL_COOKIE}=api1; Path=/"),))
                RealFirmware.sessions.add("api1")
            else:
                self._send(
                    403,
                    json.dumps({"error": REFUSED_BODY}).encode(),
                    (("Content-Type", "application/json"),),
                )
            return
        if not self._cookie():
            # The cold POST, with no session: refused in words, still HTTP 200.
            self._send(200, b"Missing session id")
            return
        if not right:
            # The defect, exactly as captured: 200, no new cookie, and the reason
            # in the body.
            self._send(200, REFUSED_BODY.encode())
            return
        RealFirmware.sessions.add("granted")
        self._send(
            200,
            ACCEPTED_BODY.encode(),
            (("Set-Cookie", f"{REAL_COOKIE}=granted; Path=/; HttpOnly"),),
        )

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def real_radio() -> Iterator[str]:
    RealFirmware.sessions = set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), RealFirmware)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{server.server_address[0]}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_login_refused_behind_an_http_200_is_not_a_login(real_radio: str) -> None:
    """The defect this fixture exists for.

    The radio said `Invalid credentials.` and the console, judging on the status
    code alone, believed the login had worked - and then reported the 403 that
    followed as something that "need not mean the password is wrong", when the
    radio had already said in those words that it was.
    """
    with pytest.raises(RadioError) as caught:
        AirOsRadio(real_radio, USER, "wrong").status()
    said = str(caught.value)
    assert REFUSED_BODY in said, "the radio's own words have to reach the operator"
    assert "need not mean the password is wrong" not in said


def test_the_right_password_gets_in_on_that_same_firmware(real_radio: str) -> None:
    """The check may not cost a login that works. This firmware marks a real one
    with a fresh cookie and a redirect page rather than with words."""
    device = AirOsRadio(real_radio, USER, PASSWORD)
    assert device.status().signal_dbm == -63
    assert device.login_method == "session"


def test_a_refusal_in_json_behind_a_403_is_quoted_too(real_radio: str) -> None:
    """`/api/auth` gives the same answer in JSON. Both paths have to be read: a
    body that names the failure is the most useful sentence the radio has."""
    from vmd.radio.airos import refusal_words

    assert refusal_words(json.dumps({"error": REFUSED_BODY})) == REFUSED_BODY
    assert refusal_words(REFUSED_BODY) == REFUSED_BODY
    # And a page is not a sentence: a whole login form re-served is not a quote.
    assert refusal_words(LOGIN_PAGE * 20) == ""
    assert refusal_words(ACCEPTED_BODY) == ""


def test_nothing_is_claimed_about_a_firmware_that_says_nothing() -> None:
    """One build of many. A radio that answers 200 with an empty body and no
    cookie is not evidence of a refusal, and status.cgi is still the thing that
    finds out - the old behaviour, deliberately kept."""
    from vmd.radio.airos import check_login

    check_login("", {})
    check_login("ok", {})


def test_the_evidence_is_more_than_one_english_string() -> None:
    """This firmware is one build of many, so the words are not the test. A
    login that took sets a fresh session cookie, or redirects; one that did not
    does neither and says why."""
    from vmd.radio.airos import login_accepted

    assert login_accepted("Invalid credentials.", {"Set-Cookie": "AIROS_x=new"}) is True
    assert login_accepted(ACCEPTED_BODY, {}) is True
    assert login_accepted("", {"Location": "/"}) is True
    assert login_accepted(REFUSED_BODY, {}) is False


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_the_quoted_words_are_bounded_and_redacted(real_radio: str, password: str) -> None:
    """A sentence built out of a device's own text is a device's own text. It is
    quoted, cut, and passed through the redaction like everything else - a login
    answer can echo what it was sent."""
    with pytest.raises(RadioError) as caught:
        AirOsRadio(real_radio, USER, password).status()
    said = str(caught.value)
    assert leaks(said, password) == []
    assert len(said) < 400


# ------------------------------------------------------- reading the login page


def test_the_hidden_fields_of_a_login_page_are_found() -> None:
    """A CSRF token lives in a hidden input, and that is where the fix looks."""
    found = hidden_fields(LOGIN_PAGE)
    assert found["AIROS_TOKEN"] == TOKEN
    assert found["uri"] == "/index.cgi"
    assert "password" not in found, "only hidden fields, not the typed ones"


def test_a_page_that_is_not_a_login_page_yields_nothing_rather_than_raising() -> None:
    assert hidden_fields("") == {}
    assert hidden_fields("<html><p>not a form at all</p>") == {}
    assert hidden_fields("<input type=hidden name=a value=b><input type=hidden>") == {"a": "b"}


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


def test_the_service_never_raises(nowhere: str) -> None:
    service = service_for(nowhere)
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
        Behaviour.logged_in = False
        Behaviour.accept_login = False
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


# --------------------------------------- reading it often enough to look alive
#
# "I want the numbers from the signal to be automatically updated realtime - I
# want to see that it's actually capturing them." The cadence is half of that;
# the other half is in the panel, which needs something that changes once per
# reading that actually landed.


class SteadyRadio:
    """A radio that answers at once, and counts how often it was asked."""

    def __init__(self) -> None:
        self.calls = 0

    def status(self):
        self.calls += 1
        return _Steady()


class _Steady:
    @staticmethod
    def as_dict() -> dict:
        return {"connected": True, "reason": "", "signal_dbm": -63.0}


def test_the_radio_is_read_again_inside_one_beat_of_the_console() -> None:
    """The panel is redrawn on the window's heartbeat and nowhere else, so an
    interval longer than that beat means some beats redraw the previous reading
    and the figures visibly stall. Pinned against the heartbeat itself rather
    than against a number, because the number that matters is the relationship."""
    from vmd.desktop.window import HEARTBEAT_MS
    from vmd.radio.service import CACHE_SECONDS

    assert CACHE_SECONDS < HEARTBEAT_MS / 1000.0, (
        "the radio is asked less often than the console draws it"
    )


def test_each_reading_that_landed_is_counted() -> None:
    """What the panel's mark advances on. A count of readings, not of redraws:
    the console redraws whether or not the radio answered."""
    radio = SteadyRadio()
    service = _service_around(radio)
    try:
        assert until(lambda: service.status().get("readings") == 1, PATIENCE)
        assert until(lambda: service.status().get("readings", 0) >= 2, PATIENCE)
    finally:
        service.close()


def test_a_read_that_failed_is_not_counted_as_a_reading() -> None:
    """He is being shown that figures are arriving. A login timeout is not a
    figure arriving, and a mark that advanced on one would be a lie told twice
    a minute for months."""
    wedged = WedgedRadio()
    service = _service_around(wedged)
    try:
        service.status()
        assert wedged.entered.wait(PATIENCE)
        wedged.released.set()
        # The radio's own words, not merely "not connected": a reading nobody
        # has taken yet also says not connected, and waiting for that would let
        # this pass without a failed read ever having landed.
        assert until(lambda: "cannot reach" in service.status().get("reason", ""), PATIENCE)
        assert "readings" not in service.status(), service.status()
    finally:
        wedged.released.set()
        service.close()


def test_a_radio_that_will_not_answer_is_still_asked_only_one_at_a_time() -> None:
    """The interval is a floor, not a rate. A radio that has to log in again
    takes seconds to answer, and nothing may start a second read while the
    first is still out - which is what would turn a shorter interval into a
    radio being hammered."""
    import time as _time

    wedged = WedgedRadio()
    service = _service_around(wedged)
    try:
        for _ in range(30):
            service.status()
            _time.sleep(0.01)
        assert wedged.entered.wait(PATIENCE)
        assert wedged.calls == 1, f"{wedged.calls} reads were started at once"
    finally:
        wedged.released.set()
        service.close()
