"""The 'Restart the camera on a schedule' box and the dialog that adds a rule."""

from __future__ import annotations

from datetime import datetime

from vmd.desktop.restart_box import NOTHING, RestartBox, RestartDialog
from vmd.settings import RestartRule

NOW = datetime(2026, 11, 11, 12, 48)  # a Wednesday


def dialog(qtbot) -> RestartDialog:
    widget = RestartDialog(lambda: NOW)
    qtbot.addWidget(widget)
    return widget


def box(qtbot, rules=()) -> RestartBox:
    widget = RestartBox()
    qtbot.addWidget(widget)
    widget.load(list(rules), lambda: NOW)
    return widget


# -- the dialog ---------------------------------------------------------------

def test_nothing_can_be_added_until_every_step_is_chosen(qtbot) -> None:
    d = dialog(qtbot)
    assert d.rule() is None and not d.add_button.isEnabled()
    d.kind_buttons["every_days"].click()
    assert d.rule() is None
    d.day_count_buttons[5].click()
    assert d.rule() is None
    d.hour_buttons[19].click()
    assert d.add_button.isEnabled()
    assert d.rule() == RestartRule(kind="every_days", days=5, at="19:00", since=NOW)


def test_every_few_days_says_it_repeats_forever(qtbot) -> None:
    d = dialog(qtbot)
    d.choose_kind("every_days")
    d.choose_number(5)
    d.choose_hour(19)
    assert d.sentence_text() == "Restart every 5 days at 19:00 - again and again, forever."
    assert d.times_text().startswith(
        "Next: today 19:00  →  Mon 16.11 19:00  →  Sat 21.11 19:00  →  Thu 26.11 19:00  →  … and so on"
    )


def test_every_few_hours_needs_no_time(qtbot) -> None:
    d = dialog(qtbot)
    d.kind_buttons["every_hours"].click()
    d.hour_count_buttons[2].click()
    assert d.rule() == RestartRule(kind="every_hours", hours=2, since=NOW)
    assert d.sentence_text() == "Restart every 2 hours - again and again, forever."


def test_once_a_day_with_minutes(qtbot) -> None:
    d = dialog(qtbot)
    d.kind_buttons["daily"].click()
    d.hour_buttons[16].click()
    d.minute_buttons[30].click()
    assert d.rule().at == "16:30"
    assert d.sentence_text() == "Restart every day at 16:30 - every day, forever."


def test_changing_how_often_clears_the_number(qtbot) -> None:
    d = dialog(qtbot)
    d.choose_kind("every_hours")
    d.choose_number(2)
    d.choose_kind("every_days")
    assert d.number is None
    assert d.rule() is None
    assert not d.hour_count_buttons[2].isChecked()


# -- the box ------------------------------------------------------------------

def test_empty_says_there_is_nothing_scheduled(qtbot) -> None:
    widget = box(qtbot)
    assert widget.values() == []
    assert widget._empty.text() == NOTHING
    assert widget.next_line() == ""


def test_add_puts_the_rule_on_the_list_as_a_sentence(qtbot) -> None:
    widget = box(qtbot)
    rule = RestartRule(kind="every_hours", hours=2, since=NOW)
    widget.ask_for_rule = lambda: rule
    widget.add_button.click()
    assert widget.rules() == [rule]
    assert widget.rows[0].sentence.text() == "Restart every 2 hours - again and again, forever"
    assert widget.next_line() == "Next restart: today 14:48 (in 2 h 0 min)"


def test_cancel_adds_nothing(qtbot) -> None:
    widget = box(qtbot)
    widget.ask_for_rule = lambda: None
    widget.add_button.click()
    assert widget.rules() == []


def test_delete_takes_a_rule_off(qtbot) -> None:
    widget = box(qtbot, [RestartRule(kind="daily", at="16:00", since=NOW)])
    widget.rows[0].delete.click()
    assert widget.rules() == []
    assert widget.next_line() == ""


def test_loaded_rules_keep_when_they_were_added(qtbot) -> None:
    saved = datetime(2026, 11, 1, 9, 0)
    widget = box(qtbot, [RestartRule(kind="every_hours", hours=2, since=saved)])
    assert widget.values()[0]["since"] == saved
