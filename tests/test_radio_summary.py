"""The link at a glance: one word, two bars, one short line.

The panel this replaces was right and unreadable. Fourteen sentences, every one
of them the result of a day's work, in a column somebody walks past - and the
verdict from the person who actually stands in front of it was that it is too
much text for anybody who is not already an engineer: "much less text in the
link tab, make it easier to understand also for no tech guys, make it visual".

So what is tested here is not new readings. It is the same readings, and the two
things that can go wrong when a paragraph is turned into a picture:

* **the picture says something the sentences do not.** Two views of one radio
  that disagree are worse than the paragraph was, because now the operator has
  to decide which of his own console's opinions to believe. Every threshold used
  here is the one `link_lines` already uses, and the tests below pin them
  together rather than restating them.
* **the picture invents a figure.** A bar is a shape, and a shape has to be some
  length; the temptation to draw a missing reading as an empty bar rather than
  as nothing is exactly the "0 dBm because the field was not found" mistake the
  parser was written to avoid, one layer up.
"""

from __future__ import annotations

import pytest

from tests.test_radio_panel import FakeRadioService, his_link, reading, texts
from vmd.desktop.style import PALETTE, state_colour
from vmd.radio.meter import STILL_ENOUGH, UNKNOWN_CAPTION, Meter
from vmd.radio.panel import (
    HEADLINE_BUSY,
    HEADLINE_CHECKING,
    HEADLINE_FULL,
    HEADLINE_GOOD,
    HEADLINE_NO_LINK,
    HEADLINE_NOT_SET_UP,
    HEADLINE_WEAK,
    SIGNAL_CEILING_DBM,
    SIGNAL_FLOOR_DBM,
    STALE_AFTER_SECONDS,
    LinkPanel,
    link_lines,
    link_summary,
)


# ------------------------------------------------------------------- the word


def test_his_own_link_is_one_word_and_that_word_is_full() -> None:
    """The reading that started all of this: -66 dBm, 88% of the airtime spent,
    10.7 Mb/s of video on it. Fourteen sentences said so. One word has to."""
    summary = link_summary(his_link())
    assert summary["headline"] == HEADLINE_FULL
    assert summary["state"] == "alarm"


def test_a_link_with_nothing_wrong_with_it_says_so_and_stops_talking() -> None:
    summary = link_summary(reading(signal_dbm=-58, rx_mbps=1.0, rx_capacity_mbps=18.0))
    assert summary["headline"] == HEADLINE_GOOD
    assert summary["state"] == "ok"
    assert summary["note"] == "", "a healthy link does not need a sentence about itself"


def test_a_weak_signal_takes_the_headline_from_a_full_link() -> None:
    """Both are alarms and only one of them can be the word. The signal wins
    because it is the one with something to do about it on the roof - and
    because a signal near the noise is what fills a link in the first place."""
    both = link_summary(his_link(signal_dbm=-88, airtime_percent=95.0))
    assert both["headline"] == HEADLINE_WEAK


def test_a_busy_link_is_named_before_a_signal_with_thin_margin() -> None:
    busy = link_summary(his_link(signal_dbm=-70, airtime_percent=65.0))
    assert busy["headline"] == HEADLINE_BUSY
    assert busy["state"] == "warn"


def test_the_four_states_of_the_radio_are_four_different_words() -> None:
    """A radio nobody set up, one nobody has reached yet, one that refused and
    one that answered are four different situations, and the band got this
    wrong for months: a hard failure that reads as still-checking is invisible."""
    words = {
        link_summary({})["headline"],
        link_summary({"checking": True})["headline"],
        link_summary({"connected": False, "reason": "no", "age_seconds": 1.0})["headline"],
        link_summary(reading())["headline"],
    }
    assert words == {HEADLINE_NOT_SET_UP, HEADLINE_CHECKING, HEADLINE_NO_LINK, HEADLINE_GOOD}


def test_a_radio_that_was_never_set_up_is_not_drawn_as_a_fault() -> None:
    summary = link_summary({})
    assert summary["state"] == "muted", "an empty setting is not an alarm"
    assert "Settings" in summary["note"], "it has to say where to fix it"


def test_the_word_never_disagrees_with_the_sentences_underneath_it() -> None:
    """The rule that already governs the status band and this panel, now with a
    third view of the same radio between them. Whenever the sentences contain an
    alarm, the word above them is an alarm - and when they contain none, it is
    not."""
    for link in (
        reading(signal_dbm=-58),
        reading(signal_dbm=-85),
        reading(rx_mbps=17.5, rx_capacity_mbps=18.0),
        his_link(),
        his_link(airtime_percent=25.0),
        his_link(signal_dbm=-88),
    ):
        colours = {colour for _text, colour in link_lines(link)}
        summary = link_summary(link)
        loud = PALETTE["alarm"] in colours
        assert loud == (summary["state"] == "alarm"), (summary["headline"], colours)


