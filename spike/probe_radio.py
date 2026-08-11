"""Point this at the real Ubiquiti radio, once, and the guesswork is over.

`vmd/radio/airos.py` reads airOS's `status.cgi`. This is what points it at a real
radio and prints everything that came back, so that a link panel showing dashes
becomes a change in `airos.py` rather than three rounds of guessing.

It has now done that twice, and it earned its keep on both runs.

The first: the radio answered the login with HTTP 200 and the words `Invalid
credentials.` in the body, which the console had been reading as a successful
login - so the failure surfaced later as an unexplained 403 from `status.cgi`.
The session flow was proved right at the same time.

The second, once the password was right: it printed the radio's whole answer and
the signal came back UNKNOWN. `wireless.signal` does not exist on a station -
the reading is in `wireless.sta[0]` - there is no `ccq` on this firmware, and
`polling.use`, the airtime, was not being read at all on a link that was 88%
full. All of that is in `airos.py` now, and this file's field report looks where
the parser looks: a probe that goes on naming the old keys would report a link
as unreadable that the console is reading perfectly well, which is this same
failure pointed the other way.

Run it from anywhere, with whatever python is to hand:

    python probe_radio.py 192.168.1.20 --user ubnt

or, from the VMD folder:

    uv run --offline --frozen --no-sync python spike\\probe_radio.py 192.168.1.20 --user ubnt

Everything it needs is the standard library and the console's own parser, which
it finds beside itself. If it cannot run at all it says which command to type
instead, because the person running it is standing at a laptop with the link
down.

It asks for the password rather than taking it on the command line, because
PowerShell writes every command that is typed into `ConsoleHost_history.txt` in
plain text and keeps it. Then it prints, in order:

  1. **the login exchange** - every request it made, in which flow, the status
     that came back, the headers that settle this (`Set-Cookie` says whether a
     session was ever opened, `Location` whether the login redirected,
     `WWW-Authenticate` whether it wanted HTTP auth instead, `Server` which build
     this is), the cookies held, and the hidden fields of the login page, since
     that is where a CSRF token would be;
  2. the raw JSON the radio sent, pretty-printed;
  3. what `parse_status` made of it, field by field - including, for anything it
     could not find, the exact JSON keys it looked for;
  4. the names this radio actually uses, so a mismatch is visible at a glance.

Everything is redacted against the password in both the typed and the
percent-encoded form, and every body is truncated: this output is meant to be
pasted into an email.

That turns "the antenna returns 403" and "the panel shows dashes" into a change
in `airos.py` instead of three rounds of guessing. Read-only throughout: nothing
here changes a radio setting, because a console that can reconfigure the link it
depends on is a console that can cut itself off.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import ssl
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------- the bootstrap
#
# The operator runs this from wherever they are standing, with whatever `python`
# is on their PATH - from inside `spike\`, by hand, on the day the link is down.
# That is the only way it will ever be run, so it is the way it has to work.
#
# It can: everything this file and `vmd/radio/airos.py` import is the standard
# library, so no environment is needed, and the line below puts the project root
# on the path so that `import vmd.radio` finds the console's own parser rather
# than failing. It must be the console's parser and not a copy of it: a probe
# that reports on a copy reports on nothing.
#
# When that cannot work - too old an interpreter, or this file moved away from
# the project - it says the whole command to type instead, rather than raising
# the import error at somebody who has no terminal skills and a camera to get up.
ROOT = Path(__file__).resolve().parent.parent

# The example every message here uses, and the one in docs/FIRST-MORNING.md.
# `--offline --frozen --no-sync` because the field laptop has no network and its
# packages are already installed; anything that tried to resolve would hang.
HOW_TO_RUN = (
    "uv run --offline --frozen --no-sync python "
    "spike\\probe_radio.py 192.168.1.20 --user ubnt"
)

# What runs this. Nothing here needs a new interpreter - it is all standard
# library and the annotations are postponed - so the floor is low on purpose:
# refusing a Python that would have worked would be sending someone to install
# one on a laptop with no network, in the middle of a fault.
MINIMUM_PYTHON = (3, 9)


def cannot_run(problem: str) -> None:
    """Say what is wrong and what to type instead, then stop. No traceback.

    Whoever is reading this is standing at a laptop with the link down and no
    terminal skills. An ImportError is not something they can act on; a line
    they can copy is.
    """
    print()
    print(f"  {problem}")
    print()
    print("  Open PowerShell in the VMD folder - the one with install.bat in it -")
    print("  and run this instead, with your radio's address and username:")
    print()
    print(f"      {HOW_TO_RUN}")
    print()
    raise SystemExit(2)


if sys.version_info < MINIMUM_PYTHON:
    running = ".".join(str(part) for part in sys.version_info[:3])
    wanted = ".".join(str(part) for part in MINIMUM_PYTHON)
    cannot_run(
        f"This is Python {running}, and this tool needs {wanted} or newer. "
        f"The one installed with VMD is new enough."
    )

sys.path.insert(0, str(ROOT))

try:
    from vmd.radio.airos import (  # noqa: E402
        API_LOGIN_PATH,
        LOGIN_FLOWS,
        LOGIN_PATH,
        REDACTED,
        TELLING_HEADERS,
        LoginRefused,
        check_login,
        hidden_fields,
        login_fields,
        parse_status,
        redact,
        refusal_words,
    )
except ImportError as exc:  # pragma: no cover - the copy-it-somewhere-else case
    cannot_run(
        f"This tool reads the console's own radio code, and it is not in {ROOT} "
        f"({exc}). It has to sit in the spike folder of the VMD folder to work."
    )

TIMEOUT = 6.0

# The flows the console tries, in the order it tries them. Imported rather than
# restated: two implementations that drift are worse than one that is wrong,
# because the probe would then answer a question about a login nobody sends.
FLOWS = LOGIN_FLOWS

# How much of a body is worth printing. A login page is tens of kilobytes of
# HTML and this output has to fit in an email.
BODY_CHARACTERS = 400
BODY_LINES = 8


class ProbeError(Exception):
    """The radio could not be read, with a sentence saying why.

    Carries the login exchange as well, because the failing case is the case:
    a tool that prints the exchange only when the login worked prints it exactly
    when nobody needs it.
    """

    def __init__(self, message: str, exchange: list | None = None) -> None:
        super().__init__(message)
        self.exchange = list(exchange or [])


# The keys `parse_status` looks for, in the order it looks for them. Printed
# beside anything that came back unknown, because the name it wanted is the
# whole of the fix.
#
# Half of these changed when the parser met a real radio. A station keeps its
# reading in `wireless.sta[0]` and there is no `wireless.signal` at all, so a
# report that went on naming only the old keys would report a link as unreadable
# that the console is reading perfectly well - which is the same failure this
# tool exists to prevent, pointed the other way.
LOOKED_FOR: dict[str, tuple[str, ...]] = {
    "connected": ("wireless.essid", "wireless.count", "wireless.sta (empty means down)"),
    "signal_dbm": (
        "wireless.signal",
        "wireless.sta.0.signal",
        "wireless.rssi (added to the noise floor)",
        "wireless.sta.0.rssi (added to the noise floor)",
    ),
    "remote_signal_dbm": ("wireless.sta.0.remote.signal",),
    "noise_dbm": ("wireless.noisef", "wireless.noise", "wireless.sta.0.noisefloor"),
    "quality_percent": (
        "wireless.sta.0.dl_avg_linkscore",
        "wireless.sta.0.ul_avg_linkscore",
        "wireless.ccq (which is a 0-1000 scale, and these are not)",
    ),
    "ccq": ("wireless.ccq",),
    "airtime_percent": ("wireless.polling.use",),
    "rx_airtime_percent": ("wireless.polling.rx_use",),
    "tx_airtime_percent": ("wireless.polling.tx_use",),
    "tx_mbps": ("wireless.throughput.tx",),
    "rx_mbps": ("wireless.throughput.rx",),
    "tx_capacity_mbps": ("wireless.polling.dl_capacity", "wireless.txrate"),
    "rx_capacity_mbps": ("wireless.polling.ul_capacity", "wireless.rxrate"),
    "uptime_s": ("host.uptime",),
    "device": ("host.hostname", "host.devmodel"),
}

UNITS: dict[str, str] = {
    "signal_dbm": "dBm",
    "remote_signal_dbm": "dBm",
    "noise_dbm": "dBm",
    "quality_percent": "%",
    "airtime_percent": "%",
    "rx_airtime_percent": "%",
    "tx_airtime_percent": "%",
    "tx_mbps": "Mb/s",
    "rx_mbps": "Mb/s",
    "tx_capacity_mbps": "Mb/s",
    "rx_capacity_mbps": "Mb/s",
    "uptime_s": "s",
}


def is_station(payload: dict) -> bool:
    """Whether this radio is the station end of the link, as it says itself."""
    wireless = payload.get("wireless") or {}
    return str(wireless.get("mode") or "").lower().startswith("sta")


def looked_for(payload: dict) -> dict[str, tuple[str, ...]]:
    """The keys the parser reads for THIS answer.

    Only the two capacities depend on the answer, and they have to: `dl` and
    `ul` are the link's directions rather than the radio's, so on a station the
    downlink is what arrives. A report that named `dl_capacity` as the source of
    the outgoing figure on a station would be naming the wrong key with perfect
    confidence, which is exactly the sort of thing this tool exists to stop.
    """
    paths = dict(LOOKED_FOR)
    if is_station(payload):
        paths["rx_capacity_mbps"] = ("wireless.polling.dl_capacity", "wireless.rxrate")
        paths["tx_capacity_mbps"] = ("wireless.polling.ul_capacity", "wireless.txrate")
    return paths


@dataclass
class Exchange:
    """One request of the login, and everything about it worth reading.

    Deliberately a record rather than a printed line: the redaction happens once,
    at the point the report is written, so nothing can be added here later that
    goes to the screen without passing through it.
    """

    flow: str
    method: str
    url: str
    status: int | None = None
    said: str = ""
    headers: dict = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)
    hidden: dict = field(default_factory=dict)
    body: str = ""


@dataclass
class Answer:
    """One reading of status.cgi, and how it was reached."""

    scheme: str
    body: str
    notes: list[str] = field(default_factory=list)
    exchange: list[Exchange] = field(default_factory=list)
    flow: str = ""


# ------------------------------------------------------------------ redaction
#
# `redact` and `REDACTED` are the console's own, imported above rather than
# restated here. The rule they enforce - hide the password in the typed form and
# in both percent-encoded forms, because the login is posted as an encoded form -
# is one this project has already broken once, and two copies of it are two
# chances to break it again.


# ------------------------------------------------------------------ the fetch


def reason_for(exc: BaseException) -> str:
    """One sentence for every way a radio refuses to be read.

    401 and 403 are not the same answer and must not read as the same sentence.
    401 is an authentication challenge. 403 is the radio refusing the request,
    which may be the password and may equally be a session it never opened - and
    saying otherwise is what sent the owner hunting a password that was fine.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return "the radio refused the username or password (HTTP 401)"
        if exc.code == 403:
            return (
                "the radio answered HTTP 403: it refused the request, which need "
                "not mean the password is wrong - see the login exchange above"
            )
        return f"the radio answered HTTP {exc.code}"
    if isinstance(exc, ssl.SSLError):
        return (
            "the secure connection could not be made: its certificate is "
            f"self-signed, which is expected and not checked, so this is "
            f"something else ({exc})"
        )
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, ssl.SSLError):
            return reason_for(exc.reason)
        return f"could not connect: {exc.reason}"
    if isinstance(exc, TimeoutError):
        return f"no answer within {TIMEOUT:.0f} s: {exc}"
    return f"could not connect: {exc}"


