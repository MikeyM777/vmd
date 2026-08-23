"""What the radio's answer says to somebody with no terminal.

The radio writes its own sentences, and they are good sentences for whoever is
holding the source: they carry the code it answered, the address it was asked
at, every login flow that was tried, and - last - a program to run and send the
output of. The operator of this console has no terminal, no second machine and
no source. Fourteen wrapped grey lines ending "Run spike/probe_radio.py against
this radio and send what it prints" is, to him, a paragraph that ends in a wall.

So the radio's sentence is split in two rather than replaced:

* **the line he can act on**, which is what the Link panel shows;
* **everything else**, which is technical detail and goes to the Logs tab -
  where technical detail belongs, where it is already reachable, and where
  whoever is helping him can read it out to him over a phone.

The radio's own words survive the split wherever it has any. When it answers
`"Invalid credentials."` that is the useful part of the whole paragraph, and it
is quoted rather than paraphrased: the console guessing at what a refusal means
is how "check the password" gets said about a radio whose password is right.

Nothing here asks the radio anything, and nothing here decides whether the link
is healthy - that is `window._link_state`, against the bands in
`vmd/radio/panel.py`. This is only the wording.
"""

from __future__ import annotations

import re

# What the radio said, in quotes, out of the sentence that carries it. The
# parser is deliberately loose about the words around it: `airos._refusal` is
# free to reword its own sentence, and the one thing this must keep hold of is
# the part between the quotes, which is the radio's and not ours.
_QUOTED = re.compile(r'said so:\s*"([^"]+)"')

# A trailing "check the ... in Settings" the radio already added for itself.
# Repeated, it reads as the console saying the same thing twice in one line.
_ALREADY_SAYS_SETTINGS = re.compile(r"\bin settings\b", re.IGNORECASE)


def link_trouble(reason: str) -> tuple[str, str]:
    """A radio's reason, as (the chip's few words, the panel's first line).

    Both halves are for the operator. The chip is what he glances at across the
    room, so it is short enough to sit in a one-line band beside three other
    chips; the panel line is what he reads when he has walked over, so it says
    what to do about it.

    Anything this does not recognise comes back as its own first sentence -
    never as an invention, and never as nothing. A radio that has grown a new
    refusal must still put something true on the screen.
    """
    words = str(reason or "").strip()
    if not words:
        return ("link could not be read", "The radio could not be read.")

    said = _QUOTED.search(words)
    if said is not None:
        # Its own words, quoted. The console does not know what a particular
        # airOS build means by them and does not pretend to; what it can say is
        # where the two things behind every refusal are typed.
        return (
            "link: the radio refused the login",
            f'The radio refused the login and said: "{said.group(1)}" '
            "Check the username and the password in Settings.",
        )

    lowered = words.lower()
    if "401" in lowered or "refused the username or password" in lowered:
        return (
            "link: the radio would not accept the password",
            "The radio would not accept the username or password. "
            "Check them in Settings.",
        )
    if "403" in lowered:
        # 403 is not the same as a wrong password and the panel may not say it
        # is: airOS answers 403 to a login it does not recognise as coming from
        # its own page, and after too many tries. Both of those come right on
        # their own, which is why the line ends by saying so.
        return (
            "link: the radio refused the login",
            "The radio is reachable but refused the login. Check the password "
            "in Settings; if it is right, leave it and it will try again.",
        )
    if lowered.startswith("cannot reach"):
        return (
            "link: no answer from the radio",
            f"No answer from the radio{_at(words)}. Check that it is powered "
            "and that its address in Settings is right.",
        )

    first = _first_sentence(words)
    return ("link: " + first[0].lower() + first[1:], _as_sentence(first))


def _at(words: str) -> str:
    """" at 192.168.1.20", when the radio's sentence named an address."""
    match = re.search(r"cannot reach ([^\s(:]+)", words)
    return f" at {match.group(1)}" if match is not None else ""


def _first_sentence(words: str) -> str:
    """Up to the first full stop that ends a sentence rather than an address.

    A full stop with a digit on both sides of it is inside 192.168.1.20, and
    cutting there would leave the operator reading "cannot reach 192". That is
    handled entirely by the pattern: it matches a full stop only where a space
    or the end of the string follows it, and every full stop inside an address
    is followed by a digit. There used to be a second check here, reading the
    character after the stop and skipping it when that was a digit - which the
    pattern had already made impossible, so it never once ran. It read like the
    thing keeping addresses whole, and the pattern was doing it.
    """
    for match in re.finditer(r"\.(\s|$)", words):
        return words[: match.start()].strip()
    return words.strip()


def _as_sentence(words: str) -> str:
    text = words.strip()
    if not text:
        return ""
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


def shortened(link: dict) -> tuple[dict, str]:
    """The radio's reading with its reason rewritten, and the detail it dropped.

    The detail comes back rather than being logged here, so that whoever calls
    this decides how often it is worth saying - this runs on a two-second
    heartbeat, and a console that logged the same paragraph thirty times a
    minute would destroy the ring buffer that is the only diagnostic on the
    machine.

    A reading that is fine, one that is still being taken, and one whose
    sentence is already short enough come back untouched, with no detail: there
    is nothing to move to the Logs tab and nothing to shorten.
    """
    if not isinstance(link, dict):
        return ({}, "")
    reason = str(link.get("reason") or "")
    if link.get("checking") or link.get("connected") or not reason:
        return (dict(link), "")
    _glance, headline = link_trouble(reason)
    if headline.strip().rstrip(".") == _as_sentence(reason).strip().rstrip("."):
        return (dict(link), "")
    shorter = dict(link)
    shorter["reason"] = headline
    return (shorter, reason)
