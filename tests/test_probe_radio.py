"""`spike/probe_radio.py` - the tool that turns "the panel shows dashes" into a fix.

The airOS parser was written from general knowledge of airOS and has never been
pointed at a real radio. If the owner's build names its fields differently, the
console shows `link -` for ever and nothing says why. This tool is the thing
that answers it, so its output *is* the deliverable and is what these tests are
about: the raw JSON, what the parser made of it, and - the part that turns an
investigation into a five-minute edit - the names of the keys it looked for and
did not find.

Nothing here touches a network. The fetch is injected.
"""

from __future__ import annotations

import json
import ssl
import urllib.error

import pytest

from spike import probe_radio
from tests.test_radio import ADVERSARIAL_PASSWORDS, REAL_STATUS, encodings, leaks

HOST = "10.0.0.9"
USER = "ubnt"
# The weak one, kept as the password every non-redaction test types at the
# prompt. The redaction tests below run over `ADVERSARIAL_PASSWORDS` instead:
# this one is drawn entirely from the alphabet the redaction already handled,
# which is exactly why it could not catch the leak it was written for.
PASSWORD = ADVERSARIAL_PASSWORDS[0]

STATUS = {
    "host": {"hostname": "LOCO-north", "uptime": 84231, "devmodel": "NanoStation 5AC loco"},
    "wireless": {
        "essid": "vmd-link",
        "signal": -63,
        "noisef": -96,
        "ccq": 985,
        "distance": 15400,
        "throughput": {"tx": 512, "rx": 4200},
        "polling": {"dl_capacity": 24000, "ul_capacity": 18000},
    },
}


def fetch_returning(
    payload,
    scheme: str = "https",
    notes: list[str] | None = None,
    exchange: list | None = None,
    flow: str = "cold",
):
    body = payload if isinstance(payload, str) else json.dumps(payload)

    def fetch(host: str, username: str, password: str, timeout: float = 0.0):
        return probe_radio.Answer(
            scheme=scheme,
            body=body,
            notes=list(notes or []),
            exchange=list(exchange or []),
            flow=flow,
        )

    return fetch


def run(argv, fetch, password: str = PASSWORD, capsys=None):
    code = probe_radio.main(argv, ask=lambda prompt: password, fetch=fetch)
    return code, capsys.readouterr().out


# --------------------------------------------------------- running the thing
#
# The operator ran this from inside the spike folder, with a bare `python`, and
# got a stock argparse error telling them an argument was missing and not what
# to type. This tool exists so that a fault can be diagnosed by somebody with no
# terminal skills; its first contact with them may not be a puzzle.


def test_getting_it_wrong_prints_a_command_you_can_copy(capsys) -> None:
    """The failure output has to BE the instructions.

    A usage line is correct and useless: it says an argument is missing, not
    what to type. Whoever is reading this has no terminal skills and no second
    machine, and this is the tool that was supposed to help them.
    """
    with pytest.raises(SystemExit):
        probe_radio.parse_args([])
    printed = capsys.readouterr()
    said = printed.out + printed.err
    assert "probe_radio.py" in said
    # A whole line that can be copied, with an address that looks like an
    # address rather than like a placeholder to be worked out.
    assert "192.168.1.20" in said
    assert "--user ubnt" in said
    # And what happens next, since nothing on screen would say so.
    assert "password" in said.lower()


def test_ctrl_c_at_the_password_prompt_is_not_a_stack_trace(capsys) -> None:
    """The owner did exactly this - typed the wrong address, hit the prompt, and
    pressed Ctrl-C - and got a KeyboardInterrupt traceback out of getpass.
    Nothing an operator does may surface as a stack trace."""

    def interrupted(prompt: str) -> str:
        raise KeyboardInterrupt

    code = probe_radio.main([HOST], ask=interrupted, fetch=fetch_returning(STATUS))
    out = capsys.readouterr().out
    assert code != 0
    assert "Traceback" not in out
    assert "KeyboardInterrupt" not in out
    assert out.strip(), "it may be quiet, but not silent"


