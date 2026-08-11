"""The link, in the Live tab's side column, in words an operator can act on.

The radio was read and then thrown away. `LinkStatus` carries signal, noise,
CCQ, throughput, capacity, distance, uptime and the device's name; the console
showed one of them, `signal_dbm`, in the status bar, and the design's side
column - "steering, zoom, link, storage, recent movement" - had no link in it at
all.

That mattered more here than it would elsewhere. **The link is the bottleneck of
this entire system**: one camera at the far end of a Ubiquiti point-to-point hop
of more than 15 km carrying about 5 Mb/s, and every bandwidth problem this
project has had - 20-40 s of latency, streams dropping during pans, video
stuttering - was a link problem. The figure that explains those is throughput
against the negotiated capacity, and nothing on screen showed it.

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

from PySide6.QtWidgets import QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget

from vmd.desktop.style import PALETTE

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
BUSY_FRACTION = 0.7
FULL_FRACTION = 0.9

# Below this the airOS link quality figure is saying the link is spending its
# time on retries rather than on data.
CCQ_POOR_PERCENT = 80.0

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
    lines = [(f"Signal: {signal:.0f} dBm - {words}", colour)]
    if meaning:
        lines.append((meaning, PALETTE["muted"]))

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
    """Throughput against capacity: the view that explains the video problems.

    About 5 Mb/s of link and a 4K stream will not fit in it. Shown as a share of
    what the radio says it negotiated, because "4.2 Mb/s" on its own is a number
    and "4.2 of 5" is a diagnosis.
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
    """CCQ, distance, and what the radio calls itself. Omitted when unknown.

    `parse_status` is deliberately defensive: a field it could not find comes
    back as None rather than as zero, because a console reporting 0 dBm because
    it could not find the field is worse than one reporting nothing. Undoing
    that here would put the lie back on the screen.
    """
    lines: list[tuple[str, str]] = []

    ccq = _number(link.get("ccq"))
    if ccq is not None:
        # airOS reports this on a 0-1000 scale in the builds this was written
        # against; "985%" is not something anyone can read. UNPROVEN on a real
        # device - probe_radio.py prints the raw figure beside this one.
        percent = ccq / 10.0 if ccq > 100 else ccq
        lines.append(
            (
                f"Link quality: {percent:.0f}%",
                PALETTE["warn"] if percent < CCQ_POOR_PERCENT else PALETTE["ink"],
            )
        )

    distance = _number(link.get("distance_m"))
    if distance:
        lines.append((f"Distance: {distance / 1000.0:.1f} km", PALETTE["muted"]))

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
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60.0:.0f} minutes"
    if seconds < 172800:
        return f"{seconds / 3600.0:.0f} hours"
    return f"{seconds / 86400.0:.0f} days"


class LinkPanel(QGroupBox):
    """The link lines in the Live tab's side column.

    Built around whatever the console's `RadioService` is; it reads the answer
    that service already has and never asks the radio anything itself.
    """

    def __init__(self, radio, parent: QWidget | None = None) -> None:
        super().__init__("Link", parent)
        self._radio = radio
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(2)
        # The panel asks for as much height as its wrapped sentences need at the
        # width the column gives it. Without this the column hands it the height
        # of one line per sentence and the second line of each is drawn over the
        # line beneath - which on this panel means the sentence saying the link
        # is full is the one that gets cut in half.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self.setSizePolicy(policy)
        self._labels: list[QLabel] = []
        self._shown: list[tuple[str, str]] = []
        # The mark, and the state behind it. It is a label of its own rather
        # than another entry in `lines`, and that is deliberate twice over: the
        # lines are what the radio said, and this is whether it is still saying
        # anything; and the lines are only redrawn when they change, which is
        # the one thing an indicator of liveness must not wait for.
        self._pulse = QLabel("")
        self._pulse.setWordWrap(True)
        policy = self._pulse.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        self._pulse.setSizePolicy(policy)
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
        self.refresh()

    # Exposed so the tests name the same two shapes the panel draws, rather
    # than a copy of them that can drift.
    ARRIVING = ARRIVING_GLYPHS

    def lines(self) -> list[tuple[str, str]]:
        """What is on screen, for the window and for the tests."""
        return list(self._shown)

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
        everything = [*self._labels, self._pulse]
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

    def _fit(self) -> None:
        """Give every label the height its wrapped text actually needs.

        The width is the layout's, once there is one. Before the panel has ever
        been laid out that is nothing useful, so the panel's own width less its
        borders stands in until the first resize corrects it.
        """
        width = max(self._layout.contentsRect().width(), self.width() - 24, 1)
        for label in (*self._labels, self._pulse):
            if label.text():
                label.setMinimumHeight(label.heightForWidth(width))

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
        else:
            link = link if isinstance(link, dict) else {}
            lines = link_lines(link)

        # Before the early return below, and that is the point of it being here:
        # the lines are redrawn only when they change, and a mark that waited
        # for something else to change would be a mark that never moves on a
        # link that is behaving itself.
        self._advance(link)
        glyph, words, colour = self._pulse_state
        self._pulse.setText(f"{glyph} {words}" if glyph else "")
        self._pulse.setStyleSheet(f"color: {colour};" if colour else "")
        self._pulse.setVisible(bool(glyph))

        if lines == self._shown:
            return
        self._shown = lines
        self.rebuilds += 1
        while len(self._labels) < len(lines):
            label = QLabel("")
            label.setWordWrap(True)
            # A word-wrapped QLabel asks for the height of ONE line unless its
            # size policy says the height depends on the width, and a layout
            # that believes it draws the second line over the line beneath it.
            # The column is 340 px wide and several of these sentences do not
            # fit in it, so without this the operator reads half of them.
            policy = label.sizePolicy()
            policy.setHeightForWidth(True)
            policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
            label.setSizePolicy(policy)
            # Before the mark, which stays at the bottom: it is the panel's
            # footer, and a line about the link appearing underneath it would
            # read as something the mark was about.
            self._layout.insertWidget(self._layout.indexOf(self._pulse), label)
            self._labels.append(label)
        for index, label in enumerate(self._labels):
            if index < len(lines):
                text, colour = lines[index]
                label.setText(text)
                label.setStyleSheet(f"color: {colour};")
                label.setVisible(True)
            else:
                label.setText("")
                label.setVisible(False)
        self._fit()
