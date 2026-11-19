"""The 'Switch the view on a schedule' box and its time dialog."""

from __future__ import annotations

from datetime import datetime

from vmd.desktop.view_schedule_box import TimeDialog, ViewScheduleBox
from vmd.settings import ViewSchedule

NOW = datetime(2026, 11, 19, 12, 0)
TWO = [("thermal", True), ("visible", False)]


def box(qtbot, sched: ViewSchedule | None = None, streams=TWO) -> ViewScheduleBox:
    widget = ViewScheduleBox()
    qtbot.addWidget(widget)
    widget.load(sched or ViewSchedule(), list(streams), lambda: NOW)
    return widget


def emitted(widget: ViewScheduleBox) -> list:
    seen: list = []
    widget.changed.connect(lambda: seen.append(True))
    return seen


# -- the box ------------------------------------------------------------------

def test_empty_views_are_filled_from_the_thermal_flags(qtbot) -> None:
    # Visible listed first: night must still be the thermal one.
    b = box(qtbot, streams=[("visible", False), ("thermal", True)])
    sched = b.schedule()
    assert (sched.day_view, sched.night_view) == ("visible", "thermal")
    assert (sched.day_from, sched.night_from) == ("06:00", "18:00")
    assert not sched.enabled
    assert b.next_line() == "Off"


def test_no_thermal_flag_falls_back_to_first_and_second(qtbot) -> None:
    b = box(qtbot, streams=[("a", False), ("b", False)])
    sched = b.schedule()
    assert (sched.day_view, sched.night_view) == ("a", "b")
    b = box(qtbot, streams=[("ir1", True), ("ir2", True)])
    assert (b.schedule().day_view, b.schedule().night_view) == ("ir2", "ir1")


def test_loading_emits_nothing(qtbot) -> None:
    widget = ViewScheduleBox()
    qtbot.addWidget(widget)
    seen = emitted(widget)
    widget.load(ViewSchedule(enabled=True, day_view="visible", night_view="thermal"),
                TWO, lambda: NOW)
    assert seen == []


def test_one_stream_disables_everything_and_says_why(qtbot) -> None:
    b = box(qtbot, streams=[("thermal", True)])
    assert not b.enabled_box.isEnabled()
    assert not b.day_combo.isEnabled() and not b.night_combo.isEnabled()
    assert not b.day_time.isEnabled() and not b.night_time.isEnabled()
    assert b.next_line() == "needs two views - add a second stream"


def test_ticking_it_on_emits_and_says_the_next_switch(qtbot) -> None:
    b = box(qtbot)
    seen = emitted(b)
    b.enabled_box.click()
    assert seen == [True]
    assert b.schedule().enabled
    assert b.next_line() == "Next switch: today 18:00 (in 6 h 0 min) → thermal"


def test_picking_the_same_view_for_both_says_so(qtbot) -> None:
    b = box(qtbot, ViewSchedule(enabled=True, day_view="visible", night_view="thermal"))
    seen = emitted(b)
    b.day_combo.setCurrentText("thermal")
    assert seen == [True]
    assert b.schedule().day_view == "thermal"
    assert b.next_line() == "day and night are the same view"


def test_a_view_that_is_gone_is_kept_and_named(qtbot) -> None:
    b = box(qtbot, ViewSchedule(enabled=True, day_view="day", night_view="thermal"))
    assert b.schedule().day_view == "day"
    assert b.next_line() == "the view 'day' is no longer one of the streams"


def test_a_time_picked_in_the_dialog_is_saved(qtbot) -> None:
    b = box(qtbot, ViewSchedule(enabled=True, day_view="visible", night_view="thermal"))
    asked: list = []

    def answer(initial: str, other: str) -> str:
        asked.append((initial, other))
        return "19:30"

    b.ask_for_time = answer
    seen = emitted(b)
    b.night_time.click()
    assert asked == [("18:00", "06:00")]
    assert seen == [True]
    assert b.night_time.text() == "19:30"
    assert b.schedule().night_from == "19:30"
    assert b.next_line() == "Next switch: today 19:30 (in 7 h 30 min) → thermal"


def test_a_cancelled_dialog_changes_nothing(qtbot) -> None:
    b = box(qtbot)
    b.ask_for_time = lambda initial, other: None
    seen = emitted(b)
    b.day_time.click()
    assert seen == []
    assert b.schedule().day_from == "06:00"


# -- the time dialog ------------------------------------------------------------

def test_the_dialog_starts_on_the_time_it_was_given(qtbot) -> None:
    d = TimeDialog("18:45")
    qtbot.addWidget(d)
    assert d.value() == "18:45"
    assert d.hour_buttons[18].isChecked() and d.minute_buttons[45].isChecked()


def test_hour_and_minute_choices_make_the_value(qtbot) -> None:
    d = TimeDialog("18:00")
    qtbot.addWidget(d)
    d.hour_buttons[7].click()
    d.minute_buttons[15].click()
    assert d.value() == "07:15"
    d.minute_box.setValue(20)
    assert d.value() == "07:20"
    assert not d.minute_buttons[15].isChecked()
    assert d.ok_button.isEnabled()


def test_the_other_periods_time_is_refused(qtbot) -> None:
    d = TimeDialog("18:00", forbidden="06:00")
    qtbot.addWidget(d)
    d.choose_hour(6)
    d.choose_minute(0)
    assert not d.ok_button.isEnabled()
    assert d.note_text() == "day and night cannot start at the same time"
    d.choose_minute(30)
    assert d.ok_button.isEnabled()
    assert d.note_text() == ""