def test_a_shut_stdin_is_the_same_thing() -> None:
    """Running it where nothing can be typed - a double-click, a pipe - reaches
    getpass and gets EOFError. Same treatment: a sentence, not a traceback."""

    def shut(prompt: str) -> str:
        raise EOFError

    assert probe_radio.main([HOST], ask=shut, fetch=fetch_returning(STATUS)) != 0


def test_it_finds_the_console_it_reports_on_from_wherever_it_is_run() -> None:
    """It imports the console's own parser, so `python probe_radio.py` typed
    inside the spike folder has to reach it. The point of this tool is that it
    reports what THIS console does; a copy of the parser that could drift would
    make it report about nothing."""
    from pathlib import Path

    root = Path(probe_radio.__file__).resolve().parent.parent
    assert (root / "vmd" / "radio" / "airos.py").exists()
    assert probe_radio.parse_status is not None


def test_the_way_out_is_a_whole_command_and_not_advice() -> None:
    """When the interpreter it was handed cannot run this, the sentence has to
    contain the line to type instead - the folder to be in, and everything
    after it."""
    assert "uv run" in probe_radio.HOW_TO_RUN
    assert "spike" in probe_radio.HOW_TO_RUN
    assert "--user ubnt" in probe_radio.HOW_TO_RUN
    assert "192.168.1.20" in probe_radio.HOW_TO_RUN


# ------------------------------------------------------------- the password


def test_the_password_is_never_asked_for_on_the_command_line() -> None:
    """PowerShell writes every command typed into ConsoleHost_history.txt, in
    plain text, for ever. INSTALL.md was corrected once for this exact reason."""
    with pytest.raises(SystemExit):
        probe_radio.parse_args([HOST, "--password", PASSWORD])


def test_the_password_is_prompted_for() -> None:
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return PASSWORD

    probe_radio.main([HOST], ask=ask, fetch=fetch_returning(STATUS))
    assert asked, "the password must be prompted for, not left blank"


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_the_password_is_redacted_in_every_encoding(capsys, password: str) -> None:
    """It has bitten this project twice, and both times in a report meant to be
    sent to somebody else: a password masked in one form and printed in another
    is a password that has been printed.

    Every encoding at once, in the one field the radio is quoted verbatim in, so
    that a form nobody thought of fails here rather than in an inbox.
    """
    leaky = dict(STATUS)
    leaky["debug"] = " ".join(
        f"{name}={form}" for name, form in encodings(password).items()
    )
    _code, out = run([HOST], fetch_returning(leaky), password=password, capsys=capsys)
    assert leaks(out, password) == [], "the report carries the password"
    assert "password" in out.lower(), "the field itself is still shown, redacted"


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_the_redaction_covers_every_encoding_directly(password: str) -> None:
    text = " and ".join(f"password={form}" for form in encodings(password).values())
    hidden = probe_radio.redact(text, password)
    assert leaks(hidden, password) == []
    assert probe_radio.REDACTED in hidden


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_a_note_the_radio_wrote_is_not_printed_as_it_came(capsys, password: str) -> None:
    """The notes are the radio's own words about a login that did not work, and
    they were the one line of this report printed verbatim.

    They are built from what the radio said - `fetch_status` quotes `exc.said`
    into them - and a radio that echoes the form it rejected puts the password
    in there percent-encoded. This tool exists so that a non-technical operator
    can send its output to somebody else; it has been pasted into a chat once
    already today. It may not be able to carry a password out of the building.
    """
    said = " / ".join(f"{name}={form}" for name, form in encodings(password).items())
    notes = [f'https session login - the radio refused it and said: "rejected {said}"']
    code, out = run([HOST], fetch_returning(STATUS, notes=notes), password=password, capsys=capsys)
    assert code == 0
    assert "refused it and said" in out, "the note itself still reaches the operator"
    assert leaks(out, password) == [], "the note carried the password into the report"


