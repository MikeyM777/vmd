"""vmd.desktop.view_schedule: thermal at night, visible by day."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from vmd.desktop import view_schedule as vs
from vmd.settings import Settings, ViewSchedule

STREAMS = ["thermal", "visible"]


def sched(**over) -> ViewSchedule:
    fields = dict(
        enabled=True, day_view="visible", day_from="06:00",
        night_view="thermal", night_from="18:00",
    )
    fields.update(over)
    return ViewSchedule(**fields)


def at(hh: int, mm: int = 0, ss: int = 0, day: int = 19) -> datetime:
    return datetime(2026, 11, day, hh, mm, ss)


# ------------------------------------------------------------------ settings


def test_an_old_file_switches_nothing() -> None:
    settings = Settings.model_validate({})
    assert settings.view_schedule.enabled is False
    assert settings.view_schedule.day_from == "06:00"
    assert settings.view_schedule.night_from == "18:00"


def test_a_schedule_survives_a_save_and_load() -> None:
    again = Settings.model_validate_json(Settings(view_schedule=sched()).model_dump_json())
    assert again.view_schedule == sched()


def test_a_time_is_written_back_as_hh_mm() -> None:
    assert sched(day_from="6:05").day_from == "06:05"


@pytest.mark.parametrize("bad", ["24:00", "12:60", "noon", "", "1200"])
def test_a_time_that_is_not_a_time_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        sched(night_from=bad)


def test_day_and_night_at_the_same_time_is_refused() -> None:
    with pytest.raises(ValidationError):
        sched(day_from="07:00", night_from="7:00")


# ------------------------------------------------------------------ active


def test_off_is_not_a_problem_and_not_active() -> None:
    off = sched(enabled=False)
    assert vs.problem(off, STREAMS) is None
    assert not vs.active(off, STREAMS)


def test_on_with_both_views_is_active() -> None:
    assert vs.active(sched(), STREAMS)


@pytest.mark.parametrize(
    ("over", "streams", "words"),
    [
        ({}, ["thermal"], "needs two views"),
        ({"day_view": ""}, STREAMS, "pick a view"),
        ({"day_view": "thermal"}, STREAMS, "same view"),
        ({"night_view": "ir"}, STREAMS, "'ir' is no longer"),
    ],
)
def test_why_it_cannot_run(over, streams, words) -> None:
    s = sched(**over)
    assert words in (vs.problem(s, streams) or "")
    assert not vs.active(s, streams)


# ------------------------------------------------------------------ current


@pytest.mark.parametrize(
    ("when", "view"),
    [
        (at(5, 59, 59), "thermal"),
        (at(6), "visible"),
        (at(12), "visible"),
        (at(17, 59, 59), "visible"),
        (at(18), "thermal"),
        (at(23, 59), "thermal"),
        (at(0), "thermal"),
    ],
)
def test_current_night_crossing_midnight(when, view) -> None:
    assert vs.current(sched(), when) == view


@pytest.mark.parametrize(
    ("when", "view"),
    [(at(21), "visible"), (at(2), "visible"), (at(19), "thermal"), (at(4), "thermal"), (at(12), "thermal")],
)
def test_current_day_crossing_midnight(when, view) -> None:
    odd = sched(day_from="20:00", night_from="04:00")
    assert vs.current(odd, when) == view


# ------------------------------------------------------------------ crossed


def test_a_two_second_tick_across_the_switch_switches() -> None:
    assert vs.crossed(sched(), at(17, 59, 59), at(18, 0, 1)) == "thermal"


def test_the_switch_instant_itself_counts_once() -> None:
    assert vs.crossed(sched(), at(17, 59, 58), at(18)) == "thermal"
    assert vs.crossed(sched(), at(18), at(18, 0, 2)) is None


def test_a_tick_with_no_switch_in_it_does_nothing() -> None:
    assert vs.crossed(sched(), at(12), at(12, 0, 2)) is None


def test_a_long_gap_takes_the_latest_switch() -> None:
    # 05:00 -> 19:00 crosses 06:00 (visible) then 18:00 (thermal).
    assert vs.crossed(sched(), at(5), at(19)) == "thermal"
    # Days asleep: still the latest one.
    assert vs.crossed(sched(), at(5, day=1), at(7, day=19)) == "visible"


def test_a_clock_put_back_switches_nothing() -> None:
    assert vs.crossed(sched(), at(18, 5), at(17, 55)) is None


def test_crossing_midnight_between_ticks() -> None:
    odd = sched(day_from="00:00", night_from="12:00")
    assert vs.crossed(odd, at(23, 59, 59, day=18), at(0, 0, 1)) == "visible"


# ------------------------------------------------------------------ next


def test_next_switch_today_and_tomorrow() -> None:
    assert vs.next_switch(sched(), at(12)) == (at(18), "thermal")
    assert vs.next_switch(sched(), at(18)) == (at(6, day=20), "visible")
    assert vs.next_switch(sched(), at(3)) == (at(6), "visible")


def test_describe_switch() -> None:
    assert vs.describe_switch(sched(), "thermal") == "thermal from 18:00"
    assert vs.describe_switch(sched(), "visible") == "visible from 06:00"