# ------------------------------------------------------------------- the bars


def test_the_signal_bar_runs_between_the_noise_floor_and_a_link_better_than_this() -> None:
    assert link_summary(reading(signal_dbm=SIGNAL_FLOOR_DBM))["signal"] == 0.0
    assert link_summary(reading(signal_dbm=SIGNAL_CEILING_DBM))["signal"] == 100.0
    middle = link_summary(reading(signal_dbm=-70))["signal"]
    assert 45.0 < middle < 55.0, middle


@pytest.mark.parametrize("signal", [-140.0, -3.0])
def test_a_signal_off_either_end_of_the_scale_stays_on_the_bar(signal: float) -> None:
    """A bar drawn past its own track is a bar drawn over the panel beside it."""
    assert 0.0 <= link_summary(reading(signal_dbm=signal))["signal"] <= 100.0


def test_the_in_use_bar_is_the_airtime_when_the_radio_reports_one() -> None:
    summary = link_summary(his_link())
    assert summary["use"] == pytest.approx(88.0)
    assert summary["use_caption"] == "88%"
    assert summary["use_state"] == "alarm"


def test_the_in_use_bar_falls_back_to_the_busiest_direction_of_the_capacity() -> None:
    """An access point reports no airtime. The weaker reading is all it has, and
    it comes with its own marks - `link_lines` calls 97% of capacity full, and
    the bar may not call the same reading something else."""
    summary = link_summary(reading(rx_mbps=17.5, rx_capacity_mbps=18.0, tx_mbps=0.2))
    assert summary["use"] == pytest.approx(97.2, abs=0.1)
    assert summary["use_state"] == "alarm"


def test_a_figure_the_radio_did_not_report_is_no_bar_at_all_and_never_a_zero() -> None:
    """The parser's rule, one layer up: a console showing an empty bar because
    it could not find the field has told the operator the link is dead."""
    summary = link_summary(
        reading(signal_dbm=None, airtime_percent=None, rx_mbps=None, tx_mbps=None,
                rx_capacity_mbps=None, tx_capacity_mbps=None)
    )
    assert summary["signal"] is None
    assert summary["use"] is None
    assert summary["carrying"] == ""


def test_what_the_link_is_carrying_is_one_short_line() -> None:
    carrying = link_summary(his_link())["carrying"]
    assert "Mb/s" in carrying
    assert len(carrying) < 40, f"still a sentence: {carrying!r}"


# ----------------------------------------------------------------- gone stale


def test_a_stale_reading_greys_the_word_and_both_bars() -> None:
    """The rule the sentences already follow, and the reason it exists: a
    coloured panel reads as a current panel whatever is written at the bottom of
    it, and the one thing he has to be able to tell without reading is whether
    he is looking at now."""
    summary = link_summary(his_link(age_seconds=STALE_AFTER_SECONDS + 200))
    assert summary["state"] == "muted"
    assert summary["signal_state"] == "muted" and summary["use_state"] == "muted"
    assert summary["stale"] is True
    assert "ago" in summary["note"]


def test_a_stale_reading_still_shows_the_figures_it_had() -> None:
    """Greyed, not blanked. They were true when they were read, and an empty
    panel says less than an old one that admits its age."""
    summary = link_summary(his_link(age_seconds=STALE_AFTER_SECONDS + 200))
    assert summary["signal"] is not None and summary["use"] is not None


# ---------------------------------------------------------------- the meter


def test_the_bar_travels_to_a_reading_that_moved(qtbot) -> None:
    meter = Meter("Signal")
    qtbot.addWidget(meter)
    meter.set_reading(20.0, "-80 dBm", PALETTE["warn"])
    meter.settle()
    meter.set_reading(80.0, "-58 dBm", PALETTE["ok"])
    assert meter.filled() < 80.0, "the bar jumped instead of travelling"
    meter.settle()
    assert meter.filled() == pytest.approx(80.0)


def test_the_bar_does_not_animate_a_reading_that_did_not_really_move(qtbot) -> None:
    """The radio is read every four seconds and its figures jitter by a fraction
    of a percent. A bar that animated that would never be still, on a console
    that also decodes two video streams for months at a time."""
    meter = Meter("Link in use")
    qtbot.addWidget(meter)
    meter.set_reading(50.0, "50%", PALETTE["ok"])
    meter.settle()
    before = meter.travels
    meter.set_reading(50.0 + STILL_ENOUGH / 2, "50%", PALETTE["ok"])
    assert meter.travels == before
    assert meter.filled() == pytest.approx(50.0 + STILL_ENOUGH / 2)