def test_an_empty_password_redacts_nothing_rather_than_everything() -> None:
    assert probe_radio.redact("hello", "") == "hello"


# --------------------------------------------------------------- the output


def test_it_prints_the_raw_json_pretty_printed(capsys) -> None:
    _code, out = run([HOST], fetch_returning(STATUS), capsys=capsys)
    assert '"hostname": "LOCO-north"' in out, "the raw JSON, as the radio sent it"
    assert '\n    "wireless"' in out or '\n  "wireless"' in out, "pretty-printed"


def test_it_says_which_scheme_answered(capsys) -> None:
    _code, out = run([HOST], fetch_returning(STATUS, scheme="http"), capsys=capsys)
    assert "http://" in out


def test_it_prints_what_the_parser_made_of_it(capsys) -> None:
    _code, out = run([HOST], fetch_returning(STATUS), capsys=capsys)
    assert "-63" in out, "the signal the parser found"
    assert "LOCO-north" in out
    assert "24" in out, "the capacity, in Mb/s"


def test_it_names_the_keys_it_looked_for_and_did_not_find(capsys) -> None:
    """Without this the operator reports "the panel shows dashes" and someone
    spends a morning guessing. With it the fix is one line in parse_status."""
    payload = {"wireless": {"essid": "vmd-link", "signalLevel": -63}, "host": {}}
    _code, out = run([HOST], fetch_returning(payload), capsys=capsys)
    assert "wireless.signal" in out, "the key it looked for must be named"
    assert "unknown" in out.lower()
    # And what the radio actually calls its fields, so the mismatch is visible.
    assert "signalLevel" in out


def test_a_field_that_was_found_is_not_reported_as_missing(capsys) -> None:
    _code, out = run([HOST], fetch_returning(STATUS), capsys=capsys)
    signal_line = [line for line in out.splitlines() if line.strip().startswith("signal")]
    assert signal_line and "unknown" not in signal_line[0].lower()


# ---------------------------------------------- against the radio it was run on
#
# The probe was pointed at the owner's NanoStation 5AC loco and reported the
# signal as UNKNOWN, which was true of the parser and no longer is. Its field
# report has to look where the parser now looks, or it goes on saying a link is
# unreadable that the console is reading.


def test_it_finds_the_signal_of_a_station_where_the_parser_now_looks(capsys) -> None:
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    signal = next(
        line for line in out.splitlines() if line.strip().startswith("signal_dbm")
    )
    assert "-66" in signal, signal
    assert "unknown" not in signal.lower()
    assert "sta" in signal, "and it says which key it came off"


def test_it_reports_the_airtime_because_that_is_the_reading_that_mattered(capsys) -> None:
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    airtime = next(
        line for line in out.splitlines() if line.strip().startswith("airtime_percent")
    )
    # 73, not 88. 88 was `polling.use`, which a radio in the field proved is
    # rx_use and tx_use added together - and adding them put "175% of the link
    # in use" on the console. The busier direction is the reading.
    assert "73" in airtime, airtime
    assert "rx_use" in airtime, airtime


def test_it_reports_the_far_end_and_the_link_quality(capsys) -> None:
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    remote = next(
        line for line in out.splitlines() if line.strip().startswith("remote_signal_dbm")
    )
    assert "-63" in remote and "remote" in remote
    quality = next(
        line for line in out.splitlines() if line.strip().startswith("quality_percent")
    )
    assert "100" in quality and "linkscore" in quality, quality


def test_it_prints_the_names_inside_the_station_entry(capsys) -> None:
    """The station entry is where everything on this firmware turned out to be,
    and the key list walked straight past it: `sta` is a list, and the report
    only ever opened dictionaries. The names it missed are the names of the
    whole fix."""
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    for name in ("dl_avg_linkscore", "noisefloor", "chainrssi"):
        assert name in out, f"{name} is one of the names this radio uses"
    assert "wireless.sta" in out
    # And one level further in, because the far end's reading lives there.
    assert "remote" in out and "tx_throughput" in out


