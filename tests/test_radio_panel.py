"""The link panel: the one view that makes the link's problems visible.

Every bandwidth problem this system has had was a link problem - 20-40 s of
latency, streams dropping during pans, video stuttering - and the console read
nine figures off the radio and showed exactly one of them, in the status bar.
This is the rest of them, in the Live tab's side column beside storage.

Two things are being tested here and neither is decoration:

* the numbers mean something to an operator who does not know that -65 dBm is
  healthy and -85 dBm is marginal, and that a link running at capacity is why
  the picture stutters;
* nothing here waits for the radio. An unreachable one costs about 12 s, and the
  panel is redrawn on the window's two-second heartbeat.
"""

from __future__ import annotations

import threading
import time

import pytest

from vmd.desktop.style import PALETTE
from vmd.radio.airos import RadioError
from vmd.radio.panel import STALE_AFTER_SECONDS, LinkPanel, link_lines
from vmd.settings import Settings


def reading(**kwargs) -> dict:
    """A radio service answer, in the shape RadioService.status() returns."""
    base = {
        "connected": True,
        "reason": "",
        "signal_dbm": -63,
        "noise_dbm": -96,
        "ccq": 985.0,
        "tx_mbps": 0.512,
        "rx_mbps": 4.2,
        "tx_capacity_mbps": 24.0,
        "rx_capacity_mbps": 18.0,
        "distance_m": 15400,
        "uptime_s": 84231,
        "device": "LOCO-north",
        "age_seconds": 1.0,
    }
    base.update(kwargs)
    return base


def texts(lines: list[tuple[str, str]]) -> str:
    return " | ".join(text for text, _ in lines).lower()


def coloured(lines: list[tuple[str, str]], colour: str) -> list[str]:
    return [text for text, own in lines if own == colour]


# ------------------------------------------------------------- the four states


def test_a_radio_that_was_never_set_up_says_so_rather_than_showing_dashes() -> None:
    lines = link_lines({"connected": False, "reason": "the radio is not set up"})
    assert lines
    assert "not set up" in texts(lines)
    assert not coloured(lines, PALETTE["alarm"]), "an empty setting is not a fault"


def test_a_radio_that_has_not_answered_yet_says_it_is_being_asked() -> None:
    """Different from "the link has nothing to report", which is what this
    console says when the link is fine."""
    lines = link_lines({"connected": False, "checking": True, "reason": "checking the radio"})
    assert "checking" in texts(lines)


def test_a_current_reading_is_shown_without_an_age() -> None:
    lines = link_lines(reading(age_seconds=1.0))
    assert "-63 dBm" in " ".join(text for text, _ in lines)
    assert "ago" not in texts(lines) and "old" not in texts(lines)


def test_a_stale_reading_is_never_presented_as_the_state_of_the_link_now() -> None:
    """An operator who reads a signal figure believes the link was up when they
    read it. A reading from four minutes ago says nothing about now."""
    lines = link_lines(reading(age_seconds=STALE_AFTER_SECONDS + 200))
    aged = [text for text, _ in lines if "ago" in text.lower() or "old" in text.lower()]
    assert aged, "a stale reading must carry its age"
    assert not coloured(lines, PALETTE["ok"]), "nothing stale may read as healthy"


def test_a_radio_that_could_not_be_read_shows_the_reason_it_gave() -> None:
    lines = link_lines(
        {"connected": False, "reason": "cannot reach 10.0.0.9", "age_seconds": 2.0}
    )
    assert "cannot reach 10.0.0.9" in texts(lines)
    assert coloured(lines, PALETTE["alarm"])


# ------------------------------------------------------------------ the signal


def test_a_healthy_signal_is_called_healthy_and_drawn_as_ok() -> None:
    lines = link_lines(reading(signal_dbm=-58))
    signal = [(t, c) for t, c in lines if t.startswith("Signal")]
    assert signal and signal[0][1] == PALETTE["ok"]
    assert "-58 dBm" in signal[0][0]
    assert signal[0][0] != "Signal: -58 dBm", "a number alone is not something to act on"


def test_a_signal_with_little_margin_left_is_a_warning_not_a_number() -> None:
    lines = link_lines(reading(signal_dbm=-74))
    signal = [(t, c) for t, c in lines if t.startswith("Signal")]
    assert signal and signal[0][1] == PALETTE["warn"], signal