def test_an_identical_reading_costs_the_bar_nothing(qtbot) -> None:
    meter = Meter("Signal")
    qtbot.addWidget(meter)
    meter.set_reading(60.0, "-66 dBm", PALETTE["warn"])
    meter.settle()
    before = meter.travels
    for _ in range(10):
        meter.set_reading(60.0, "-66 dBm", PALETTE["warn"])
    assert meter.travels == before


def test_a_bar_with_no_reading_says_so_rather_than_sitting_at_zero(qtbot) -> None:
    meter = Meter("Signal")
    qtbot.addWidget(meter)
    meter.set_reading(None, "", PALETTE["muted"])
    value, caption, _colour = meter.reading()
    assert value is None
    assert caption == UNKNOWN_CAPTION


# ----------------------------------------------------------------- the panel


def test_the_panel_leads_with_the_word_and_the_bars(qtbot) -> None:
    panel = LinkPanel(FakeRadioService(his_link()))
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.summary()["headline"] == HEADLINE_FULL
    signal, use = panel.meters()
    assert signal.reading()[1] == "-66 dBm"
    assert use.reading()[1] == "88%"


def test_the_bars_are_coloured_by_what_they_mean(qtbot) -> None:
    panel = LinkPanel(FakeRadioService(his_link()))
    qtbot.addWidget(panel)
    panel.refresh()
    signal, use = panel.meters()
    assert signal.reading()[2] == state_colour("warn"), "-66 dBm is workable, not healthy"
    assert use.reading()[2] == state_colour("alarm"), "88% of the airtime is not black"


def test_the_sentences_are_shut_by_default_and_still_exist(qtbot) -> None:
    """Shut, because the whole complaint was the paragraph. Still there, because
    whoever is helping him over the phone needs the reasoning that produced the
    word - and because nothing was deleted to make the panel shorter."""
    panel = LinkPanel(FakeRadioService(his_link()))
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.details_open() is False
    assert "airtime" in texts(panel.lines()), "the sentences were deleted, not folded away"


def test_opening_the_details_shows_the_sentences(qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    panel = LinkPanel(FakeRadioService(his_link()))
    qtbot.addWidget(panel)
    panel.resize(340, 700)
    panel.show()
    panel.show_details(True)
    QApplication.processEvents()
    visible = [
        label.text()
        for label in panel.findChildren(type(panel._pulse))
        if label.isVisibleTo(panel) and label.text()
    ]
    assert any("airtime" in text.lower() for text in visible), visible


def test_shutting_the_details_gives_the_room_back(qtbot) -> None:
    """The point of folding rather than deleting: the panel has to be smaller
    with them shut, or nothing was gained."""
    from PySide6.QtWidgets import QApplication

    panel = LinkPanel(FakeRadioService(his_link()))
    qtbot.addWidget(panel)
    panel.resize(340, 700)
    panel.show()
    panel.show_details(True)
    QApplication.processEvents()
    open_height = panel.sizeHint().height()
    panel.show_details(False)
    QApplication.processEvents()
    assert panel.sizeHint().height() < open_height


def test_the_panel_does_not_say_the_same_thing_three_times_when_opened(qtbot) -> None:
    """Opened on his own link, the panel said "Nothing else fits - the picture
    can stutter or drop during a pan", then "Airtime: 88% used - the link is
    full", then "Nothing else will fit on it. A picture that stutters, falls
    behind, or drops during a pan is this" - one fact, three times, in a panel
    he asked to have less text in.

    The note under the word is a shortened one of those sentences, so it stands
    down while they are up. The sentences keep their full wording rather than
    the note keeping its: they are what somebody reads out over the phone, and
    they have to stand up without the word above them.
    """
    from PySide6.QtWidgets import QApplication

    panel = LinkPanel(FakeRadioService(his_link()))
    qtbot.addWidget(panel)
    panel.resize(340, 900)
    panel.show()
    QApplication.processEvents()

    shut = [
        label.text()
        for label in panel.findChildren(type(panel._note))
        if label.isVisibleTo(panel) and label.text()
    ]
    assert any("stutter" in text for text in shut), "the short warning went missing"

    panel.show_details(True)
    QApplication.processEvents()
    opened = [
        label.text()
        for label in panel.findChildren(type(panel._note))
        if label.isVisibleTo(panel) and label.text()
    ]
    stutter = [text for text in opened if "stutter" in text]
    assert len(stutter) == 1, stutter
    # And the one that survived is the full sentence, not the summary.
    assert "not the camera" in stutter[0], stutter


def test_a_radio_that_throws_still_gets_a_word(qtbot) -> None:
    class Angry:
        def status(self) -> dict:
            raise OSError("the radio reader is gone")

    panel = LinkPanel(Angry())
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel.summary()["headline"] == HEADLINE_NO_LINK