def test_the_verdict_leads_with_the_airtime_and_puts_the_capacity_in_its_place(
    capsys,
) -> None:
    """194 Mb/s of "capacity" printed as a headline beside 88% airtime is worse
    than useless: it is the number that made a full link look healthy."""
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    verdict = out.split("VERDICT")[-1].lower()
    assert "airtime" in verdict and "88" in verdict
    assert "estimate" in verdict, "the capacity has to be named as one"
    assert "kbps" in verdict and "confirmed" in verdict


def test_the_verdict_describes_the_panel_the_console_actually_draws(capsys) -> None:
    """The link panel led with the dBm figure. It has not since `c476aee`.

    It leads with one word for the whole link - GOOD, FAIR, BUSY, FULL, WEAK -
    with the dBm as the caption on the Signal bar and the sentences folded
    behind `Details`. This script is the one the operator is told to run when
    the radio will not read, and its output is what he sends on; a report
    describing a screen that no longer exists sends whoever is helping him
    looking for a figure that is not where it says.
    """
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    verdict = out.split("VERDICT")[-1]
    assert "as its headline" not in verdict, verdict
    assert "Signal bar" in verdict, verdict
    # And it names the vocabulary the headline is actually drawn from, so that
    # whoever reads this knows what to ask him to read out.
    assert "FULL" in verdict and "GOOD" in verdict, verdict


def test_the_verdict_says_why_no_distance_is_reported(capsys) -> None:
    """The radio sent a distance. Two of them, in fact - 0 and 1 - on a 15 km
    link, and the report has to say that is why the panel shows neither rather
    than leaving somebody to wonder where it went."""
    _code, out = run([HOST], fetch_returning(REAL_STATUS), capsys=capsys)
    verdict = out.split("VERDICT")[-1].lower()
    assert "distance" in verdict and "metres" in verdict


def test_a_link_with_nothing_associated_is_reported_as_down(capsys) -> None:
    payload = {"host": {}, "wireless": {"essid": "LOCO", "mode": "sta-ptp", "sta": []}}
    _code, out = run([HOST], fetch_returning(payload), capsys=capsys)
    assert "link is down" in out.lower() or "nothing is associated" in out.lower()


def test_a_radio_that_answers_everything_reports_a_usable_link(capsys) -> None:
    code, out = run([HOST], fetch_returning(STATUS), capsys=capsys)
    assert code == 0
    assert "panel" in out.lower(), "it must say what the console will now show"


# -------------------------------------------------------------- the failures
#
# A sentence, never a traceback. The operator has no terminal and no Python.


def test_a_login_page_instead_of_json_is_a_sentence(capsys) -> None:
    code, out = run([HOST], fetch_returning("<html><body>login</body></html>"), capsys=capsys)
    assert code == 1
    assert "Traceback" not in out
    assert "json" in out.lower() or "login" in out.lower()


def test_a_radio_that_could_not_be_read_is_a_sentence(capsys) -> None:
    def fetch(host, username, password, timeout=0.0):
        raise probe_radio.ProbeError("the radio refused the username or password")

    code, out = run([HOST], fetch, capsys=capsys)
    assert code == 1
    assert "Traceback" not in out
    assert "username or password" in out


def test_an_unexpected_failure_is_still_a_sentence(capsys) -> None:
    """Anything the tool did not think of must still leave the operator with
    something they could read out over the phone."""

    def fetch(host, username, password, timeout=0.0):
        raise ValueError("something nobody predicted")

    code, out = run([HOST], fetch, capsys=capsys)
    assert code == 1
    assert "Traceback" not in out
    assert "something nobody predicted" in out


