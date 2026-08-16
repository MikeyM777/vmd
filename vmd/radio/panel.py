"""The link, in the Live tab's side column, in words an operator can act on.

The radio was read and then thrown away. `LinkStatus` carries signal, the far
end's signal, noise, link quality, airtime, throughput, capacity, uptime and the
device's name; the console showed one of them, `signal_dbm`, in the status bar,
and the design's side column - "steering, zoom, link, storage, recent movement" -
had no link in it at all. (There is no distance on it. The radio reports two
fields that look like one and neither is in metres, so it was removed rather
than printed - see `parse_status` in `vmd/radio/airos.py`.)

That mattered more here than it would elsewhere. **The link is the bottleneck of
this entire system**: one camera at the far end of a Ubiquiti point-to-point hop
of more than 15 km, and every bandwidth problem this project has had - 20-40 s
of latency, streams dropping during pans, video stuttering - was a link problem.

And when the radio was finally read, the figure that explains those turned out
not to be the one this panel led with. It showed throughput against the
negotiated capacity - "3.1 Mb/s of 24 Mb/s (13%)", a link with room to spare -
about a radio reporting **88% of its airtime spent**, carrying 10.7 Mb/s of
video, with PTZ commands taking two seconds to answer. Airtime is what a
wireless link runs out of; bits per second are what that airtime happens to buy
at the modulation rate of the moment. So the airtime leads, the capacity figure
is named as the estimate it is, and "will another stream fit" - the question the
whole 4K argument turns on - has an answer on the screen.

Two rules run through the whole file:

* **A number without a reading is not information.** An operator who does not
  know that -65 dBm is healthy and -85 dBm is marginal cannot act on -85. Every
  figure here carries what it means.
* **Nothing here asks the radio anything.** `RadioService` reads it on a worker
  for exactly this reason: an unreachable radio costs about 12 s of login
  timeouts, and this panel is redrawn on the window's two-second heartbeat. It
  reads the service's cached answer, the same one the status bar reads, and a
  reading that has gone stale is never shown as the state of the link now.

It lives beside the parser rather than in `vmd/desktop/` because what these
lines may claim is a property of what `parse_status` can actually know - the
"left off rather than shown as zero" rule below is the parser's rule, and the
two have to be read together.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import (
    PALETTE,
    SIZE_BAND,
    SIZE_HEADING,
    SIZE_SMALL,
    SPACE_HAIR,
    SPACE_SNUG,
    SPACE_TIGHT,
    WEIGHT_VALUE,
    state_colour,
    state_glyph,
)
from vmd.radio.meter import Meter

logger = logging.getLogger(__name__)

# How old a reading may be before the panel says how old it is. The radio is
# asked every four seconds and an unreachable one takes about twelve to answer,
# so anything inside this is the ordinary rhythm of a link that is up. The
# status bar uses the same number, and for the same reason.
STALE_AFTER_SECONDS = 15.0

# What the signal figure means on THIS deployment, and why these two numbers.
#
# The link is a Ubiquiti point-to-point of more than 15 km carrying about
# 5 Mb/s. An airOS radio's noise floor sits around -90 to -96 dBm, so:
#
# * At -65 dBm and stronger there is roughly 30 dB above the noise. That is
#   enough for the higher modulation rates, which is where the negotiated
#   capacity comes from - the link has room for the video and room to spare.
# * Between -65 and -80 the radio starts dropping to lower modulation rates and
#   the capacity falls with them. It still works. What it no longer has is
#   margin, and margin is what a 15 km path spends on rain, on wind moving a
#   mast, and on a dish that was aligned once in fair weather. This band is a
#   link that is one bad afternoon from trouble, so it is a warning and not
#   silence.
# * At -80 and weaker there is 10-15 dB above the noise: the lowest rates,
#   retries, and a capacity that can fall below what one video stream needs.
#   This is where the stuttering that started this project lives.
#
# These are the general airOS figures, not measurements of this link - nobody
# has read this radio yet. They are deliberately one band pessimistic: calling a
# working link "marginal" costs a phone call, and calling a marginal link
# "healthy" costs the picture at the moment somebody needed it.
SIGNAL_HEALTHY_DBM = -65.0
SIGNAL_MARGINAL_DBM = -80.0

# How full the link has to be before it is worth saying so. Above 70% a burst
# no longer fits and latency starts building; above 90% it is simply full, which
# is what a stuttering picture looks like from this end.
#
# These are the fractions of the CAPACITY figure, and they are now the fallback
# rather than the headline - see `_traffic_lines`. On a radio that reports its
# airtime they are not used at all.
BUSY_FRACTION = 0.7
FULL_FRACTION = 0.9

# Airtime: the share of the medium's TIME that is spent, and the reading that
# tells him whether another stream will fit. It is the question the whole 4K
# argument turns on and the panel did not show it.
#
# Why these two numbers, and why they are lower than the throughput fractions
# above:
#
# * A wireless link is one shared, half-duplex medium. What runs out is airtime,
#   not bits per second - the bits per second a given airtime buys depend on the
#   modulation rate, which falls with the signal, so the same 10 Mb/s can cost
#   30% of the air on a good day and 90% on a wet one. Throughput alone cannot
#   see that happen. Airtime can.
# * Delay does not rise with load, it rises with 1/(1-load). At 60% a packet
#   waits about two and a half times what it would on an empty link, at 80%
#   five times, at 90% ten. His PTZ commands take two seconds to answer at 88%,
#   which is that curve and not a camera fault.
# * Video is bursty. A key frame is several times the mean rate for a few
#   milliseconds, so a link with 20% of its air free cannot absorb a burst that
#   needs twice its average - the burst queues, and queueing is the latency and
#   the stutter.
#
# So: below 60% there is room for another stream; from 60% there is no room for
# a burst and latency is already building; from 80% the link is full and what is
# on it already is not getting through cleanly. Deliberately one band
# pessimistic, which is this panel's rule everywhere: calling a working link
# busy costs a phone call, calling a full link healthy costs the picture.
# How far past its own capacity estimate a link may measure before the reading
# is thrown out rather than believed.
#
# Not 100. airMAX's capacity is worked out from the modulation rate and the
# link genuinely beats it - that is why the airtime reading is preferred over
# this one wherever the radio gives both. 150% is a link doing better than the
# estimate; 5000% is one of the two figures being in kb/s while the other is in
# Mb/s, which is the failure this guards.
MOST_A_SHARE_CAN_BE = 150.0

AIRTIME_BUSY_PERCENT = 60.0
AIRTIME_FULL_PERCENT = 80.0

# Below this the link quality figure is saying the link is spending its time on
# retries rather than on data. Whichever field it came from - airMAX's
# linkscores on the firmware that was measured, ccq on older builds - the parser
# hands this panel a percentage, so there is one scale here and no guessing.
QUALITY_POOR_PERCENT = 80.0

# How far apart the two ends' signals have to be before the difference is worth
# a sentence. A few dB is ordinary - different radios, different cables, a
# different noise floor at each end. Six is not: that is one dish aimed better
# than the other, and it is a fault that is invisible from either end alone.
ASYMMETRY_DB = 6.0

# The mark that says whether readings are still arriving, and its two words.
#
# "I want the numbers from the signal to be automatically updated realtime - I
# want to see that it's actually capturing them." The second half of that is the
# hard half: a figure that is correct but never visibly moves is exactly what a
# frozen figure looks like, and this console has spent a day teaching its
# operator - correctly - not to believe a screen that looks calm.
#
# The age cannot carry it. The panel is redrawn on the same two-second beat that
# takes the reading, so the age at the moment of drawing is always about one
# beat: it would read "2 s ago" for ever, which is the frozen number all over
# again. So the mark advances once per reading that actually landed, and it
# advances on nothing else - a mark that moved on every redraw would say only
# that the console is redrawing, which he can already see.
#
# Two shapes and not two colours, which is the recording dot's rule and
# DESIGN.md's: a filled dot and a hollow one while readings are arriving, a
# still bar when they have stopped. What separates them across a room is the
# movement, not the ink - and the still bar is the state that has to be
# unmistakable, because it is the one that means the figures above are history.
#
# Quiet on purpose. It is one short muted line at the bottom of a panel someone
# stands in front of for months, and it changes at the pace of a slow pulse.
ARRIVING_GLYPHS = ("●", "○")
ARRIVING_WORDS = "readings arriving"
STOPPED_GLYPH = "■"
STOPPED_WORDS = "no new readings"

CHECKING_WORDS = "Checking the radio..."
NOT_SET_UP_WORDS = (
    "The radio is not set up. Enter its address, username and password in "
    "Settings and the link will be shown here."
)
NO_SIGNAL_WORDS = (
    "This radio did not report a signal strength. Run "
    "spike/probe_radio.py against it to find out what it calls the field."
)


def link_lines(link: dict) -> list[tuple[str, str]]:
    """The link panel, as (sentence, colour) pairs. Pure, and tested as such.

    `link` is whatever `RadioService.status()` last left behind. Four states, and
    they are not interchangeable: nobody has set the radio up, nobody has managed
    to read it yet, here is what it says, and here is what it said a while ago.
    """
    if link.get("checking"):
        return [(CHECKING_WORDS, PALETTE["muted"])]

    age = link.get("age_seconds")
    if not isinstance(age, (int, float)):
        # Nothing has been read and nothing is being read: there is no radio
        # configured to read. Not a fault, and not drawn as one.
        return [(_sentence(link.get("reason")) or NOT_SET_UP_WORDS, PALETTE["muted"])]

    stale = age >= STALE_AFTER_SECONDS
    lines: list[tuple[str, str]] = []

    if not link.get("connected"):
        lines.append(
            (_sentence(link.get("reason")) or "The radio could not be read.", PALETTE["alarm"])
        )
        if stale:
            lines.append((f"That was {_age(age)} ago.", PALETTE["warn"]))
        return lines

    lines += _signal_lines(link)
    lines += _traffic_lines(link)
    lines += _detail_lines(link)
    if stale:
        # Every figure goes grey, not only the ones that were green.
        #
        # Saying it was not enough. A panel full of coloured figures reads as a
        # panel full of current figures whatever the sentence underneath says,
        # and the operator has to be able to tell at a glance - without reading
        # anything - whether he is looking at now or at four minutes ago. The
        # rule was already here for the healthy colour, on the grounds that a
        # stale reading may not be drawn in the ink that means "the link is fine
        # right now"; it is just as true of the amber and the red, which claim
        # "the link is in trouble right now" and cannot say that either.
        lines = [(text, PALETTE["muted"]) for text, _colour in lines]
        lines.append(
            (
                f"Read {_age(age)} ago, so not necessarily the link now.",
                PALETTE["warn"],
            )
        )
    return lines


# Where the signal bar's two ends are, and why they are not the two thresholds
# above. The thresholds say what a reading MEANS; these say what a full bar and
# an empty one are, and a bar whose ends were the thresholds would be full at
# -65 dBm and empty at -80, which is a bar that spends its life pinned at one
# end or the other.
#
# -90 dBm is about the noise floor of an airOS radio: at the floor there is
# nothing left. -50 dBm is a short, well-aimed link in fair weather - better
# than this 15 km path will ever be, which is the point. A bar that reads 60%
# on a link that is working is telling the truth about a link that is working
# at 15 km; a bar that reads 100% would be a bar that cannot get better and
# therefore cannot get worse either.
SIGNAL_FLOOR_DBM = -90.0
SIGNAL_CEILING_DBM = -50.0

# The one word at the top, per state. Chosen for somebody who has never heard of
# dBm: what he needs from across the room is whether to do anything, and the
# number that made the decision is still on the bar underneath.
HEADLINE_CHECKING = "CHECKING"
HEADLINE_NOT_SET_UP = "NOT SET UP"
HEADLINE_NO_LINK = "NO LINK"
HEADLINE_WEAK = "WEAK"
HEADLINE_FULL = "FULL"
HEADLINE_BUSY = "BUSY"
HEADLINE_FAIR = "FAIR"
HEADLINE_GOOD = "GOOD"
# It answered and said nothing useful. Its own state, because "the radio is not
# answering" and "the radio is answering nonsense" send somebody to two
# different places.
HEADLINE_NO_FIGURES = "NO FIGURES"


def link_summary(link: dict) -> dict:
    """The whole link as one word, two bars and one short line. Pure, and tested.

    Same reading as `link_lines`, and deliberately the same thresholds - the two
    views may never disagree about the same radio, which is the rule the status
    band already follows about the signal. What differs is how much is said:
    this is what somebody sees without reading, and the sentences are what he
    gets when he asks for them.

    Returns a dict rather than a dataclass because everything downstream of it
    is a paint call and a test, and both want it by name.
    """
    blank = {
        "state": "muted",
        "headline": "",
        "note": "",
        "signal": None,
        "signal_state": "muted",
        "signal_caption": "",
        "use": None,
        "use_state": "muted",
        "use_caption": "",
        "use_marks": [],
        "carrying": "",
        "stale": False,
    }

    if link.get("checking"):
        return {**blank, "headline": HEADLINE_CHECKING, "note": "Reading the radio..."}

    age = link.get("age_seconds")
    if not isinstance(age, (int, float)):
        return {
            **blank,
            "headline": HEADLINE_NOT_SET_UP,
            "note": "Enter the radio's address, username and password in Settings.",
        }

    stale = age >= STALE_AFTER_SECONDS
    if not link.get("connected"):
        return {
            **blank,
            "state": "alarm",
            "headline": HEADLINE_NO_LINK,
            "note": _sentence(link.get("reason")) or "The radio could not be read.",
            "stale": stale,
        }

    signal = _number(link.get("signal_dbm"))
    signal_state = "muted"
    signal_percent = None
    signal_caption = ""
    if signal is not None:
        signal_percent = _share(signal, SIGNAL_FLOOR_DBM, SIGNAL_CEILING_DBM)
        signal_caption = f"{signal:.0f} dBm"
        if signal >= SIGNAL_HEALTHY_DBM:
            signal_state = "ok"
        elif signal >= SIGNAL_MARGINAL_DBM:
            signal_state = "warn"
        else:
            signal_state = "alarm"

    use, busy_at, full_at = _link_use(link)
    use_state = "muted"
    use_caption = ""
    if use is not None:
        use_caption = f"{use:.0f}%"
        if use >= full_at:
            use_state = "alarm"
        elif use >= busy_at:
            use_state = "warn"
        else:
            use_state = "ok"

    # Which of the two writes the headline. A weak signal comes first at the
    # alarm level and not because it is worse: it is the one with something to
    # do about it. A dish that has moved is fixed on the roof; a link that is
    # full because 4K is on it is fixed in Settings, and it is also what a weak
    # signal eventually causes, so naming the signal names the cause.
    if signal_state == "alarm":
        state, headline = "alarm", HEADLINE_WEAK
        note = "The signal is close to the noise - the picture can break up."
    elif use_state == "alarm":
        state, headline = "alarm", HEADLINE_FULL
        note = "Nothing else fits - the picture can stutter or drop during a pan."
    elif use_state == "warn":
        state, headline = "warn", HEADLINE_BUSY
        note = "Little room left for a burst, and delay builds from here."
    elif signal_state == "warn":
        state, headline = "warn", HEADLINE_FAIR
        note = "Working, with little margin left for rain or a mast that moved."
    elif signal_state == "muted" and use_state == "muted":
        # It answered, and said nothing about how the link is doing. A warning
        # and not a quiet state, which is what `link_lines` has always called it
        # and what this summary got wrong: the two views of one radio may not
        # disagree, and a chip drawing "nothing to report" over a radio that
        # refused to report is the same defect as the healthy word in the red
        # box, one layer down.
        state, headline = "warn", HEADLINE_NO_FIGURES
        note = "The radio answered but did not say how strong the link is."
    else:
        state, headline, note = "ok", HEADLINE_GOOD, ""

    if stale:
        # Grey, and it says why. A coloured word at the top of the panel claims
        # the link is like that NOW, and a reading from four minutes ago cannot
        # claim anything about now - the same rule the sentences follow.
        state = "muted"
        note = f"Last read {_age(age)} ago, so not necessarily the link now."
        signal_state = use_state = "muted"

    return {
        "state": state,
        "headline": headline,
        "note": note,
        "signal": signal_percent,
        "signal_state": signal_state,
        "signal_caption": signal_caption,
        "use": use,
        "use_state": use_state,
        "use_caption": use_caption,
        # The marks travel with the reading because they are not the same two
        # marks for both readings: airtime turns at 60 and 80, a share of the
        # capacity at 70 and 90, and a bar carrying the wrong scale under the
        # right number is a lie drawn very precisely.
        "use_marks": [] if use is None else [busy_at, full_at],
        "carrying": _carrying(link),
        "stale": stale,
    }


def _link_use(link: dict) -> tuple[float | None, float, float]:
    """How much of the link is spent, and the two marks that reading turns on.

    Airtime when the radio reports it, because airtime is what a wireless link
    runs out of. The busiest direction's share of its capacity when it does not
    - a weaker reading with its own, higher marks, which is why they travel with
    the number instead of being constants at the point of use.
    """
    airtime = _number(link.get("airtime_percent"))
    if airtime is not None and 0.0 <= airtime <= 100.0:
        return airtime, AIRTIME_BUSY_PERCENT, AIRTIME_FULL_PERCENT

    busiest = None
    for key, capacity_key in (("rx_mbps", "rx_capacity_mbps"), ("tx_mbps", "tx_capacity_mbps")):
        rate = _number(link.get(key))
        capacity = _number(link.get(capacity_key))
        if rate is None or not capacity or capacity <= 0:
            continue
        share = rate / capacity * 100.0
        busiest = share if busiest is None else max(busiest, share)
    # The same rule as the airtime one, applied to the reading that replaces it.
    #
    # This one is a ratio of two numbers the radio reports separately, so a unit
    # that is wrong on one of them - kb/s read as Mb/s, or the other way round -
    # is a thousandfold error that arrives looking exactly like a busy link.
    # There is one legitimate reason to be over 100 here, which is that airMAX's
    # capacity figure is an estimate and the link can beat it; there is no
    # legitimate reason to be over MOST_A_SHARE_CAN_BE.
    if busiest is not None and not 0.0 <= busiest <= MOST_A_SHARE_CAN_BE:
        logger.warning(
            "the link's throughput works out at %.0f%% of the capacity the radio "
            "reports, which is not a share of anything - one of the two figures "
            "is in the wrong unit. It is being ignored rather than acted on.",
            busiest,
        )
        busiest = None
    return busiest, BUSY_FRACTION * 100.0, FULL_FRACTION * 100.0


def _carrying(link: dict) -> str:
    """The traffic, in as few characters as it can be said in."""
    parts = [
        f"{value:.1f} Mb/s {way}"
        for value, way in (
            (_number(link.get("rx_mbps")), "in"),
            (_number(link.get("tx_mbps")), "out"),
        )
        if value is not None
    ]
    return " · ".join(parts)


def _share(value: float, floor: float, ceiling: float) -> float:
    """Where `value` sits between two ends, as 0-100 and never outside it."""
    if ceiling <= floor:
        return 0.0
    return max(0.0, min(100.0, (value - floor) / (ceiling - floor) * 100.0))


def _signal_lines(link: dict) -> list[tuple[str, str]]:
    signal = _number(link.get("signal_dbm"))
    if signal is None:
        return [(NO_SIGNAL_WORDS, PALETTE["warn"])]

    if signal >= SIGNAL_HEALTHY_DBM:
        words, colour, meaning = "healthy", PALETTE["ok"], ""
    elif signal >= SIGNAL_MARGINAL_DBM:
        words, colour, meaning = (
            "workable",
            PALETTE["warn"],
            "Little margin left for rain, or for a mast that has moved.",
        )
    else:
        words, colour, meaning = (
            "marginal",
            PALETTE["alarm"],
            "Close to the noise: this is where the picture starts breaking up.",
        )
    # The far end's reading goes on this same line rather than on a heading of
    # its own. Both halves matter on a point-to-point link - it can be strong
    # one way and weak the other - but the panel is one column on a laptop and a
    # second "Signal:" block would cost more than the fact is worth.
    #
    # The COLOUR still follows this end's figure alone. `vmd/desktop/window.py`
    # colours the status chip from `signal_dbm` and says in as many words that
    # it must never disagree with this panel about the same reading; a far end
    # that governed the ink here would have the two arguing one line apart.
    remote = _number(link.get("remote_signal_dbm"))
    if remote is None:
        lines = [(f"Signal: {signal:.0f} dBm - {words}", colour)]
    else:
        lines = [
            (f"Signal: {signal:.0f} dBm here, {remote:.0f} dBm at the far end - {words}", colour)
        ]
    if meaning:
        lines.append((meaning, PALETTE["muted"]))
    if remote is not None and abs(signal - remote) >= ASYMMETRY_DB:
        lines.append(
            (
                f"The two ends do not hear each other equally - {abs(signal - remote):.0f} dB "
                "apart. That is usually one dish aimed better than the other, and the "
                "weaker direction is the one that fails first.",
                PALETTE["warn"],
            )
        )

    noise = _number(link.get("noise_dbm"))
    if noise is not None:
        lines.append(
            (
                f"{signal - noise:.0f} dB above the noise floor ({noise:.0f} dBm).",
                PALETTE["muted"],
            )
        )
    return lines


def _traffic_lines(link: dict) -> list[tuple[str, str]]:
    """What the link is carrying, led by the figure that says whether more fits.

    Airtime when the radio reports it, and throughput against capacity when it
    does not. Which of those two is the headline is not a presentation choice:
    a wireless link runs out of TIME, not of bits per second, and his radio was
    at 88% of its airtime while this panel said "3.1 Mb/s of 24 Mb/s (13%)" - a
    link with room to spare, about a link that had none.
    """
    airtime = _number(link.get("airtime_percent"))
    if airtime is not None:
        return _airtime_lines(link, airtime)
    return _capacity_share_lines(link)


def _airtime_lines(link: dict, airtime: float) -> list[tuple[str, str]]:
    """The airtime, the traffic in it, and the capacity estimate put in its place.

    The order is the argument. Airtime first because it is the true reading;
    what is being carried second, with what the airtime says the whole link is
    worth; and airMAX's capacity estimate last and grey, because 194 Mb/s beside
    88% airtime is not a second opinion, it is a number from a different
    question - what the modulation rate would allow, not what this link is
    doing.
    """
    if airtime >= AIRTIME_FULL_PERCENT:
        words, colour = "the link is full", PALETTE["alarm"]
        meaning = (
            "Nothing else will fit on it. A picture that stutters, falls behind, or "
            "drops during a pan is this, not the camera and not the console."
        )
    elif airtime >= AIRTIME_BUSY_PERCENT:
        words, colour = "little room left", PALETTE["warn"]
        meaning = (
            "There is no room for a burst - a key frame, or a pan - and latency builds "
            "from here."
        )
    else:
        words, colour, meaning = "room to spare", PALETTE["ink"], ""

    lines = [(f"Airtime: {airtime:.0f}% used - {words}", colour)]
    if meaning:
        lines.append((meaning, colour))

    # Whose airtime this is, when it is not one camera's.
    #
    # "The VMD shows FULL on the ubiquiti capacity while on the airOS it's far
    # from reality. The FLIR sends 2.5 Mbps and multiply it by 2 because there
    # are 2 cameras." The figure was right and the sentence around it was not:
    # airtime is a property of the medium, so it already counts every camera on
    # the radio, and a panel that says "the link is full" beside one camera's
    # picture reads as an accusation against that camera. Said before the split
    # below, because it is what the split is a split OF.
    cameras = link.get("cameras")
    if isinstance(cameras, int) and cameras > 1:
        lines.append(
            (
                f"That is the whole radio link, with all {cameras} cameras on it - "
                f"not this camera alone.",
                PALETTE["muted"],
            )
        )

    coming, going = _number(link.get("rx_airtime_percent")), _number(
        link.get("tx_airtime_percent")
    )
    if coming is not None and going is not None:
        lines.append(
            (f"{coming:.0f}% of it coming in, {going:.0f}% going out.", PALETTE["muted"])
        )

    rate = _number(link.get("rx_mbps")), _number(link.get("tx_mbps"))
    carried = [
        f"{value:.1f} Mb/s {way}"
        for value, way in zip(rate, ("in", "out"))
        if value is not None
    ]
    if carried:
        said = "Carrying " + ", ".join(carried) + "."
        total = sum(value for value in rate if value is not None)
        # What the whole link is worth, from two figures the radio measured
        # rather than from one it estimated - and the answer to "will another
        # stream fit". Only worked out when the link is busy enough for the
        # arithmetic to mean anything: at low airtime the modulation would
        # change under a second stream anyway, and the number would be a guess
        # wearing the clothes of a measurement.
        if airtime >= AIRTIME_BUSY_PERCENT and total > 0:
            said += (
                f" That costs {airtime:.0f}% of the airtime, so about "
                f"{total / airtime * 100.0:.0f} Mb/s is the whole of this link."
            )
        lines.append((said, PALETTE["ink"]))

    estimate = [
        f"{value:.0f} Mb/s {way}"
        for value, way in (
            (_number(link.get("rx_capacity_mbps")), "in"),
            (_number(link.get("tx_capacity_mbps")), "out"),
        )
        if value
    ]
    if estimate:
        lines.append(
            (
                "The radio's own estimate is " + " and ".join(estimate) + ", worked out "
                "from the signal rather than measured. The airtime above is what the "
                "link is actually doing.",
                PALETTE["muted"],
            )
        )
    return lines


def _capacity_share_lines(link: dict) -> list[tuple[str, str]]:
    """Throughput against capacity, for a radio that reports no airtime.

    Weaker than the airtime view and kept because it is all such a radio can
    say: "4.2 Mb/s" on its own is a number and "4.2 of 18" is at least an
    argument. What it cannot see is the modulation rate falling underneath it,
    which is why it is no longer the headline anywhere the airtime exists.
    """
    lines: list[tuple[str, str]] = []
    busiest = 0.0
    for key, capacity_key, words in (
        ("rx_mbps", "rx_capacity_mbps", "Coming in"),
        ("tx_mbps", "tx_capacity_mbps", "Going out"),
    ):
        rate = _number(link.get(key))
        if rate is None:
            continue
        capacity = _number(link.get(capacity_key))
        if capacity and capacity > 0:
            share = rate / capacity
            busiest = max(busiest, share)
            colour = PALETTE["ink"]
            if share >= FULL_FRACTION:
                colour = PALETTE["alarm"]
            elif share >= BUSY_FRACTION:
                colour = PALETTE["warn"]
            lines.append(
                (
                    f"{words}: {rate:.1f} Mb/s of {capacity:.0f} Mb/s "
                    f"({share * 100:.0f}%)",
                    colour,
                )
            )
        else:
            lines.append(
                (
                    f"{words}: {rate:.1f} Mb/s, and this radio did not report a "
                    "capacity to compare it against.",
                    PALETTE["ink"],
                )
            )
    if busiest >= FULL_FRACTION:
        lines.append(
            (
                "The link is full. A picture that stutters, falls behind, or drops "
                "during a pan is this, not the camera and not the console.",
                PALETTE["alarm"],
            )
        )
    elif busiest >= BUSY_FRACTION:
        lines.append(
            (
                "The link is close to full: there is no room left for a burst, and "
                "latency builds from here.",
                PALETTE["warn"],
            )
        )
    return lines


def _detail_lines(link: dict) -> list[tuple[str, str]]:
    """Link quality and what the radio calls itself. Omitted when unknown.

    `parse_status` is deliberately defensive: a field it could not find comes
    back as None rather than as zero, because a console reporting 0 dBm because
    it could not find the field is worse than one reporting nothing. Undoing
    that here would put the lie back on the screen.

    There is no distance here any more, and that is the same rule. The panel
    used to print `wireless.distance` as metres; on the radio that was finally
    read, that field is 0 and the station's own is 1, on a path that is really
    15 km. Neither is metres, nothing here knows what they are, and "Distance:
    1 km" is worse than nothing because somebody would believe it.
    """
    lines: list[tuple[str, str]] = []

    quality = _number(link.get("quality_percent"))
    if quality is not None:
        # Already a percentage whichever field it came from: airMAX's linkscores
        # are 0-100 and ccq is 0-1000, and the parser is where that is known.
        lines.append(
            (
                f"Link quality: {quality:.0f}%",
                PALETTE["warn"] if quality < QUALITY_POOR_PERCENT else PALETTE["ink"],
            )
        )

    device = str(link.get("device") or "")
    uptime = _number(link.get("uptime_s"))
    if device and uptime:
        lines.append((f"{device}, up for {_age(uptime)}", PALETTE["muted"]))
    elif device:
        lines.append((device, PALETTE["muted"]))
    elif uptime:
        lines.append((f"Up for {_age(uptime)}", PALETTE["muted"]))
    return lines


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sentence(reason) -> str:
    """A reason from the radio, as a sentence rather than a fragment."""
    text = str(reason or "").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


def _age(seconds: float) -> str:
    """A span in the largest unit that keeps it readable, said properly.

    The count is rounded first and the word chosen from what it rounded to, not
    from the unrounded figure - `1 seconds` is what the uptime of a radio that
    has just rebooted read as, and that is the moment somebody is reading this
    line rather than glancing at it: a one-second uptime is the whole of the
    answer to "why did the picture stop". A sentence that looks unfinished there
    reads as a console that is broken rather than a radio that restarted.
    """
    for limit, size, word in (
        (90.0, 1.0, "second"),
        (5400.0, 60.0, "minute"),
        (172800.0, 3600.0, "hour"),
    ):
        if seconds < limit:
            return _plural(seconds / size, word)
    return _plural(seconds / 86400.0, "day")


def _plural(count: float, word: str) -> str:
    whole = round(count)
    return f"{whole:.0f} {word}" + ("" if whole == 1 else "s")




class LinkPanel(QGroupBox):
    """The link, as one word and two bars, with the sentences a click away.

    Built around whatever the console's `RadioService` is; it reads the answer
    that service already has and never asks the radio anything itself.

    The panel used to be those sentences and nothing else - fourteen of them at
    the worst, every one true, and the operator's verdict was that it was too
    much text to be read by anybody who is not already an engineer. So the
    reading now arrives in three layers, and which layer somebody is in is his
    choice rather than this file's:

    * **the word**, which is the whole link in one - GOOD, BUSY, FULL, WEAK -
      and is meant to be read from across the room without stopping;
    * **the two bars**, signal and how much of the link is spent, which say how
      far from trouble each of them is without anybody knowing what a dBm is;
    * **the sentences**, unchanged, behind Details - because the reasoning that
      put "FULL" on the screen still has to be reachable by whoever is helping
      him over the phone.

    Nothing was deleted to make it shorter. `link_lines` still produces every
    sentence it did, and the same thresholds decide the word above them, so the
    two can never disagree about the same radio.
    """

    def __init__(self, radio, parent: QWidget | None = None) -> None:
        super().__init__("Link", parent)
        self._radio = radio
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(SPACE_HAIR)
        # The panel asks for as much height as its wrapped sentences need at the
        # width the column gives it. Without this the column hands it the height
        # of one line per sentence and the second line of each is drawn over the
        # line beneath - which on this panel means the sentence saying the link
        # is full is the one that gets cut in half.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self.setSizePolicy(policy)

        # ------------------------------------------------------------ the word
        header = QHBoxLayout()
        header.setSpacing(SPACE_SNUG)
        self._glyph = QLabel("")
        self._headline = QLabel("")
        header.addWidget(self._glyph)
        header.addWidget(self._headline)
        header.addStretch(1)
        self._layout.addLayout(header)

        self._note = self._sentence_label(SIZE_SMALL)
        self._layout.addWidget(self._note)
        self._layout.addSpacing(SPACE_TIGHT)

        # ------------------------------------------------------------ the bars
        self._signal = Meter("Signal")
        self._signal.set_marks(
            [
                _share(SIGNAL_MARGINAL_DBM, SIGNAL_FLOOR_DBM, SIGNAL_CEILING_DBM),
                _share(SIGNAL_HEALTHY_DBM, SIGNAL_FLOOR_DBM, SIGNAL_CEILING_DBM),
            ]
        )
        self._use = Meter("Link in use")
        self._layout.addWidget(self._signal)
        self._layout.addSpacing(SPACE_TIGHT)
        self._layout.addWidget(self._use)
        self._layout.addSpacing(SPACE_TIGHT)

        self._carrying = self._sentence_label(SIZE_SMALL)
        self._layout.addWidget(self._carrying)

        # ------------------------------------------------------- the sentences
        #
        # Shut by default and it stays shut: this is the panel somebody stands
        # in front of for months, and a detail pane that reopened itself every
        # time the console restarted would be the paragraph coming back.
        self._details = QToolButton()
        self._details.setCheckable(True)
        self._details.setChecked(False)
        self._details.setCursor(Qt.CursorShape.PointingHandCursor)
        self._details.setStyleSheet(
            "QToolButton { border: 0; background: transparent; padding: 0;"
            f" color: {PALETTE['muted']}; font-size: {SIZE_HEADING}px; }}"
            f" QToolButton:hover {{ color: {PALETTE['ink']}; }}"
        )
        self._details.toggled.connect(self._show_details)
        self._layout.addWidget(self._details, 0, Qt.AlignmentFlag.AlignLeft)

        self._labels: list[QLabel] = []
        self._shown: list[tuple[str, str]] = []
        self._summary: dict = {}
        # The mark, and the state behind it. It is a label of its own rather
        # than another entry in `lines`, and that is deliberate twice over: the
        # lines are what the radio said, and this is whether it is still saying
        # anything; and the lines are only redrawn when they change, which is
        # the one thing an indicator of liveness must not wait for.
        self._pulse = self._sentence_label(SIZE_HEADING)
        self._layout.addWidget(self._pulse)
        # A sentinel, not None: a service that never reports a count at all must
        # still move the mark once, on its first reading, rather than never.
        self._counted: object = object()
        self._beat = 0
        self._pulse_state: tuple[str, str, str] = ("", "", "")
        # How many times the panel has actually been redrawn. This runs every
        # two seconds for months; the number says whether it is doing work for
        # nothing.
        self.rebuilds = 0
        self._show_details(False)
        self.refresh()

    # Exposed so the tests name the same two shapes the panel draws, rather
    # than a copy of them that can drift.
    ARRIVING = ARRIVING_GLYPHS

    @staticmethod
    def _sentence_label(size: int) -> QLabel:
        label = QLabel("")
        label.setWordWrap(True)
        # A word-wrapped QLabel asks for the height of ONE line unless its size
        # policy says the height depends on the width, and a layout that
        # believes it draws the second line over the line beneath it. The column
        # is 340 px wide and several of these sentences do not fit in it, so
        # without this the operator reads half of them.
        policy = label.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        label.setSizePolicy(policy)
        label.setStyleSheet(f"font-size: {size}px;")
        return label

    def lines(self) -> list[tuple[str, str]]:
        """What the sentences say, for the window and for the tests.

        Produced whether or not Details is open: the panel decides what to draw,
        not what is true, and a test that had to click a button to find out what
        the radio said would be testing the button.
        """
        return list(self._shown)

    def summary(self) -> dict:
        """The word, the bars and the short line - what is on screen at a glance."""
        return dict(self._summary)

    def details_open(self) -> bool:
        return self._details.isChecked()

    def show_details(self, opened: bool) -> None:
        """Open or shut the sentences. For the window's shortcuts and the tests."""
        self._details.setChecked(bool(opened))

    def meters(self) -> tuple[Meter, Meter]:
        """The signal bar and the in-use bar."""
        return self._signal, self._use

    def pulse(self) -> tuple[str, str, str]:
        """The mark, its words and its colour: glyph, sentence, colour.

        Empty glyph means there is nothing to say about readings at all - no
        radio has been set up, or none has ever answered and the panel is
        already saying so in as many words. Those are two different states from
        "it was answering and has stopped", which is the whole point: a hard
        failure that looks like still-checking is how the one fault at the far
        end of this link went unnoticed for months.
        """
        return self._pulse_state

    def clipped(self) -> list[str]:
        """Any sentence the panel is not tall enough to show in full.

        Word wrapping is the one way this panel can lose half a sentence without
        anything going wrong: a QLabel's own idea of how tall it should be is a
        guess, and where the guess is short the layout draws the rest of the
        sentence over the line beneath it. The sentence that says the link is
        full is three lines long and is exactly the one that would be cut.
        """
        cut: list[str] = []
        room = self._layout.contentsRect()
        everything = [self._note, self._carrying, *self._labels, self._pulse]
        shown = [label for label in everything if label.isVisibleTo(self) and label.text()]
        for index, label in enumerate(shown):
            # Three ways to lose a line, and the third is the one that actually
            # happened: the label is too short for its own wrapped text; it runs
            # off the bottom of the panel; or the panel was given less height
            # than it asked for, and the layout has laid the next sentence over
            # the tail of this one.
            box = label.geometry()
            after = shown[index + 1].geometry() if index + 1 < len(shown) else None
            if (
                label.height() < label.heightForWidth(max(label.width(), 1))
                or box.bottom() > room.bottom() + 1
                or (after is not None and box.bottom() >= after.top())
            ):
                cut.append(label.text())
        return cut

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._fit()

    def _show_details(self, opened: bool) -> None:
        self._details.setText(("▾ Less" if opened else "▸ Details"))
        for index, label in enumerate(self._labels):
            label.setVisible(bool(opened) and index < len(self._shown))
        # The note under the word goes away while the sentences are up, because
        # it IS one of them, shortened. Opened, the panel said "Nothing else
        # fits - the picture can stutter or drop during a pan", then "Airtime:
        # 88% used - the link is full", then "Nothing else will fit on it. A
        # picture that stutters, falls behind, or drops during a pan is this" -
        # the same fact three times, in a panel he asked to have less text in.
        #
        # The sentences keep their full wording rather than the note keeping
        # its: they are what somebody reads out over the phone, and they have to
        # stand up without the word above them.
        self._note.setVisible(bool(self._note.text()) and not opened)
        self._fit()

    def _fit(self) -> None:
        """Give every label the height its wrapped text actually needs.

        The width is the layout's, once there is one. Before the panel has ever
        been laid out that is nothing useful, so the panel's own width less its
        borders stands in until the first resize corrects it.
        """
        width = max(self._layout.contentsRect().width(), self.width() - 24, 1)
        for label in (self._note, self._carrying, *self._labels, self._pulse):
            if label.text() and label.isVisibleTo(self):
                label.setMinimumHeight(label.heightForWidth(width))
            else:
                label.setMinimumHeight(0)

    def _advance(self, link: dict) -> None:
        """Move the mark on, if a reading has landed since the last redraw.

        Nothing here reads a clock. The mark advances on the count the service
        publishes and on nothing else, so it says "a reading arrived", never
        "the console repainted" - and when the readings stop, so does it.
        """
        age = link.get("age_seconds")
        known = isinstance(age, (int, float))
        if link.get("checking") or not known:
            # Nobody has answered yet, or there is no radio to answer. Neither
            # is a stopped reading and neither may be drawn as one; the panel is
            # already saying which of the two it is, in words.
            self._pulse_state = ("", "", "")
            return
        if not link.get("connected") or age >= STALE_AFTER_SECONDS:
            # It is still being asked every beat. What has stopped arriving is
            # answers, and this mark is about answers.
            self._pulse_state = (STOPPED_GLYPH, STOPPED_WORDS, PALETTE["warn"])
            return
        counted = link.get("readings")
        if counted != self._counted:
            self._counted = counted
            self._beat = (self._beat + 1) % len(ARRIVING_GLYPHS)
        self._pulse_state = (
            ARRIVING_GLYPHS[self._beat],
            ARRIVING_WORDS,
            PALETTE["muted"],
        )

    def refresh(self) -> None:
        try:
            link = self._radio.status()
        except Exception:  # noqa: BLE001 - the pictures are not downstream of this
            logger.exception("the radio could not be asked about the link")
            link = {}
            lines = [("The radio could not be asked about the link.", PALETTE["alarm"])]
            summary = {
                **link_summary({}),
                "state": "alarm",
                "headline": HEADLINE_NO_LINK,
                "note": "The radio could not be asked about the link.",
            }
        else:
            link = link if isinstance(link, dict) else {}
            lines = link_lines(link)
            summary = link_summary(link)

        # Before the early return below, and that is the point of it being here:
        # the lines are redrawn only when they change, and a mark that waited
        # for something else to change would be a mark that never moves on a
        # link that is behaving itself.
        self._advance(link)
        glyph, words, colour = self._pulse_state
        self._pulse.setText(f"{glyph} {words}" if glyph else "")
        self._pulse.setStyleSheet(
            f"color: {colour}; font-size: {SIZE_HEADING}px;" if colour else ""
        )
        self._pulse.setVisible(bool(glyph))

        # The bars take every reading, including one identical to the last: the
        # meter itself decides whether that is a journey or a no-op, and it is
        # the only thing that knows where its own fill currently is.
        self._signal.set_reading(
            summary["signal"], summary["signal_caption"], state_colour(summary["signal_state"])
        )
        self._use.set_reading(
            summary["use"], summary["use_caption"], state_colour(summary["use_state"])
        )
        if summary["use_marks"] != self._summary.get("use_marks"):
            self._use.set_marks(summary["use_marks"])

        if lines == self._shown and summary == self._summary:
            return
        self._summary = summary
        self._shown = lines
        self.rebuilds += 1

        word_colour = state_colour(summary["state"])
        self._glyph.setText(state_glyph(summary["state"]))
        self._glyph.setStyleSheet(f"color: {word_colour}; font-size: {SIZE_BAND}px;")
        self._headline.setText(summary["headline"])
        self._headline.setStyleSheet(
            f"color: {word_colour}; font-size: {SIZE_BAND}px; font-weight: {WEIGHT_VALUE};"
        )
        self._note.setText(summary["note"])
        # Hidden while the sentences are up: see `_show_details`. Set through
        # that rather than here so there is one rule about it and not two.
        self._note.setVisible(bool(summary["note"]) and not self._details.isChecked())
        self._note.setStyleSheet(f"color: {word_colour}; font-size: {SIZE_SMALL}px;")
        self._carrying.setText(summary["carrying"])
        self._carrying.setVisible(bool(summary["carrying"]))
        self._carrying.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )

        while len(self._labels) < len(lines):
            label = self._sentence_label(SIZE_SMALL)
            # Before the mark, which stays at the bottom: it is the panel's
            # footer, and a line about the link appearing underneath it would
            # read as something the mark was about.
            self._layout.insertWidget(self._layout.indexOf(self._pulse), label)
            self._labels.append(label)
        for index, label in enumerate(self._labels):
            if index < len(lines):
                text, colour = lines[index]
                label.setText(text)
                label.setStyleSheet(f"color: {colour}; font-size: {SIZE_SMALL}px;")
            else:
                label.setText("")
        self._details.setVisible(bool(lines))
        self._show_details(self._details.isChecked())