def test_a_marginal_signal_is_an_alarm() -> None:
    lines = link_lines(reading(signal_dbm=-85))
    signal = [(t, c) for t, c in lines if t.startswith("Signal")]
    assert signal and signal[0][1] == PALETTE["alarm"], signal
    assert "marginal" in signal[0][0].lower()


def test_the_noise_floor_is_shown_as_a_margin_not_only_as_a_number() -> None:
    lines = link_lines(reading(signal_dbm=-63, noise_dbm=-96))
    assert "33 dB" in " ".join(text for text, _ in lines)


def test_a_radio_that_reports_no_signal_points_at_the_probe() -> None:
    """The parser has never met a real radio. If the signal is not where it
    looks, the panel must say what to run rather than showing a dash."""
    lines = link_lines(reading(signal_dbm=None, noise_dbm=None))
    assert "probe_radio" in texts(lines)


# -------------------------------------------------------------- the throughput


def test_the_throughput_is_shown_against_the_capacity() -> None:
    """This is the view that explains the video problems: about 5 Mb/s of link,
    and a 4K stream that will not fit in it."""
    lines = link_lines(reading(rx_mbps=4.2, rx_capacity_mbps=18.0))
    carried = " ".join(text for text, _ in lines)
    assert "4.2" in carried and "18" in carried


def test_a_link_running_at_its_capacity_says_that_is_why_the_picture_stutters() -> None:
    lines = link_lines(reading(rx_mbps=17.5, rx_capacity_mbps=18.0))
    assert coloured(lines, PALETTE["alarm"])
    assert "stutter" in texts(lines) or "full" in texts(lines)


def test_a_quiet_link_is_not_shouted_about() -> None:
    lines = link_lines(reading(rx_mbps=1.0, tx_mbps=0.2))
    assert not coloured(lines, PALETTE["alarm"])
    assert not coloured(lines, PALETTE["warn"])


def test_throughput_without_a_capacity_is_still_shown_and_says_which_is_missing() -> None:
    lines = link_lines(reading(rx_capacity_mbps=None, tx_capacity_mbps=None))
    assert "4.2" in " ".join(text for text, _ in lines)
    assert "capacity" in texts(lines)


# --------------------------------------------------- what is not known is left out


def test_an_unknown_field_is_left_off_rather_than_shown_as_zero() -> None:
    """parse_status is deliberately defensive about this, and the panel must not
    undo it: a console reporting 0 dBm because it could not find the field is
    worse than one reporting nothing."""
    lines = link_lines(
        reading(ccq=None, distance_m=None, uptime_s=None, device="", tx_mbps=None, rx_mbps=None)
    )
    said = texts(lines)
    assert "0 %" not in said and "0%" not in said
    assert "0.0 mb/s" not in said
    assert "distance" not in said
    assert "up for" not in said


def test_the_things_that_are_known_are_all_shown() -> None:
    said = texts(link_lines(reading()))
    assert "quality" in said, "CCQ"
    assert "15.4 km" in said, "distance"
    assert "loco-north" in said, "the device's name"
    assert "up for" in said, "uptime"


def test_ccq_is_shown_as_a_percentage_whatever_scale_the_radio_used() -> None:
    """airOS reports CCQ on a 0-1000 scale in the builds this was written
    against, and "985%" is not something anyone can read."""
    assert "98" in texts(link_lines(reading(ccq=985.0)))
    assert "985" not in texts(link_lines(reading(ccq=985.0)))
    assert "94" in texts(link_lines(reading(ccq=94.0)))


# -------------------------------------------------------------------- the panel


class FakeRadioService:
    """A radio service with the cached shape, and no radio behind it."""

    def __init__(self, status: dict) -> None:
        self._status = status
        self.asked = 0

    def status(self) -> dict:
        self.asked += 1
        return dict(self._status)


def test_the_panel_shows_the_link_lines(qtbot) -> None:
    panel = LinkPanel(FakeRadioService(reading()))
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.lines(), "the link panel must say something"
    assert "-63 dBm" in " ".join(text for text, _ in panel.lines())


def test_the_panel_says_something_before_anything_has_been_read(qtbot) -> None:
    panel = LinkPanel(FakeRadioService({"connected": False, "checking": True}))
    qtbot.addWidget(panel)
    assert panel.lines(), "a blank panel is the failure this exists to remove"