def test_no_address_is_a_sentence_and_never_a_connection() -> None:
    with pytest.raises(probe_radio.ProbeError) as caught:
        probe_radio.fetch_status("", USER, PASSWORD)
    assert "address" in str(caught.value)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), "username or password"),
        (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "403"),
        (urllib.error.HTTPError("u", 500, "Server Error", {}, None), "500"),
        (ssl.SSLError("wrong version number"), "certificate"),
        (urllib.error.URLError(ConnectionRefusedError("refused")), "refused"),
        (TimeoutError("timed out"), "timed out"),
    ],
)
def test_every_way_a_radio_can_fail_reads_as_a_sentence(error, expected: str) -> None:
    said = probe_radio.reason_for(error)
    assert expected in said
    assert said == said.strip() and said, "a sentence, not an empty string"


def test_a_403_is_not_called_a_rejected_password() -> None:
    """The defect this file's tool now exists to settle. 403 is the radio
    refusing the request; whether the password is wrong is not in it."""
    said = probe_radio.reason_for(urllib.error.HTTPError("u", 403, "Forbidden", {}, None))
    assert "username or password" not in said
    assert "403" in said


# --------------------------------------------------------- the login exchange
#
# The radio in the field answers 403 to the login, so the login is where the
# failure now is, and dumping status.cgi says nothing about it. This section is
# the whole deliverable: the owner runs this once and sends back what it prints,
# and that has to be enough to settle the cause in one round rather than three.

SESSION = probe_radio.Exchange(
    flow="session",
    method="GET",
    url="https://10.0.0.9/login.cgi",
    status=200,
    headers={
        "Set-Cookie": "AIROS_SESSIONID=0123456789abcdef; path=/",
        "Server": "lighttpd",
        "Content-Type": "text/html",
    },
    cookies=["AIROS_SESSIONID"],
    hidden={"AIROS_TOKEN": "t0ken", "uri": "/index.cgi"},
    body="<html><form>...</form></html>",
)
REFUSED = probe_radio.Exchange(
    flow="cold",
    method="POST",
    url="https://10.0.0.9/login.cgi",
    status=403,
    said="Forbidden",
    headers={"WWW-Authenticate": 'Basic realm="airOS"', "Location": "/login.cgi"},
)


def test_it_prints_every_request_of_the_login_exchange(capsys) -> None:
    _code, out = run(
        [HOST], fetch_returning(STATUS, exchange=[SESSION, REFUSED]), capsys=capsys
    )
    assert "GET" in out and "POST" in out
    assert out.count("/login.cgi") >= 2, "every request, not just the last"
    assert "200" in out and "403" in out, "and the status of each"
    assert "session" in out and "cold" in out, "and which flow it belonged to"


def test_it_prints_the_response_headers_that_settle_this(capsys) -> None:
    """Set-Cookie says whether a session was ever opened, Location whether the
    login redirected, WWW-Authenticate whether it wanted HTTP auth instead, and
    Server which build this is. Those four are the answer."""
    _code, out = run(
        [HOST], fetch_returning(STATUS, exchange=[SESSION, REFUSED]), capsys=capsys
    )
    for header in ("Set-Cookie", "Location", "WWW-Authenticate", "Server"):
        assert header in out, f"{header} is one of the four that settles this"


def test_it_prints_the_hidden_fields_of_the_login_page(capsys) -> None:
    """This is where a CSRF token lives, and a token nobody sent back is the
    strongest candidate for the 403."""
    _code, out = run([HOST], fetch_returning(STATUS, exchange=[SESSION]), capsys=capsys)
    assert "AIROS_TOKEN" in out
    assert "t0ken" in out


def test_it_prints_the_cookies_it_holds_without_printing_their_values(capsys) -> None:
    """The name and the fact of a session cookie is the diagnosis. Its value is
    a live credential for the radio and nobody needs it read out over a phone."""
    _code, out = run([HOST], fetch_returning(STATUS, exchange=[SESSION]), capsys=capsys)
    assert "AIROS_SESSIONID" in out
    held = [line for line in out.splitlines() if "cookies held" in line.lower()]
    assert held, "it must say what it is holding"
    assert "0123456789abcdef" not in " ".join(held)