def _opener(jar: http.cookiejar.CookieJar) -> urllib.request.OpenerDirector:
    # The same three handlers the console uses, for the same three reasons:
    # airOS ships a self-signed certificate for an address that is not in it, the
    # login is a cookie, and no proxy may ever sit between this machine and a
    # radio that is a cable away.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(jar),
    )


def _kept(headers) -> dict[str, str]:
    """The response headers that settle this, and only those."""
    kept: dict[str, str] = {}
    if headers is None:
        return kept
    for name in TELLING_HEADERS:
        values = headers.get_all(name) if hasattr(headers, "get_all") else None
        if values is None:
            one = headers.get(name)
            values = [one] if one else []
        if values:
            kept[name] = " | ".join(str(value) for value in values)
    return kept


def _held(jar: http.cookiejar.CookieJar) -> list[str]:
    """The cookies in hand, named and measured but never quoted.

    Whether a session cookie exists is the diagnosis. Its value is a live
    credential for the radio, and nobody needs it read out over a phone.
    """
    return sorted(f"{cookie.name} ({len(cookie.value or '')} characters)" for cookie in jar)


def _record(
    exchange: list[Exchange],
    flow: str,
    method: str,
    url: str,
    jar: http.cookiejar.CookieJar,
    response=None,
    error: BaseException | None = None,
    body: str = "",
    hidden: dict | None = None,
) -> None:
    """One line of the story, whether it went well or not."""
    if error is None:
        status = getattr(response, "status", None)
        said = str(getattr(response, "reason", "") or "")
        headers = getattr(response, "headers", None)
    elif isinstance(error, urllib.error.HTTPError):
        # An HTTPError is a response: it has a code, a reason and headers, and
        # those are exactly the three things this exists to show.
        status = error.code
        said = str(error.reason or "")
        headers = error.headers
    else:
        status = None
        said = reason_for(error)
        headers = None
    exchange.append(
        Exchange(
            flow=flow,
            method=method,
            url=url,
            status=status,
            said=said,
            headers=_kept(headers),
            cookies=_held(jar),
            hidden=dict(hidden or {}),
            body=body,
        )
    )


