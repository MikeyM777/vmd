"""Deciding what footage to delete, and warning before it happens."""

from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vmd.storage.index import Segment, SegmentIndex

logger = logging.getLogger(__name__)

DAY_SECONDS = 86400.0

# How far the wall clock may move, beyond the time that actually passed, before
# it stops being believed.
#
# This laptop is offline. There is no NTP, so nothing corrects a date entered
# wrong and nothing contradicts it, and the date is typed by a person. A day is
# far more than any real correction on a machine that is never off for long, and
# far less than the mistake that matters - a year, or a decade, from a mistyped
# field.
CLOCK_JUMP_SECONDS = DAY_SECONDS

# How many passes must agree about the time before the age rule is allowed to
# delete anything again. Two, because one pass agreeing with a wrong clock is
# only the wrong clock repeating itself, and because at the ordinary retention
# cadence that is a couple of minutes - long enough for somebody who has just
# mistyped a date to correct it, short enough that a genuine correction is not
# held up.
CONFIRMING_PASSES = 2

# The most segments the age rule may remove in one pass.
#
# In ordinary working it needs one or two: a pass every minute removes whatever
# crossed the boundary in that minute. Wanting hundreds at once is never the
# ordinary working of an age rule - it is a clock that has moved, or a retention
# policy that has just been shortened by a lot - and both are worth doing slowly
# and saying out loud. Twelve is an hour of five-minute segments, so a genuine
# policy change still converges in hours rather than never.
AGE_DELETIONS_PER_PASS = 12


@dataclass
class RetentionPlan:
    delete: list[Segment] = field(default_factory=list)
    warning: str | None = None
    used_bytes: int = 0
    budget_bytes: int = 0
    # Why the age rule did not do everything it was asked to, if it did not.
    # Deleting footage is irreversible, so declining has to be as visible as
    # deleting would have been: silence here reads exactly like a rule that ran
    # and found nothing to do.
    declined: str | None = None


def _readable(when: float, pattern: str = "%d %B %Y %H:%M") -> str:
    """A date an operator can read, from a number that may not be one.

    The whole point of this module's clock handling is that `when` may be
    nonsense - a hand-typed year, a negative epoch after a jump backwards - and
    `fromtimestamp` raises on exactly those. A message about a broken clock must
    never be the thing that breaks.
    """
    try:
        return datetime.datetime.fromtimestamp(when).strftime(pattern)
    except (OSError, OverflowError, ValueError):
        return f"{when:.0f} seconds past 1970, which is not a date this machine can show"


@dataclass(frozen=True)
class ClockVerdict:
    """Whether the wall clock can be believed enough to delete footage by age."""

    trusted: bool
    jumped: float  # seconds the clock moved that time did not
    reason: str  # "" when there is nothing to say