def test_it_says_which_flow_got_in(capsys) -> None:
    """Which login the radio accepted is the single most useful line here: it is
    the difference between a radio that wants a session opened first and one
    that does not, and that is the whole open question."""
    _code, out = run(
        [HOST], fetch_returning(STATUS, exchange=[SESSION], flow="session"), capsys=capsys
    )
    assert "the one this radio accepted" in out
    got_in = next(line for line in out.splitlines() if "accepted" in line)
    assert "session" in got_in

    _code, out = run(
        [HOST], fetch_returning(STATUS, exchange=[REFUSED], flow="cold"), capsys=capsys
    )
    got_in = next(line for line in out.splitlines() if "accepted" in line)
    assert "cold" in got_in


def test_a_cookie_is_named_and_measured_but_never_quoted() -> None:
    """It is a live credential for a device on the operator's desk, and this
    output is meant to be pasted into an email."""
    import http.cookiejar
    import urllib.request

    jar = http.cookiejar.CookieJar()
    request = urllib.request.Request("http://10.0.0.9/login.cgi")
    response = type(
        "R",
        (),
        {
            "info": lambda self: _headers("Set-Cookie: AIROS_SESSIONID=s3cr3tvalue; path=/"),
        },
    )()
    jar.extract_cookies(response, request)
    held = probe_radio._held(jar)
    assert held == ["AIROS_SESSIONID (11 characters)"]
    assert "s3cr3tvalue" not in " ".join(held)


def _headers(raw: str):
    import email

    return email.message_from_string(raw)


def test_the_login_exchange_is_printed_even_when_nothing_logged_in(capsys) -> None:
    """The failing case is the case. A tool that prints the exchange only when
    the login worked prints it exactly when nobody needs it."""

    def fetch(host, username, password, timeout=0.0):
        raise probe_radio.ProbeError("the radio answered HTTP 403", exchange=[SESSION, REFUSED])

    code, out = run([HOST], fetch, capsys=capsys)
    assert code == 1
    assert "Traceback" not in out
    assert "403" in out
    assert "Set-Cookie" in out, "the exchange, not just the error"
    assert "AIROS_TOKEN" in out


@pytest.mark.parametrize("password", ADVERSARIAL_PASSWORDS)
def test_the_login_exchange_never_prints_the_password(capsys, password: str) -> None:
    """In every encoding, and in a body, a header, a hidden field or a URL.

    One field per encoding, so that a form the redaction has stopped covering
    names itself rather than hiding behind the one beside it.
    """
    forms = encodings(password)
    leaky = probe_radio.Exchange(
        flow="cold",
        method="POST",
        url=f"https://10.0.0.9/login.cgi?u={USER}&p={forms['percent-encoded']}",
        status=403,
        said=f"rejected password={forms['typed']}",
        headers={"Set-Cookie": f"last={forms['form-encoded']}; note={forms['typed']}"},
        hidden={"prefill": forms["Python-escaped"]},
        body=f"username={USER}&password={forms['JSON-escaped']}&uri=%2F",
    )
    code, out = run(
        [HOST], fetch_returning(STATUS, exchange=[leaky]), password=password, capsys=capsys
    )
    assert code == 0
    assert "prefill" in out, "the exchange itself is still printed"
    assert leaks(out, password) == []


def test_the_bodies_are_truncated(capsys) -> None:
    """A radio's login page is tens of kilobytes of HTML and the owner has to
    paste this into an email."""
    huge = probe_radio.Exchange(
        flow="session",
        method="GET",
        url="https://10.0.0.9/login.cgi",
        status=200,
        body="x" * 20000,
    )
    _code, out = run([HOST], fetch_returning(STATUS, exchange=[huge]), capsys=capsys)
    assert "x" * 20000 not in out
    assert len(out) < 12000, f"the whole report came to {len(out)} characters"


def test_the_probe_tries_exactly_what_the_console_tries(capsys) -> None:
    """Two implementations that drift are worse than one that is wrong: the
    probe would then answer a question about a login the console does not send."""
    from vmd.radio.airos import LOGIN_FLOWS

    assert probe_radio.FLOWS == LOGIN_FLOWS