def _login(
    flow: str,
    scheme: str,
    host: str,
    username: str,
    password: str,
    jar: http.cookiejar.CookieJar,
    opener: urllib.request.OpenerDirector,
    exchange: list[Exchange],
    timeout: float,
) -> dict[str, str]:
    """One login flow, recorded request by request. Raises what the radio raised."""
    if flow == "api":
        url = f"{scheme}://{host}{API_LOGIN_PATH}"
        data = json.dumps({"username": username, "password": password}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        return _post(flow, url, data, headers, jar, opener, exchange, timeout)

    url = f"{scheme}://{host}{LOGIN_PATH}"
    hidden: dict[str, str] = {}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    if flow == "session":
        # The candidate this whole exercise is about: the login page sets a
        # cookie and carries a token, and a POST without them may be the 403.
        request = urllib.request.Request(url, headers={"Accept": "text/html"})
        try:
            with opener.open(request, timeout=timeout) as response:
                page = response.read().decode("utf-8", "replace")
                hidden = hidden_fields(page)
                _record(exchange, flow, "GET", url, jar, response=response, body=page, hidden=hidden)
                opened = _kept(response.headers)
        except (urllib.error.URLError, OSError) as exc:
            _record(exchange, flow, "GET", url, jar, error=exc)
            raise
        headers["Referer"] = url
        headers["Origin"] = f"{scheme}://{host}"
        if opened.get("X-CSRF-ID"):
            headers["X-CSRF-ID"] = opened["X-CSRF-ID"]

    data = urllib.parse.urlencode(login_fields(username, password, hidden)).encode()
    return _post(flow, url, data, headers, jar, opener, exchange, timeout)


def _post(
    flow: str,
    url: str,
    data: bytes,
    headers: dict,
    jar: http.cookiejar.CookieJar,
    opener: urllib.request.OpenerDirector,
    exchange: list[Exchange],
    timeout: float,
) -> dict[str, str]:
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            _record(exchange, flow, "POST", url, jar, response=response, body=body)
            after = _kept(response.headers)
    except (urllib.error.URLError, OSError) as exc:
        body = ""
        if isinstance(exc, urllib.error.HTTPError):
            body = exc.read().decode("utf-8", "replace")
        _record(exchange, flow, "POST", url, jar, error=exc, body=body)
        # A refusal that names itself is worth more than the code it arrived in.
        # /api/auth answers `{"error":"Invalid credentials."}` behind a 403, and
        # "403, which need not mean the password is wrong" is the wrong summary
        # of a radio that just said the password is wrong.
        said = refusal_words(body)
        if said:
            raise LoginRefused(said) from exc
        raise
    # The console's own check, imported rather than repeated: this radio answers
    # a wrong password with HTTP 200 and says so only in the body, and a probe
    # that called that a login would report the 403 from status.cgi as the
    # mystery it is not. `_kept` joins repeated headers with " | " where the
    # console's `_telling` uses ", ", which the check does not care about - it
    # asks whether there is a Set-Cookie at all.
    check_login(body, after)
    csrf = after.get("X-CSRF-ID") or headers.get("X-CSRF-ID")
    return {"X-CSRF-ID": csrf} if csrf else {}


def fetch_status(host: str, username: str, password: str, timeout: float = TIMEOUT) -> Answer:
    """Log in and read status.cgi, the way the console does and saying so.

    Every scheme and every flow the console would try, in the order it tries
    them, with what came back from each one kept. Nothing here decides what the
    403 means: it collects what would let somebody decide.
    """
    host = host.strip()
    if not host:
        raise ProbeError("no radio address was given")

    notes: list[str] = []
    exchange: list[Exchange] = []
    for scheme in ("https", "http"):
        for flow in FLOWS:
            jar = http.cookiejar.CookieJar()
            opener = _opener(jar)
            try:
                carried = _login(
                    flow, scheme, host, username, password, jar, opener, exchange, timeout
                )
            except LoginRefused as exc:
                # The radio answered and refused in words. Kept as a note and the
                # next flow is still tried - a build that wants a session refuses
                # the cold POST in words too - but this is the sentence that ends
                # the investigation, so it is quoted exactly.
                notes.append(
                    f'{scheme} {flow} login - the radio refused it and said: "{exc.said}"'
                )
                continue
            except (urllib.error.URLError, OSError) as exc:
                notes.append(f"{scheme} {flow} login - {reason_for(exc)}")
                if not isinstance(exc, urllib.error.HTTPError):
                    # Nothing answered at all. The other flows would only spend
                    # another timeout each finding that out.
                    break
                continue

            url = f"{scheme}://{host}/status.cgi"
            try:
                request = urllib.request.Request(url, headers=dict(carried))
                with opener.open(request, timeout=timeout) as response:
                    body = response.read().decode("utf-8", "replace")
                    _record(exchange, flow, "GET", url, jar, response=response, body=body)
            except (urllib.error.URLError, OSError) as exc:
                _record(exchange, flow, "GET", url, jar, error=exc)
                notes.append(f"{url} - {reason_for(exc)}")
                continue
            return Answer(scheme=scheme, body=body, notes=notes, exchange=exchange, flow=flow)

    raise ProbeError(
        f"{host} could not be logged into. What was tried:\n"
        + "\n".join(f"      {note}" for note in notes),
        exchange=exchange,
    )


# ------------------------------------------------------------- the login story


def excerpt(body: str, password: str) -> list[str]:
    """A body, cut down to something that fits in an email, and redacted.

    Cut before it is wrapped, so a login page that is one 30 kB line of HTML
    costs the same few lines as one that is nicely formatted.
    """
    text = redact(body or "", password)
    lines = text.splitlines()
    cut = lines[:BODY_LINES]
    shown = "\n".join(cut)
    if len(shown) > BODY_CHARACTERS:
        cut = shown[:BODY_CHARACTERS].splitlines()
        cut.append("... (cut)")
    elif len(lines) > BODY_LINES:
        cut.append("... (cut)")
    wrapped: list[str] = []
    for line in cut:
        if line.strip():
            wrapped += textwrap.wrap(line, width=64) or [line.strip()]
    return wrapped


def login_lines(exchange: list[Exchange], password: str, flow: str = "") -> list[str]:
    """The login exchange, request by request.

    This is the block the owner sends back, and it has to settle the cause in
    one round rather than three. Every request; the status of each; the four
    headers that decide it; the cookies held at that point; and the hidden
    fields of the login page, because a CSRF token nobody sent back is the
    strongest candidate for a 403 that has nothing to do with the password.
    """
    lines = ["-" * 72, "the login exchange (what was sent, and what came back)", "-" * 72]
    if not exchange:
        lines.append("  nothing was sent: there was no address to send it to.")
        return lines
    for step in exchange:
        head = f"  [{step.flow}] {step.method} {redact(step.url, password)}"
        lines.append(head)
        answered = f"HTTP {step.status}" if step.status is not None else "no answer"
        said = redact(step.said, password)
        lines.append(f"      -> {answered}" + (f" ({said})" if said else ""))
        for name, value in step.headers.items():
            lines.append(f"      {name}: {redact(value, password)}")
        if step.cookies:
            lines.append("      cookies held: " + ", ".join(step.cookies))
        else:
            lines.append("      cookies held: none")
        if step.hidden:
            lines.append("      hidden fields on the page (a CSRF token would be here):")
            for name, value in step.hidden.items():
                lines.append(f"        {name} = {redact(value, password)}")
        for line in excerpt(step.body, password):
            lines.append(f"      | {line}")
    lines.append("")
    if flow:
        # What got in, and nothing about what would not have. The flows are
        # tried in order and the first one that works stops the rest, so a
        # session login says the session login works - not that the cold POST
        # would have failed. Which of those is true is above, in the statuses.
        lines.append(f"  The {flow} login is the one this radio accepted.")
        if flow == "session":
            lines.append("  It opened a session on its own login page and posted from there.")
            lines.append("  The cold POST was not reached, because this got in first.")
        elif flow == "cold":
            lines.append("  A plain POST, with no session opened first. Whatever the")
            lines.append("  session flow above ran into, it was not this radio's password.")
        else:
            lines.append("  Through the airOS 8 API rather than the login form. The console")
            lines.append("  will need the X-CSRF-ID it hands back on every later request.")
    else:
        lines.append("  No flow logged in. Whichever status is above is the whole answer:")
        lines.append("  401 is a rejected password; 403 is the radio refusing the request,")
        lines.append("  and the Set-Cookie and hidden-field lines above say whether a")
        lines.append("  session was ever opened to refuse.")
    return lines


# ---------------------------------------------------------------- the reading


def at_path(payload: dict, path: str):
    """The value at a dotted path, or `None` and False if it is not there.

    A step that is a number indexes a list, because the reading of a
    point-to-point link lives in `wireless.sta.0` - the one entry for the radio
    at the other end - and a walk that only opened dictionaries walked straight
    past everything that turned out to matter.
    """
    # Only the part before a bracket or a space: the table above annotates some
    # paths with how they are used, and that annotation is for the reader.
    node = payload
    for step in path.split(" ")[0].split("."):
        if step.isdigit():
            index = int(step)
            if not isinstance(node, list) or index >= len(node):
                return None, False
            node = node[index]
            continue
        if not isinstance(node, dict) or step not in node:
            return None, False
        node = node[step]
    return node, True


def field_lines(payload: dict) -> list[str]:
    """What `parse_status` made of this JSON, field by field.

    Anything unknown carries the exact key names that were looked for, which is
    the difference between a five-minute edit and an investigation.
    """
    parsed = parse_status(payload).as_dict()
    lines: list[str] = []
    for name, paths in looked_for(payload).items():
        value = parsed.get(name)
        found = [path for path in paths if at_path(payload, path)[1]]
        if name == "connected":
            shown = "yes" if value else "no"
        elif value in (None, ""):
            shown = ""
        else:
            unit = UNITS.get(name, "")
            shown = f"{value} {unit}".strip()
        if shown:
            source = f"from {found[0]}" if found else "worked out from the fields above"
            lines.append(f"  {name:<20} {shown:<22} {source}")
        else:
            lines.append(
                f"  {name:<20} {'UNKNOWN':<22} looked for "
                + ", ".join(paths)
                + " - not in this radio's JSON"
            )
    return lines


def key_lines(payload: dict, prefix: str = "", depth: int = 4) -> list[str]:
    """The names this radio actually uses.

    Lists are opened as well as dictionaries, and that is not a tidy-up: on the
    radio this was run against, everything the console was missing lived in
    `wireless.sta[0]` and one level further down again in its `remote`. The old
    walk opened dictionaries only, so it printed `wireless.*` with `sta` in the
    list and then stopped - naming the container and none of its contents, which
    is the one thing this block exists to do.
    """
    lines: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}{key}"
        lines += _named(path, value, depth)
    return lines


