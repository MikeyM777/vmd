"""When the camera restarts by itself. Pure: no Qt, no network, no disk.

Every time here is naive local wall-clock time, the same thing the operator
reads off the clock on the wall. No `zoneinfo`: the offline PC may have no tz
database. What that costs is in the spec and is accepted - on the two DST
nights an `every_hours` rule is an hour off once, a wall time that does not
exist that night is skipped, and one that happens twice fires once.

See docs/superpowers/specs/2026-11-11-camera-restart-design.md.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta

from vmd.settings import RestartRule

# How late a tick may notice a slot and still act on it. The heartbeat is two
# seconds; a slot older than this was missed (the console was closed, the PC
# was off) and is skipped rather than caught up - a console starting after a
# power cut must not restart a camera that has only just come up.
GRACE = timedelta(seconds=60)

# Nothing restarts within this long of the last restart, either side of it.
# The camera takes about two minutes to come back; restarting it again while it
# boots achieves nothing. Not longer than this: at ten minutes, a restart by
# hand at 22:10 silently swallowed the scheduled one at 22:15 - the first time
# the owner tried it. This is also what merges two rules that land together,
# and what stops the repeated hour at the end of summer time firing twice.
MIN_GAP = timedelta(minutes=3)

# The red warning on the Live tab appears this long before a scheduled restart.
WARN_BEFORE = timedelta(minutes=10)

# Seconds left at which the warning flashes for FLASH_SECONDS, and below which
# it flashes all the time.
FLASH_MARKS = (600, 300, 60)
FLASH_SECONDS = 4
FLASH_ALWAYS_BELOW = 30


def _clock(at: str) -> time:
    hours, minutes = at.split(":")
    return time(int(hours), int(minutes))


def next_slot(rule: RestartRule, after: datetime) -> datetime:
    """The first restart time of this rule strictly later than `after`.

    Every rule repeats for ever; there is no last slot.
    """
    if rule.kind == "every_hours":
        step = timedelta(hours=rule.hours)
        if after < rule.since:
            return rule.since + step
        k = math.floor((after - rule.since) / step) + 1
        return rule.since + k * step

    at = _clock(rule.at)
    if rule.kind == "daily":
        start = max(after, rule.since)
        candidate = datetime.combine(start.date(), at)
        if candidate <= start:
            candidate += timedelta(days=1)
        return candidate

    # every_days: the first `at` at or after `since`, then every N days.
    first = datetime.combine(rule.since.date(), at)
    if first < rule.since:
        first += timedelta(days=1)
    if after < first:
        return first
    step = timedelta(days=rule.days)
    k = math.floor((after - first) / step) + 1
    return first + k * step


def upcoming_slots(rule: RestartRule, now: datetime, count: int) -> list[datetime]:
    """The next `count` restarts of one rule - what the editor shows."""
    slots: list[datetime] = []
    when = now
    for _ in range(count):
        when = next_slot(rule, when)
        slots.append(when)
    return slots


def _blocked(when: datetime, last_restart: datetime | None) -> bool:
    return last_restart is not None and abs(when - last_restart) < MIN_GAP


def due(
    rules: list[RestartRule], now: datetime, last_restart: datetime | None
) -> RestartRule | None:
    """The rule whose time has just come, or None.

    A slot is due when it lies in (now - GRACE, now] and nothing restarted the
    camera within MIN_GAP of now - either side, so a clock put back does not
    restart it again either.
    """
    if _blocked(now, last_restart):
        return None
    for rule in rules:
        if next_slot(rule, now - GRACE) <= now:
            return rule
    return None


def next_restart(
    rules: list[RestartRule], now: datetime
) -> tuple[datetime, RestartRule] | None:
    """The next restart of all the rules, for the "Next restart" line."""
    best: tuple[datetime, RestartRule] | None = None
    for rule in rules:
        when = next_slot(rule, now)
        if best is None or when < best[0]:
            best = (when, rule)
    return best


def upcoming(
    rules: list[RestartRule],
    now: datetime,
    last_restart: datetime | None,
    within: timedelta = WARN_BEFORE,
) -> tuple[datetime, RestartRule] | None:
    """The restart the Live tab should be warning about, or None.

    A slot that `due` will skip (too close to the last restart) is not warned
    about: a countdown to nothing is worse than no countdown.
    """
    best: tuple[datetime, RestartRule] | None = None
    for rule in rules:
        when = next_slot(rule, now - timedelta(microseconds=1))
        if when - now > within or _blocked(when, last_restart):
            continue
        if best is None or when < best[0]:
            best = (when, rule)
    return best


def flashing(seconds_left: float) -> bool:
    """Whether the warning flashes with this long left."""
    if seconds_left <= FLASH_ALWAYS_BELOW:
        return True
    return any(mark - FLASH_SECONDS < seconds_left <= mark for mark in FLASH_MARKS)


def clock_words(seconds_left: float) -> str:
    """'9:59' - whole seconds, rounded up, so it never shows 0:00 too early."""
    whole = max(0, math.ceil(seconds_left))
    return f"{whole // 60}:{whole % 60:02d}"


def describe(rule: RestartRule) -> str:
    """The rule in the words the Logs tab and the warning use."""
    if rule.kind == "every_hours":
        return "every hour" if rule.hours == 1 else f"every {rule.hours} hours"
    if rule.kind == "daily":
        return f"every day at {rule.at}"
    return f"every {rule.days} days at {rule.at}"


def day_words(when: datetime, now: datetime) -> str:
    if when.date() == now.date():
        return "today"
    if when.date() == (now + timedelta(days=1)).date():
        return "tomorrow"
    return when.strftime("%a %d.%m")


def next_in_words(when: datetime, now: datetime) -> str:
    """'today 16:00 (in 3 h 12 min)', 'Fri 13.11 19:00 (in 2 days)'."""
    left = max(timedelta(0), when - now)
    minutes = max(1, math.ceil(left.total_seconds() / 60))
    if minutes >= 48 * 60:
        span = f"in {round(minutes / (24 * 60))} days"
    elif minutes >= 60:
        span = f"in {minutes // 60} h {minutes % 60} min"
    else:
        # Under an hour it counts down in seconds, so the line is seen to move.
        span = f"in {clock_words(left.total_seconds())}"
    return f"{day_words(when, now)} {when:%H:%M} ({span})"
