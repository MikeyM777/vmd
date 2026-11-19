"""When the Live tab switches between its day and night views. Pure: no Qt,
no network, no disk.

Naive local wall-clock time, like `vmd/ptz/restart_schedule.py`, for the same
reason: the offline PC may have no tz database, and the operator sets the times
off the clock on the wall.

Two switches a day: the day view from `day_from`, the night view from
`night_from`. Either period may cross midnight. The schedule only ever acts
when a switch time passes, so a view chosen by hand holds until the next one.

See docs/superpowers/specs/2026-11-19-view-schedule-design.md.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable

from vmd.ptz.restart_schedule import next_in_words
from vmd.settings import ViewSchedule

__all__ = [
    "active",
    "problem",
    "current",
    "crossed",
    "next_switch",
    "describe_switch",
    "next_in_words",
]

# A gap between two ticks longer than this is only searched over its last part.
# The latest switch inside it is all `crossed` needs, and two days always holds
# one, however long the console was asleep.
_LOOK_BACK = timedelta(days=2)


def _clock(at: str) -> time:
    hours, minutes = at.split(":")
    return time(int(hours), int(minutes))


def problem(sched: ViewSchedule, streams: Iterable[str]) -> str | None:
    """Why an enabled schedule cannot run, in words for the Settings box.

    None when it can run - or when it is simply off, which is not a problem.
    """
    names = list(streams)
    if not sched.enabled:
        return None
    if len(names) < 2:
        return "needs two views - add a second stream"
    if not sched.day_view or not sched.night_view:
        return "pick a view for day and for night"
    if sched.day_view == sched.night_view:
        return "day and night are the same view"
    for name in (sched.day_view, sched.night_view):
        if name not in names:
            return f"the view {name!r} is no longer one of the streams"
    return None


def active(sched: ViewSchedule, streams: Iterable[str]) -> bool:
    """Whether the schedule should switch anything at all."""
    return sched.enabled and problem(sched, streams) is None


def current(sched: ViewSchedule, now: datetime) -> str:
    """The view that should be on at `now`."""
    day, night = _clock(sched.day_from), _clock(sched.night_from)
    at = now.time()
    if day < night:
        return sched.day_view if day <= at < night else sched.night_view
    # The day crosses midnight (an odd setting, but a legal one).
    return sched.night_view if night <= at < day else sched.day_view


def _switches(sched: ViewSchedule, start: datetime, end: datetime):
    """Every switch with start < when <= end, oldest first."""
    found = []
    date = start.date()
    while date <= end.date():
        for at, view in ((sched.day_from, sched.day_view), (sched.night_from, sched.night_view)):
            when = datetime.combine(date, _clock(at))
            if start < when <= end:
                found.append((when, view))
        date += timedelta(days=1)
    found.sort(key=lambda item: item[0])
    return found


def crossed(sched: ViewSchedule, before: datetime, now: datetime) -> str | None:
    """The view a switch turned on between two ticks, or None.

    A switch time in (before, now] counts; several (a long gap) -> the latest.
    A clock put back (`now <= before`) switches nothing.
    """
    if now <= before:
        return None
    found = _switches(sched, max(before, now - _LOOK_BACK), now)
    return found[-1][1] if found else None


def next_switch(sched: ViewSchedule, now: datetime) -> tuple[datetime, str]:
    """The next switch strictly after `now`, and the view it turns on."""
    return _switches(sched, now, now + timedelta(days=1))[0]


def describe_switch(sched: ViewSchedule, view: str) -> str:
    """'thermal from 18:00' - for the Logs tab."""
    at = sched.day_from if view == sched.day_view else sched.night_from
    return f"{view} from {at}"