def _named(path: str, value, depth: int) -> list[str]:
    """One value's names, or its value if it does not have any."""
    if depth <= 0:
        return [f"  {path:<24} {_short(value)}"]
    if isinstance(value, dict):
        lines = [f"  {path + '.*':<24} {', '.join(sorted(value)) or '(empty)'}"]
        for inner, deeper in value.items():
            if isinstance(deeper, (dict, list)):
                lines += _named(f"{path}.{inner}", deeper, depth - 1)
        return lines
    if isinstance(value, list):
        if not value:
            return [f"  {path:<24} (empty list)"]
        if not any(isinstance(item, (dict, list)) for item in value):
            # chainrssi and its like: a list of numbers is a value, not a shape.
            return [f"  {path:<24} {_short(value)}"]
        lines = [f"  {path:<24} a list of {len(value)}"]
        for index, item in enumerate(value):
            lines += _named(f"{path}.{index}", item, depth - 1)
        return lines
    return [f"  {path:<24} {value!r}"]


def _short(value, limit: int = 60) -> str:
    """A value as a reader can take it in, and never as a wall of JSON."""
    written = repr(value)
    return written if len(written) <= limit else written[:limit] + "..."


# -------------------------------------------------------------------- verdict


def _wrapped(names: list[str], width: int = 68) -> list[str]:
    """A list of field names over as many lines as it takes, and no wider."""
    lines: list[str] = []
    row = ""
    for name in names:
        piece = name + ", "
        if len(row) + len(piece) > width:
            lines.append("  " + row.rstrip())
            row = ""
        row += piece
    if row:
        lines.append("  " + row.rstrip().rstrip(","))
    return lines