# ------------------------------------------------------ against a fake airOS
#
# Everything above injects the fetch. These run the real one, against the same
# fake radios the console is tested against - imported rather than copied, so
# that a radio the console is proved against is a radio the probe is proved
# against too. Loopback only; nothing here goes near a real address.

from tests.test_radio import Behaviour, FakeRadio, SESSION_COOKIE, TOKEN  # noqa: E402


@pytest.fixture
def fake_radio():
    import threading
    from http.server import ThreadingHTTPServer

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


def test_it_reports_the_session_a_token_radio_wanted(fake_radio: str) -> None:
    """The exchange has to show the cookie and the token, because those are the
    two things that decide whether the 403 was ever about the password."""
    Behaviour.mode = "token"
    answer = probe_radio.fetch_status(fake_radio, USER, "linkpass", timeout=5.0)
    assert answer.flow == "session"
    # The https attempt is in here too, recorded as the failure it was: this
    # fake speaks plain http, and what was tried is part of the report.
    assert [step for step in answer.exchange if step.status is None], "https was tried"
    page = next(
        step
        for step in answer.exchange
        if step.method == "GET" and "login.cgi" in step.url and step.status == 200
    )
    assert page.hidden["AIROS_TOKEN"] == TOKEN
    assert "Set-Cookie" in page.headers
    posted = next(step for step in answer.exchange if step.method == "POST")
    assert any("AIROS_SESSIONID" in cookie for cookie in posted.cookies)
    assert '"signal": -63' in answer.body or '"signal":-63' in answer.body


def test_it_reports_a_403_it_could_not_get_past(fake_radio: str) -> None:
    """The case in the field. Nothing logs in, and the report is the deliverable."""
    Behaviour.mode = "forbidden"
    with pytest.raises(probe_radio.ProbeError) as caught:
        probe_radio.fetch_status(fake_radio, USER, "linkpass", timeout=5.0)
    exchange = caught.value.exchange
    assert exchange, "the exchange has to survive the failure"
    refusals = [step for step in exchange if step.status == 403]
    assert len(refusals) >= 2, "both flows were tried and both were refused"
    assert {step.flow for step in refusals} >= {"session", "cold"}
    assert "username or password" not in str(caught.value)


def test_the_report_of_a_403_says_a_session_was_opened(fake_radio: str, capsys) -> None:
    """Which is the line that ends the investigation: a cookie was set and the
    radio still said no, or no cookie was ever set at all."""
    Behaviour.mode = "forbidden"

    def fetch(host, username, password, timeout=0.0):
        return probe_radio.fetch_status(fake_radio, username, password, timeout=5.0)

    code, out = run([HOST], fetch, password="linkpass", capsys=capsys)
    assert code == 1
    assert "Traceback" not in out
    assert "AIROS_SESSIONID" in out, "the cookie the radio set"
    assert "AIROS_TOKEN" in out, "the token its login page carried"
    assert "403" in out
    assert "username or password" not in out
    # Set-Cookie itself is quoted whole and on purpose: its path, its flags and
    # whether there is more than one of it are the diagnosis. What is never
    # quoted is the jar, which is a running tally and would repeat that value on
    # every line of the report.
    assert SESSION_COOKIE in out
    held = [line for line in out.splitlines() if "cookies held" in line]
    assert held and SESSION_COOKIE.split("=")[1] not in " ".join(held)


def test_the_probe_never_reaches_a_radio_through_a_proxy(monkeypatch) -> None:
    """It posts the radio's password. urllib otherwise honours http_proxy and,
    on Windows, the registry's proxy settings, and on an air-gapped machine that
    is traffic which should not exist at all."""
    import http.cookiejar
    import urllib.request

    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    opener = probe_radio._opener(http.cookiejar.CookieJar())
    routed = [
        handler.proxies
        for handler in opener.handlers
        if isinstance(handler, urllib.request.ProxyHandler) and handler.proxies
    ]
    assert routed == [], f"the radio would be reached through {routed}"