def test_a_radio_that_throws_costs_the_panel_and_not_the_window(qtbot) -> None:
    class Angry:
        def status(self) -> dict:
            raise OSError("the radio reader is gone")

    panel = LinkPanel(Angry())
    qtbot.addWidget(panel)
    panel.refresh()  # must not raise
    assert panel.lines()


def test_the_panel_never_waits_for_the_radio(qtbot) -> None:
    """The regression this must not reintroduce: the reading was taken on the
    thread that draws the window, and an unreachable radio costs about 12 s of
    login timeouts. Bounded independently of the service, so a regression fails
    rather than hangs."""
    from vmd.background import BackgroundValue
    from vmd.radio.service import CACHE_SECONDS, RadioService, _reader

    class Wedged:
        def __init__(self) -> None:
            self.released = threading.Event()

        def status(self):
            self.released.wait(5.0)
            raise RadioError("cannot reach 10.0.0.9")

    wedged = Wedged()
    service = RadioService(Settings())
    service.radio = wedged
    service._reading = BackgroundValue(
        read=_reader(wedged), stale_after=CACHE_SECONDS, name="a wedged radio"
    )
    panel = LinkPanel(service)
    qtbot.addWidget(panel)
    try:
        started = time.monotonic()
        for _ in range(20):  # forty seconds of heartbeats
            panel.refresh()
        elapsed = time.monotonic() - started
    finally:
        wedged.released.set()
        service.close()
    assert elapsed < 0.5, f"twenty redraws cost {elapsed:.2f} s on a wedged radio"


def test_the_panel_does_not_rebuild_itself_when_nothing_changed(qtbot) -> None:
    """It is redrawn every two seconds for months."""
    service = FakeRadioService(reading())
    panel = LinkPanel(service)
    qtbot.addWidget(panel)
    panel.refresh()
    before = panel.rebuilds
    for _ in range(5):
        panel.refresh()
    assert panel.rebuilds == before


@pytest.mark.parametrize("age", [0.0, STALE_AFTER_SECONDS, 3600.0])
def test_every_age_produces_a_line_and_never_an_exception(age: float) -> None:
    assert link_lines(reading(age_seconds=age))


def test_no_sentence_is_cut_in_half_by_the_column_it_sits_in(qtbot) -> None:
    """The column is 340 px wide and several of these sentences do not fit in
    one line of it. A word-wrapped label whose height was guessed too short
    draws its second line over the line beneath it - and the sentence that would
    be cut is the three-line one saying the link is full, which is the whole
    reason this panel exists."""
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    busy = reading(signal_dbm=-84, rx_mbps=17.6, rx_capacity_mbps=18.0, ccq=612.0)
    column = QWidget()
    qtbot.addWidget(column)
    column.setFixedWidth(340)
    layout = QVBoxLayout(column)
    panel = LinkPanel(FakeRadioService(busy))
    layout.addWidget(panel)
    # A widget below it that will take every pixel it is allowed to, as the
    # movement list does in the real column: that is what leaves the panel with
    # exactly the height it asked for and no more.
    filler = QWidget()
    layout.addWidget(filler, 1)
    column.resize(340, 860)
    column.show()
    QApplication.processEvents()
    assert panel.clipped() == [], "half a sentence is worse than none"


# ------------------------------------------- showing that it is actually reading
#
# The operator's own words: "I want the numbers from the signal to be
# automatically updated realtime - I want to see that it's actually capturing
# them." Two requirements, and the second is the one that matters. A figure that
# is correct but never visibly moves is indistinguishable from a figure that is
# frozen, and this system has spent a whole day teaching him - correctly - not
# to believe a console that looks calm.
#
# The age cannot be that indicator. The panel is redrawn on the same two-second
# heartbeat that takes the reading, so the age at the moment of drawing is
# always about two seconds, whatever the interval is: it would sit at "2 s ago"
# for ever and look exactly like the frozen number he is complaining about. So
# what moves is a mark that advances once per reading that actually landed.


def test_the_panel_shows_a_mark_saying_readings_are_arriving(qtbot) -> None:
    panel = LinkPanel(FakeRadioService(reading(readings=4)))
    qtbot.addWidget(panel)
    panel.refresh()
    glyph, words, _colour = panel.pulse()
    assert glyph, "nothing on the panel says whether it is still reading"
    assert words


