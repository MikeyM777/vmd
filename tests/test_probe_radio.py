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

HOST = "10.0.0.9"
USER = "ubnt"
# Deliberately full of characters that are escaped when a form is encoded: the
# redaction has to catch both the typed form and the percent-encoded one.
PASSWORD = "p@ss word/1"

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


def fetch_returning(payload, scheme: str = "https", notes: list[str] | None = None):
    body = payload if isinstance(payload, str) else json.dumps(payload)

    def fetch(host: str, username: str, password: str, timeout: float = 0.0):
        return probe_radio.Answer(scheme=scheme, body=body, notes=list(notes or []))

    return fetch


def run(argv, fetch, password: str = PASSWORD, capsys=None):
    code = probe_radio.main(argv, ask=lambda prompt: password, fetch=fetch)
    return code, capsys.readouterr().out


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


def test_the_password_is_redacted_in_both_forms(capsys) -> None:
    """It has bitten this project once already, in the diagnostic report: a
    password that is masked in one form and printed percent-encoded in the other
    is a password that has been printed."""
    leaky = dict(STATUS)
    leaky["debug"] = f"last login username={USER}&password={PASSWORD} uri=/"
    encoded = "p%40ss%20word%2F1"
    _code, out = run([HOST], fetch_returning(leaky), capsys=capsys)
    assert PASSWORD not in out
    assert encoded not in out
    assert "password" in out.lower(), "the field itself is still shown, redacted"


def test_the_redaction_covers_both_forms_directly() -> None:
    text = f"password={PASSWORD} and password=p%40ss%20word%2F1"
    hidden = probe_radio.redact(text, PASSWORD)
    assert PASSWORD not in hidden
    assert "p%40ss%20word%2F1" not in hidden
    assert hidden.count(probe_radio.REDACTED) == 2


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
