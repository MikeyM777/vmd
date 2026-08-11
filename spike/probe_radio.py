"""Point this at the real Ubiquiti radio, once, and the guesswork is over.

`vmd/radio/airos.py` reads airOS's `status.cgi`. It was written from general
knowledge of airOS and **has never been pointed at a real radio**. The payload
the test suite serves was invented, not captured. If the owner's airOS version
or model names its fields differently, the console's link panel shows dashes for
ever and nothing on screen says why.

    uv run python spike/probe_radio.py 10.0.0.9 --user ubnt

It asks for the password rather than taking it on the command line, because
PowerShell writes every command that is typed into `ConsoleHost_history.txt` in
plain text and keeps it. Then it prints three things:

  1. the raw JSON the radio sent, pretty-printed, with the password redacted in
     both the typed and the percent-encoded form;
  2. what `parse_status` made of it, field by field - including, for anything it
     could not find, the exact JSON keys it looked for;
  3. the names this radio actually uses, so a mismatch between the two is
     visible at a glance.

That turns "the panel shows dashes" into a one-line edit in `airos.py` instead
of a morning of guessing. Read-only throughout: nothing here changes a radio
setting, because a console that can reconfigure the link it depends on is a
console that can cut itself off.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# So that `python spike/probe_radio.py` works as well as `uv run python ...`:
# this reports on what the console's own parser does, and it must be the same
# parser, not a copy of it that can drift.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vmd.radio.airos import parse_status  # noqa: E402

TIMEOUT = 6.0

REDACTED = "***"


class ProbeError(Exception):
    """The radio could not be read, with a sentence saying why."""


# The keys `parse_status` looks for, in the order it looks for them. Printed
# beside anything that came back unknown, because the name it wanted is the
# whole of the fix.
LOOKED_FOR: dict[str, tuple[str, ...]] = {
    "connected": ("wireless.essid", "wireless.count"),
    "signal_dbm": ("wireless.signal", "wireless.rssi (added to the noise floor)"),
    "noise_dbm": ("wireless.noisef", "wireless.noise"),
    "ccq": ("wireless.ccq",),
    "tx_mbps": ("wireless.throughput.tx",),
    "rx_mbps": ("wireless.throughput.rx",),
    "tx_capacity_mbps": ("wireless.polling.dl_capacity", "wireless.txrate"),
    "rx_capacity_mbps": ("wireless.polling.ul_capacity", "wireless.rxrate"),
    "distance_m": ("wireless.distance",),
    "uptime_s": ("host.uptime",),
    "device": ("host.hostname", "host.devmodel"),
}

UNITS: dict[str, str] = {
    "signal_dbm": "dBm",
    "noise_dbm": "dBm",
    "tx_mbps": "Mb/s",
    "rx_mbps": "Mb/s",
    "tx_capacity_mbps": "Mb/s",
    "rx_capacity_mbps": "Mb/s",
    "distance_m": "m",
    "uptime_s": "s",
}


@dataclass
class Answer:
    """One reading of status.cgi, and how it was reached."""

    scheme: str
    body: str
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ redaction


def redact(text: str, password: str) -> str:
    """Hide the password in every form this program could have written it.

    Both forms, and this is not belt and braces: the login is posted as an
    encoded form, so a radio that echoes what it was sent - or a diagnostic that
    prints the request - shows `p%40ss`, not `p@ss`. Masking one and printing the
    other is printing it. That has already happened once in this project.
    """
    if not password:
        return text
    # safe="" on purpose: the default leaves "/" alone, which would have left
    # half of a password containing a slash on the screen.
    forms = (password, urllib.parse.quote(password, safe=""), urllib.parse.quote_plus(password))
    for form in forms:
        if form:
            text = text.replace(form, REDACTED)
    return text


# ------------------------------------------------------------------ the fetch


def reason_for(exc: BaseException) -> str:
    """One sentence for every way a radio refuses to be read."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "the radio refused the username or password"
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


