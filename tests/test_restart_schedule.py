"""When the camera restarts by itself. Pure arithmetic on wall-clock times."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from vmd.ptz.restart_schedule import (
    clock_words,
    day_words,
    describe,
    due,
    flashing,
    next_in_words,
    next_restart,
    next_slot,
    upcoming,
    upcoming_slots,
)
from vmd.settings import RestartRule

SAVED = datetime(2026, 11, 11, 13, 17)  # a Wednesday


def every_hours(n: int, since: datetime = SAVED) -> RestartRule:
    return RestartRule(kind="every_hours", hours=n, since=since)


def daily(at: str, since: datetime = SAVED) -> RestartRule:
    return RestartRule(kind="daily", at=at, since=since)


def every_days(n: int, at: str, since: datetime = SAVED) -> RestartRule:
    return RestartRule(kind="every_days", days=n, at=at, since=since)


# -- next_slot ----------------------------------------------------------------

def test_every_two_hours_counts_from_the_moment_it_was_saved() -> None:
    rule = every_hours(2)
    assert next_slot(rule, SAVED) == datetime(2026, 11, 11, 15, 17)
    assert next_slot(rule, datetime(2026, 11, 11, 15, 17)) == datetime(2026, 11, 11, 17, 17)
    assert next_slot(rule, datetime(2026, 11, 11, 23, 30)) == datetime(2026, 11, 12, 1, 17)


def test_daily_is_today_if_still_ahead_else_tomorrow() -> None:
    rule = daily("16:00")
    assert next_slot(rule, SAVED) == datetime(2026, 11, 11, 16, 0)
    assert next_slot(rule, datetime(2026, 11, 11, 16, 0)) == datetime(2026, 11, 12, 16, 0)


def test_nothing_fires_before_the_rule_was_saved() -> None:
    assert next_slot(daily("09:00"), datetime(2026, 11, 10, 8, 0)) == datetime(2026, 11, 12, 9, 0)
    assert next_slot(every_hours(2), datetime(2026, 11, 10)) == datetime(2026, 11, 11, 15, 17)


def test_every_two_days_starts_on_the_first_time_after_saving_and_repeats() -> None:
    rule = every_days(2, "19:00")
    assert next_slot(rule, SAVED) == datetime(2026, 11, 11, 19, 0)
    assert next_slot(rule, datetime(2026, 11, 11, 19, 0)) == datetime(2026, 11, 13, 19, 0)
    assert next_slot(rule, datetime(2026, 11, 12, 20, 0)) == datetime(2026, 11, 13, 19, 0)
    assert next_slot(rule, datetime(2027, 3, 1)) > datetime(2027, 3, 1)  # still going months later


def test_every_two_days_saved_after_the_time_starts_tomorrow() -> None:
    assert next_slot(every_days(2, "10:00"), SAVED) == datetime(2026, 11, 12, 10, 0)


def test_upcoming_slots_keep_repeating() -> None:
    assert upcoming_slots(every_days(5, "19:00"), SAVED, 4) == [
        datetime(2026, 11, 11, 19, 0),
        datetime(2026, 11, 16, 19, 0),
        datetime(2026, 11, 21, 19, 0),
        datetime(2026, 11, 26, 19, 0),
    ]


# -- due ----------------------------------------------------------------------

def test_a_rule_is_due_on_the_tick_that_crosses_it() -> None:
    rule = daily("16:00")
    assert due([rule], datetime(2026, 11, 11, 15, 59, 59), None) is None
    assert due([rule], datetime(2026, 11, 11, 16, 0, 1), None) is rule


def test_a_missed_slot_is_skipped_not_caught_up() -> None:
    assert due([daily("16:00")], datetime(2026, 11, 11, 17, 40), None) is None


def test_a_restart_just_done_is_not_done_again() -> None:
    just = datetime(2026, 11, 11, 16, 0, 1)
    assert due([daily("16:00")], datetime(2026, 11, 11, 16, 0, 3), last_restart=just) is None


def test_two_rules_within_ten_minutes_give_one_restart() -> None:
    hourly = every_hours(1, since=datetime(2026, 11, 11, 15, 5))  # 16:05
    at_four = daily("16:00")
    assert due([hourly, at_four], datetime(2026, 11, 11, 16, 0, 1), None) is at_four
    after = datetime(2026, 11, 11, 16, 0, 1)
    assert due([hourly, at_four], datetime(2026, 11, 11, 16, 5, 1), last_restart=after) is None


def test_a_clock_put_back_does_not_restart_twice() -> None:
    later = datetime(2026, 10, 25, 1, 30, 5)
    assert due([daily("01:30")], datetime(2026, 10, 25, 1, 30, 1), last_restart=later) is None


def test_no_rules_nothing_due() -> None:
    assert due([], datetime(2026, 11, 11, 16, 0), None) is None


# -- the warning before it ----------------------------------------------------

def test_the_warning_starts_ten_minutes_before() -> None:
    rule = daily("16:00")
    assert upcoming([rule], datetime(2026, 11, 11, 15, 49, 59), None) is None
    assert upcoming([rule], datetime(2026, 11, 11, 15, 50), None) == (datetime(2026, 11, 11, 16, 0), rule)


def test_no_warning_for_a_restart_that_will_be_skipped() -> None:
    rule = daily("16:00")
    just = datetime(2026, 11, 11, 15, 55)
    assert upcoming([rule], datetime(2026, 11, 11, 15, 56), last_restart=just) is None


@pytest.mark.parametrize(
    ("left", "expected"),
    [(600, True), (597, True), (595, False), (400, False), (300, True), (296, False),
     (60, True), (57, True), (45, False), (30, True), (12, True), (1, True)],
)
def test_it_flashes_at_the_marks_and_all_through_the_last_half_minute(left, expected) -> None:
    assert flashing(left) is expected


def test_the_clock_reads_minutes_and_seconds() -> None:
    assert clock_words(600) == "10:00"
    assert clock_words(599.2) == "10:00"
    assert clock_words(59.5) == "1:00"
    assert clock_words(9) == "0:09"
    assert clock_words(0) == "0:00"


# -- what the operator reads --------------------------------------------------

def test_next_restart_is_the_earliest_of_all_rules() -> None:
    rules = [every_days(2, "19:00"), daily("16:00"), every_hours(2)]
    when, rule = next_restart(rules, datetime(2026, 11, 11, 14, 0))
    assert when == datetime(2026, 11, 11, 15, 17)
    assert rule is rules[2]
    assert next_restart([], datetime(2026, 11, 11, 14, 0)) is None


def test_each_rule_has_a_sentence() -> None:
    assert describe(every_hours(2)) == "every 2 hours"
    assert describe(every_hours(1)) == "every hour"
    assert describe(daily("16:00")) == "every day at 16:00"
    assert describe(every_days(2, "19:00")) == "every 2 days at 19:00"


def test_days_in_words() -> None:
    now = datetime(2026, 11, 11, 12, 48)
    assert day_words(datetime(2026, 11, 11, 23, 0), now) == "today"
    assert day_words(datetime(2026, 11, 12, 1, 0), now) == "tomorrow"
    assert day_words(datetime(2026, 11, 13, 19, 0), now) == "Fri 13.11"


def test_the_next_restart_in_words() -> None:
    now = datetime(2026, 11, 11, 12, 48)
    assert next_in_words(datetime(2026, 11, 11, 16, 0), now) == "today 16:00 (in 3 h 12 min)"
    assert next_in_words(datetime(2026, 11, 12, 9, 0), now) == "tomorrow 09:00 (in 20 h 12 min)"
    assert next_in_words(datetime(2026, 11, 13, 19, 0), now) == "Fri 13.11 19:00 (in 2 days)"
    assert next_in_words(now + timedelta(seconds=30), now) == "today 12:48 (in 1 min)"