class ClockWatch:
    """Watches the wall clock against a clock that cannot be set by hand.

    Two clocks, and only one of them can be typed into: `time.monotonic` counts
    forward at one second per second whatever anybody does to the date. Holding
    them against each other is the only way this machine can tell "an hour
    passed" from "somebody set the year to 2027".

    The last reading is written to disk, because the reboot is the whole
    scenario: a Windows update or a power cut, an operator who sets the date
    afterwards, and a recorder that starts with no memory of what the time used
    to be. Across a restart only a backwards move is provable - the machine may
    genuinely have been off for a month - so a forward one is believed there and
    caught instead by the archive's own timestamps; see `plan_retention`.
    """

    def __init__(
        self,
        path: str | Path,
        jump_limit: float = CLOCK_JUMP_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._jump_limit = float(jump_limit)
        self._monotonic = monotonic
        # None until this process has taken a reading of its own. Until then the
        # only comparison available is against the file, which says nothing
        # about how long the machine was off.
        self._marked_at: float | None = None
        self._seen_at, self._agreed = self._load()

    @property
    def trusted(self) -> bool:
        return self._agreed >= CONFIRMING_PASSES

    def observe(self, now: float) -> ClockVerdict:
        previous, agreed = self._seen_at, self._agreed
        elapsed = (
            None if self._marked_at is None else max(self._monotonic() - self._marked_at, 0.0)
        )
        self._marked_at = self._monotonic()

        if previous is None:
            self._remember(now, 0)
            return ClockVerdict(
                trusted=False,
                jumped=0.0,
                reason=(
                    "this is the first retention pass on this archive, so there "
                    "is nothing yet to compare the clock against"
                ),
            )

        expected = previous + elapsed if elapsed is not None else previous
        jump = now - expected
        implausible = (
            jump < -self._jump_limit if elapsed is None else abs(jump) > self._jump_limit
        )
        if implausible:
            self._remember(now, 0)
            return ClockVerdict(
                trusted=False,
                jumped=jump,
                reason=(
                    f"the clock moved {jump / DAY_SECONDS:+.1f} days that time did "
                    f"not - it now says {_readable(now)}. "
                    f"Deleting footage for being old is suspended until "
                    f"{CONFIRMING_PASSES} passes agree about the time"
                ),
            )

        agreed = min(agreed + 1, CONFIRMING_PASSES)
        self._remember(now, agreed)
        if agreed < CONFIRMING_PASSES:
            return ClockVerdict(
                trusted=False,
                jumped=jump,
                reason=(
                    f"the clock has been agreed by {agreed} of {CONFIRMING_PASSES} "
                    f"passes since it last moved"
                ),
            )
        return ClockVerdict(trusted=True, jumped=jump, reason="")

    # -- the file ---------------------------------------------------------

    def _load(self) -> tuple[float | None, int]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return float(payload["seen_at"]), int(payload.get("agreed", 0))
        except (OSError, ValueError, TypeError, KeyError):
            # No file, or one that cannot be read. Nothing is known, which is
            # the same state as a fresh archive: the next passes establish it.
            return None, 0

    def _remember(self, now: float, agreed: int) -> None:
        self._seen_at, self._agreed = now, agreed
        payload = json.dumps({"seen_at": now, "agreed": agreed})
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=self._path.name + ".", suffix=".tmp"
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as file:
                    file.write(payload)
                os.replace(temp_path, self._path)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        except OSError:
            # Not fatal, and not silent. Without the file a clock jump across a
            # restart goes unnoticed; with a half-written one it would be read
            # as a jump that never happened, which is why it is replaced rather
            # than overwritten.
            logger.warning(
                "could not write %s, so a clock jump across a restart may go "
                "unnoticed",
                self._path,
                exc_info=True,
            )


def plan_retention(
    segments: list[Segment],
    now: float,
    budget_bytes: int,
    budget_enabled: bool,
    retention_days: int | None,
    warn_at_fraction: float,
    bytes_per_second: float,
    clock_reason: str = "",
    age_limit_per_pass: int = AGE_DELETIONS_PER_PASS,
) -> RetentionPlan:
    """Decide which segments to remove. Pure: no filesystem, no clock, no side effects.

    Two independent rules, either of which may be disabled:
      age    - remove anything that ended more than `retention_days` ago
      budget - while the total exceeds `budget_bytes`, remove the oldest

    The budget rule reads no clock at all and is deliberately untouched by
    everything below: a full disk still has to be reclaimed however wrong the
    date is, and a disk that fills is the failure this whole service exists to
    avoid.

    `clock_reason` is non-empty when the wall clock cannot be believed, and says
    why. The age rule does not run at all then, and says so - see `declined`.

    Sorting is deliberately defensive rather than assumed. It is close to free because
    callers pass SegmentIndex.all(), which is already ordered by the same key.
    """
    ordered = sorted(segments, key=lambda s: (s.start, s.id))
    used_bytes = sum(s.size_bytes for s in ordered)
    plan = RetentionPlan(used_bytes=used_bytes, budget_bytes=budget_bytes)

    doomed_ids: set[int] = set()

    if retention_days is not None and clock_reason:
        plan.declined = (
            f"Nothing is being deleted for being older than {retention_days} days, "
            f"because {clock_reason}. The storage budget is unaffected and still "
            f"reclaims space."
        )
    elif retention_days is not None:
        # Measured from the newest recording, not from the clock, whenever the
        # clock is running ahead of the archive. "Keep thirty days" is a
        # statement about the recordings, and the recordings carry their own
        # timestamps: this is the same answer as `now` whenever the clock is
        # right, and the difference between keeping the archive and losing it
        # when a date has been typed a year out. It is also the more honest
        # reading after an outage - a machine that recorded nothing for a week
        # kept a week of footage, not nothing.
        newest = max((segment.end for segment in ordered), default=now)
        cutoff = min(now, newest) - retention_days * DAY_SECONDS
        doomed = [segment for segment in ordered if segment.end < cutoff]
        if len(doomed) > age_limit_per_pass:
            plan.declined = (
                f"{len(doomed)} recordings are older than the {retention_days}-day "
                f"rule, which is far more than one pass ever removes in ordinary "
                f"working. Only {age_limit_per_pass} are being deleted now, and the "
                f"rest on later passes - check the machine's date before letting it "
                f"run on."
            )
            doomed = doomed[:age_limit_per_pass]
        for segment in doomed:
            plan.delete.append(segment)
            doomed_ids.add(segment.id)

    if budget_enabled:
        remaining = used_bytes - sum(s.size_bytes for s in plan.delete)
        for segment in ordered:
            if remaining <= budget_bytes:
                break
            if segment.id in doomed_ids:
                continue
            plan.delete.append(segment)
            doomed_ids.add(segment.id)
            remaining -= segment.size_bytes

    if budget_enabled and used_bytes >= warn_at_fraction * budget_bytes:
        surviving = [s for s in ordered if s.id not in doomed_ids]
        plan.warning = _warning_text(
            ordered, surviving, used_bytes, budget_bytes, bytes_per_second, bool(plan.delete)
        )

    return plan


def _warning_text(
    ordered: list[Segment],
    surviving: list[Segment],
    used_bytes: int,
    budget_bytes: int,
    bytes_per_second: float,
    deleting: bool,
) -> str:
    """The operator-facing storage message.

    Two distinct states, and both must be reported. A system at its budget deletes
    on almost every pass, so a message that appears only when nothing is being
    deleted would be visible during the initial fill-up and then silent for the rest
    of the deployment - precisely when footage is actually being lost.
    """
    percent = 100.0 * used_bytes / budget_bytes if budget_bytes else 100.0

    if deleting:
        kept = surviving[0] if surviving else None
        edge = _readable(kept.start) if kept else "everything"
        return (
            f"Storage {percent:.0f}% full. Oldest footage is being deleted continuously "
            f"to keep recording. Nothing before {edge} is still held."
        )

    oldest = ordered[0] if ordered else None
    when = _readable(oldest.start, "%d %B %Y") if oldest else "the oldest footage"
    headroom = max(budget_bytes - used_bytes, 0)
    if bytes_per_second > 0:
        hours = headroom / (bytes_per_second * 3600.0)
        # The figure is rounded first and the word chosen from what it rounded
        # to, never from the unrounded one: 1.4 hours is drawn as `1` and so has
        # to read `1 hour`. This is the last warning before footage starts being
        # deleted, and a sentence that looks unfinished at that moment reads as a
        # console that is broken rather than a drive that is filling.
        whole = round(hours)
        timing = (
            f"in about {whole:.0f} hour" + ("" if whole == 1 else "s")
            if hours >= 1
            else "within the hour"
        )
    else:
        timing = "once recording resumes"
    return f"Storage {percent:.0f}% full. Footage from {when} will be deleted {timing}."


def apply_plan(
    plan: RetentionPlan,
    index: SegmentIndex,
    unlink: Callable[[str], None] = os.unlink,
    events=None,
) -> list[Segment]:
    """Delete the planned segments from disk and from the index.

    Returns the segments actually removed, which is not always everything that was
    planned: a locked file is left for a later attempt. Callers need to know which
    ones really went, not just how many.

    A file that is already gone is not an error - the index row is still removed, so the
    catalogue converges on the truth rather than accumulating dead entries.

    `events` is an optional event store (anything with `delete_before(cutoff,
    stream=...)`). Given one, the movement events that point into the deleted
    footage go with it, so the operator's list never offers to play a file that
    has been reclaimed. It is a parameter rather than an import because
    retention belongs to recording, and recording must work on a machine where
    detection was never turned on.
    """
    removed: list[Segment] = []
    for segment in plan.delete:
        try:
            unlink(segment.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # Locked or unreadable: leave the row and try again next pass. Retrying is
            # right - the alternative is dropping the row and losing track of a file
            # that still occupies the budget - but it must not fail silently forever.
            logger.warning("could not delete %s: %s", segment.path, exc)
            continue
        try:
            index.delete(segment.id)
        except Exception as exc:  # noqa: BLE001 - the file is already gone
            # The file has been unlinked; only the catalogue entry is left. If
            # this raised, the whole plan aborted on its first entry and the
            # same entry was retried forever - so a full disk could free exactly
            # one file, because freeing space required writing to the database
            # that was out of space. Skip the row and keep deleting.
            logger.warning("deleted %s but could not remove its index row: %s", segment.path, exc)
            continue
        removed.append(segment)
    if events is not None and removed:
        _reclaim_events(removed, events)
    return removed


def _reclaim_events(removed: list[Segment], events) -> None:
    """Drop the events whose footage has just gone, one stream at a time.

    Per stream, because retention reclaims each stream's oldest footage
    independently: the visible camera's recording of the same minutes may still
    be on disk, and its events still point at it.

    Keyed on what was *actually* removed rather than what was planned, so a
    locked file keeps its events - the footage is still there.

    A failure here is logged and swallowed. Freeing the disk is the job of this
    function; if it needed a working events.db to finish, a locked events.db
    would fill the disk and stop recording.
    """
    cutoffs: dict[str, float] = {}
    for segment in removed:
        cutoffs[segment.stream] = max(cutoffs.get(segment.stream, segment.end), segment.end)
    for stream, cutoff in cutoffs.items():
        try:
            gone = events.delete_before(cutoff, stream=stream)
        except Exception as exc:  # noqa: BLE001 - the footage is already gone
            logger.warning("could not remove events for %s: %s", stream, exc)
            continue
        if gone:
            logger.info("retention removed %d event(s) for %s", gone, stream)