def test_the_mark_advances_only_when_a_new_reading_lands(qtbot) -> None:
    """A mark that changed on every redraw would be a decoration that says
    nothing: the console redraws whether or not the radio answered."""
    service = FakeRadioService(reading(readings=4))
    panel = LinkPanel(service)
    qtbot.addWidget(panel)
    panel.refresh()
    settled = panel.pulse()[0]
    for _ in range(3):
        panel.refresh()
    assert panel.pulse()[0] == settled, "the mark moved without a reading behind it"

    service._status = reading(readings=5)
    panel.refresh()
    assert panel.pulse()[0] != settled, "a reading landed and nothing on screen moved"


def test_the_mark_stops_when_the_readings_stop(qtbot) -> None:
    """The recording dot's rule: two shapes, and what tells them apart is that
    one of them moves. A console that has stopped reading the radio may not go
    on looking like one that is reading it."""
    panel = LinkPanel(FakeRadioService(reading(age_seconds=STALE_AFTER_SECONDS + 200)))
    qtbot.addWidget(panel)
    panel.refresh()
    glyph, words, colour = panel.pulse()
    assert glyph and glyph != panel.ARRIVING[0] and glyph != panel.ARRIVING[1]
    assert "no" in words.lower()
    assert colour == PALETTE["warn"]


def test_a_radio_that_is_not_answering_is_not_shown_as_reading(qtbot) -> None:
    """It is still being asked every heartbeat. What is not arriving is answers,
    and the mark is about answers."""
    panel = LinkPanel(
        FakeRadioService(
            {"connected": False, "reason": "cannot reach 10.0.0.9", "age_seconds": 1.0}
        )
    )
    qtbot.addWidget(panel)
    panel.refresh()
    glyph, _words, colour = panel.pulse()
    assert glyph not in panel.ARRIVING
    assert colour == PALETTE["warn"]


def test_a_radio_nobody_has_reached_yet_is_a_third_state(qtbot) -> None:
    """Never answered is not the same as answered-and-refused, and neither is
    the same as reading. The band got this wrong for months: no signal figure
    fell through to muted, so a hard failure looked exactly like still checking."""
    checking = LinkPanel(FakeRadioService({"connected": False, "checking": True}))
    qtbot.addWidget(checking)
    checking.refresh()
    refused = LinkPanel(
        FakeRadioService(
            {"connected": False, "reason": "Invalid credentials.", "age_seconds": 1.0}
        )
    )
    qtbot.addWidget(refused)
    refused.refresh()

    assert checking.pulse()[0] == "", "a radio nobody has reached is not a stopped one"
    assert refused.pulse()[0] != "", "a refusal must not look like still checking"
    assert texts(checking.lines()) != texts(refused.lines())


def test_a_radio_that_was_never_set_up_says_nothing_about_readings(qtbot) -> None:
    """Nothing is being read because nothing was asked for. That is not a fault
    and may not be drawn as one."""
    panel = LinkPanel(FakeRadioService({"connected": False, "reason": "the radio is not set up"}))
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.pulse()[0] == ""


# ------------------------------------------------------ stale has to LOOK stale


def test_every_figure_goes_grey_when_the_readings_stop() -> None:
    """Saying it is not enough. A panel full of coloured figures reads as a
    panel full of current figures, whatever the sentence at the bottom says -
    and he has to be able to tell at a glance whether he is looking at now or
    at four minutes ago."""
    stale = link_lines(reading(signal_dbm=-85, rx_mbps=17.5, rx_capacity_mbps=18.0,
                               age_seconds=STALE_AFTER_SECONDS + 200))
    figures = [(text, colour) for text, colour in stale if "ago" not in text.lower()]
    assert figures, "the panel stopped showing the figures altogether"
    assert all(colour == PALETTE["muted"] for _text, colour in figures), figures
    # And the one line that is about now - that these are not it - still is.
    assert coloured(stale, PALETTE["warn"])


def test_a_current_reading_keeps_the_colours_that_mean_something() -> None:
    live = link_lines(reading(signal_dbm=-85, rx_mbps=17.5, rx_capacity_mbps=18.0))
    assert coloured(live, PALETTE["alarm"]), "a marginal link now is an alarm now"


def test_the_throughput_against_capacity_survives_all_of_this() -> None:
    """The most useful figure on the panel: whether the visible camera fits on
    the link at all."""
    said = texts(link_lines(reading(rx_mbps=4.2, rx_capacity_mbps=18.0)))
    assert "4.2" in said and "18" in said