def _reported_distance(payload: dict) -> bool:
    """Whether this radio sent a distance at all, in either of its two places."""
    return any(
        at_path(payload, path)[1]
        for path in ("wireless.distance", "wireless.sta.0.distance")
    )


def verdict(payload: dict) -> list[str]:
    status = parse_status(payload)
    lines = ["", "=" * 72, "VERDICT", "=" * 72, ""]
    if not status.connected and status.reason:
        lines += [
            f"This radio says the link is not up: {status.reason}.",
            "The console will draw that as a fault rather than as a blank, which is",
            "what it is.",
            "",
        ]
    if status.signal_dbm is None:
        lines += [
            "The console's link panel will show dashes for the signal, because the",
            "signal is not where vmd/radio/airos.py looks for it. Send the two",
            "blocks above - the raw JSON and the field list - and it is a one-line",
            "change to parse_status.",
        ]
    else:
        lines += [
            f"The console's link panel will show {status.signal_dbm} dBm as its headline.",
        ]
        if status.remote_signal_dbm is not None:
            lines += [
                f"The far end hears {status.remote_signal_dbm} dBm back, which is the "
                "other half of",
                "a point-to-point link and is on the same line of the panel.",
            ]
    if status.airtime_percent is not None:
        lines += [
            "",
            f"Airtime: {status.airtime_percent:g}% of it is spent. This is the figure the",
            "panel leads with, and it is the one that says whether another stream will",
            "fit - a wireless link runs out of TIME, not of bits per second.",
        ]
    unknown = [
        name
        for name, value in status.as_dict().items()
        if value in (None, "") and name != "reason"
    ]
    if unknown:
        lines += [
            "",
            "These are not in this radio's answer. The panel leaves them off rather",
            "than showing them as zero:",
        ]
        lines += _wrapped(unknown)
    if status.ccq is not None and status.ccq > 100:
        lines += [
            "",
            f"CCQ came back as {status.ccq:g}, which is not a percentage. The parser",
            "reads anything above 100 as tenths of a percent, which is the 0-1000",
            "scale airOS uses where it reports ccq at all. Check it against the",
            "radio's own web interface while you are in there.",
        ]
    if status.tx_capacity_mbps is not None or status.rx_capacity_mbps is not None:
        lines += [
            "",
            "The capacity figures are an ESTIMATE and the panel now says so. They are",
            "polling.dl_capacity / ul_capacity, in kbps - which is confirmed on a real",
            "radio rather than assumed, against a throughput in the same unit beside",
            "them. What has NOT been confirmed is that they mean anything: 194 Mb/s",
            "on a 40 MHz link is a modulation rate worked out from the signal, and it",
            "sat on this panel beside an airtime of 88% saying the link was 13% used.",
            "The airtime is the figure to trust.",
        ]
    if _reported_distance(payload):
        lines += [
            "",
            "This radio reported a distance and the panel does not show it. On the",
            "device this was written against, wireless.distance was 0 and the station's",
            "own was 1, on a path that is really 15 km: neither field is in metres and",
            "nothing here knows what they are. A distance nobody can justify is left",
            "off rather than printed as '1 m'.",
        ]
    lines += ["", "Nothing above was assumed. Every line is something the radio said.", ""]
    return lines