def _opener() -> urllib.request.OpenerDirector:
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
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def fetch_status(host: str, username: str, password: str, timeout: float = TIMEOUT) -> Answer:
    """Log in and read status.cgi, https first and then http, as the console does."""
    host = host.strip()
    if not host:
        raise ProbeError("no radio address was given")

    notes: list[str] = []
    for scheme in ("https", "http"):
        opener = _opener()
        data = urllib.parse.urlencode(
            {"username": username, "password": password, "uri": "/"}
        ).encode()
        try:
            request = urllib.request.Request(
                f"{scheme}://{host}/login.cgi",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener.open(request, timeout=timeout).read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # Not a reason to try the other scheme: the radio answered, and
                # what it said was no.
                raise ProbeError(reason_for(exc)) from exc
            notes.append(f"{scheme}://{host}/login.cgi - {reason_for(exc)}")
            continue
        except (urllib.error.URLError, OSError) as exc:
            notes.append(f"{scheme}://{host}/login.cgi - {reason_for(exc)}")
            continue

        try:
            with opener.open(f"{scheme}://{host}/status.cgi", timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            notes.append(f"{scheme}://{host}/status.cgi - {reason_for(exc)}")
            continue
        return Answer(scheme=scheme, body=body, notes=notes)

    raise ProbeError(
        f"{host} could not be read. What was tried:\n"
        + "\n".join(f"      {note}" for note in notes)
    )


# ---------------------------------------------------------------- the reading


def at_path(payload: dict, path: str):
    """The value at a dotted path, or `None` and False if it is not there."""
    # Only the part before a bracket or a space: the table above annotates one
    # path with how it is used, and that annotation is for the reader.
    node = payload
    for step in path.split(" ")[0].split("."):
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
    for name, paths in LOOKED_FOR.items():
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


def key_lines(payload: dict, prefix: str = "") -> list[str]:
    """The names this radio actually uses, two levels deep."""
    lines: list[str] = []
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            lines.append(f"  {path + '.*':<24} {', '.join(sorted(value)) or '(empty)'}")
            for inner, deeper in value.items():
                if isinstance(deeper, dict):
                    lines.append(
                        f"  {path + '.' + inner + '.*':<24} "
                        f"{', '.join(sorted(deeper)) or '(empty)'}"
                    )
        else:
            lines.append(f"  {path:<24} {value!r}")
    return lines


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


def verdict(payload: dict) -> list[str]:
    status = parse_status(payload)
    lines = ["", "=" * 72, "VERDICT", "=" * 72, ""]
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
            f"CCQ came back as {status.ccq:g}, which is not a percentage. The panel",
            "reads anything above 100 as tenths of a percent, which is the 0-1000",
            "scale airOS is understood to use. Check it against the radio's own web",
            "interface while you are in there.",
        ]
    if status.tx_capacity_mbps is not None or status.rx_capacity_mbps is not None:
        lines += [
            "",
            "Check the capacity figures against the radio's own web interface. The",
            "parser reads polling.dl_capacity / ul_capacity as kbps and txrate /",
            "rxrate as Mb/s. That is what airOS is understood to do and it has not",
            "been confirmed on a real device - and a capacity in the wrong unit is",
            "exactly what would hide a link running full.",
        ]
    lines += ["", "Nothing above was assumed. Every line is something the radio said.", ""]
    return lines


# ----------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_radio",
        description="Ask the Ubiquiti radio what it actually reports, and what we make of it",
    )
    parser.add_argument("host", help="the radio's address, e.g. 10.0.0.9")
    parser.add_argument("--user", default="ubnt")
    # There is deliberately no --password. PowerShell keeps every command that
    # is typed, in plain text, in ConsoleHost_history.txt, for ever.
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, ask=getpass.getpass, fetch=fetch_status) -> int:
    args = parse_args(argv)
    password = ask(f"password for {args.user}@{args.host}: ")

    print()
    print(f"probing {args.host} as {args.user}")
    print()

    try:
        answer = fetch(args.host, args.user, password, TIMEOUT)
    except ProbeError as exc:
        print(f"  [x] {exc}")
        print()
        print("  Check the address, the username, the password, and that this")
        print("  machine is on the same network as the radio.")
        print()
        return 1
    except Exception as exc:  # noqa: BLE001 - a sentence beats a traceback, always
        print(f"  [x] the radio could not be read: {exc}")
        print()
        return 1

    for note in answer.notes:
        print(f"  [ ] {note}")
    print(f"  [ok] {answer.scheme}://{args.host}/status.cgi answered")
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
    raise SystemExit(main())