# ----------------------------------------------------------------------- main


WHAT_THIS_IS = """
Ask the Ubiquiti radio what it actually reports, and what the console makes of
it. Point this at the radio when the link panel shows dashes, or when the
console says it cannot log in. It only reads; it changes nothing.
"""

EXAMPLE = """
Example - copy this line and change only the address and the username:

    python probe_radio.py 192.168.1.20 --user ubnt

The address is the radio's: the one you would type into a browser to reach it.
The username is usually ubnt.

It then asks for the password. Type it at the prompt - there is deliberately no
way to put it on the command line, because PowerShell keeps every command that
is typed, in plain text, for ever.

From the VMD folder rather than from inside spike, the same run is:

    {how_to_run}
"""


class Parser(argparse.ArgumentParser):
    """An argument parser whose failure is the instructions.

    argparse prints the usage line and the name of the missing argument, which
    is correct and useless: it tells somebody that a word is missing, not what to
    type. This tool exists precisely so that a fault can be diagnosed by a person
    with no terminal skills, and their first contact with it was that message. So
    the whole help - which carries a complete, copyable command - is what a
    mistake prints.
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        print()
        print(f"  {message}")
        print()
        self.print_help()
        raise SystemExit(2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = Parser(
        description=WHAT_THIS_IS,
        epilog=EXAMPLE.format(how_to_run=HOW_TO_RUN),
        # Raw, because argparse would otherwise reflow the example onto one line
        # and it would stop being something anyone can copy. The prose above it
        # is wrapped by hand for the same reason.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("host", help="the radio's address, for example 192.168.1.20")
    parser.add_argument(
        "--user", default="ubnt", help="the radio's username (usually ubnt)"
    )
    # There is deliberately no --password. PowerShell keeps every command that
    # is typed, in plain text, in ConsoleHost_history.txt, for ever.
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, ask=getpass.getpass, fetch=fetch_status) -> int:
    args = parse_args(argv)
    try:
        password = ask(f"password for {args.user}@{args.host}: ")
    except (KeyboardInterrupt, EOFError):
        # The owner typed the wrong address, reached this prompt, and pressed
        # Ctrl-C - and got a KeyboardInterrupt traceback out of getpass. Nothing
        # an operator does may come back as a stack trace.
        print()
        print("  Stopped. Nothing was sent to the radio.")
        print()
        return 1

    print()
    print(f"probing {args.host} as {args.user}")
    print()

    try:
        answer = fetch(args.host, args.user, password, TIMEOUT)
    except KeyboardInterrupt:
        # A radio that is not answering takes about twelve seconds per flow, so
        # this is a prompt somebody waits at and gives up on.
        print()
        print("  Stopped while waiting for the radio.")
        print()
        return 1
    except ProbeError as exc:
        # The exchange first and the verdict second: the login is where the
        # failure is, so printing only the sentence would throw away the report.
        for line in login_lines(getattr(exc, "exchange", []), password):
            print(line)
        print()
        print(f"  [x] {redact(str(exc), password)}")
        print()
        print("  Send the block above. It says whether the radio ever opened a")
        print("  session, what it refused, and in which words.")
        print()
        return 1
    except Exception as exc:  # noqa: BLE001 - a sentence beats a traceback, always
        print(f"  [x] the radio could not be read: {redact(str(exc), password)}")
        print()
        return 1

    for line in login_lines(answer.exchange, password, answer.flow):
        print(line)
    print()

    for note in answer.notes:
        # Redacted like every other line here, and this one was not. The notes
        # are built in `fetch_status` out of what the RADIO said - `exc.said` is
        # quoted into them verbatim - and a radio that echoes the form it
        # rejected puts a percent-encoded password in one. This whole report
        # exists to be sent to somebody else; it may not carry a password.
        print(f"  [ ] {redact(note, password)}")
    print(redact(f"  [ok] {answer.scheme}://{args.host}/status.cgi answered", password))
    if answer.scheme == "https":
        print("       (its certificate is self-signed and was not checked, as the")
        print("        console does not check it either)")
    elif answer.notes:
        print("       https was tried first and did not answer, which is ordinary on")
        print("        a radio whose web interface is plain http. The console tries")
        print("        them in the same order, so it will reach this radio too.")
    print()

    body = redact(answer.body, password)
    try:
        payload = json.loads(answer.body)
    except json.JSONDecodeError:
        print("  [x] the radio answered with a page, not JSON. In airOS that means")
        print("      the login was not accepted, so status.cgi returned the login")
        print("      page. Check the username and password.")
        print()
        print("  What it sent, first 20 lines:")
        for line in body.splitlines()[:20]:
            print(f"      {line}")
        print()
        return 1

    print("-" * 72)
    print("what the radio said (status.cgi, exactly as it came, password redacted)")
    print("-" * 72)
    print(redact(json.dumps(payload, indent=2, sort_keys=True), password))
    print()

    print("-" * 72)
    print("what vmd/radio/airos.py makes of it")
    print("-" * 72)
    for line in field_lines(payload):
        print(redact(line, password))
    print()

    print("-" * 72)
    print("the names this radio actually uses")
    print("-" * 72)
    for line in key_lines(payload):
        print(redact(line, password))

    for line in verdict(payload):
        print(redact(line, password))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:  # pragma: no cover - the last catch, and quiet
        print()
        print("  Stopped.")
        code = 1
    raise SystemExit(code)
