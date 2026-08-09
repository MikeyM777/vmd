"""Deciding what footage to delete, and warning before it happens."""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from vmd.storage.index import Segment, SegmentIndex

logger = logging.getLogger(__name__)

DAY_SECONDS = 86400.0


@dataclass
class RetentionPlan:
    delete: list[Segment] = field(default_factory=list)
    warning: str | None = None
    used_bytes: int = 0
    budget_bytes: int = 0


def plan_retention(
    segments: list[Segment],
    now: float,
    budget_bytes: int,
    budget_enabled: bool,
    retention_days: int | None,
    warn_at_fraction: float,
    bytes_per_second: float,
) -> RetentionPlan:
    """Decide which segments to remove. Pure: no filesystem, no clock, no side effects.

    Two independent rules, either of which may be disabled:
      age    - remove anything that ended more than `retention_days` ago
      budget - while the total exceeds `budget_bytes`, remove the oldest

    Sorting is deliberately defensive rather than assumed. It is close to free because
    callers pass SegmentIndex.all(), which is already ordered by the same key.
    """
    ordered = sorted(segments, key=lambda s: (s.start, s.id))
    used_bytes = sum(s.size_bytes for s in ordered)
    plan = RetentionPlan(used_bytes=used_bytes, budget_bytes=budget_bytes)

    doomed_ids: set[int] = set()

    if retention_days is not None:
        cutoff = now - retention_days * DAY_SECONDS
        for segment in ordered:
            if segment.end < cutoff:
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
        edge = (
            datetime.datetime.fromtimestamp(kept.start).strftime("%d %B %Y %H:%M")
            if kept
            else "everything"
        )
        return (
            f"Storage {percent:.0f}% full. Oldest footage is being deleted continuously "
            f"to keep recording. Nothing before {edge} is still held."
        )

    oldest = ordered[0] if ordered else None
    when = (
        datetime.datetime.fromtimestamp(oldest.start).strftime("%d %B %Y")
        if oldest
        else "the oldest footage"
    )
    headroom = max(budget_bytes - used_bytes, 0)
    if bytes_per_second > 0:
        hours = headroom / (bytes_per_second * 3600.0)
        timing = f"in about {hours:.0f} hours" if hours >= 1 else "within the hour"
    else:
        timing = "once recording resumes"
    return f"Storage {percent:.0f}% full. Footage from {when} will be deleted {timing}."


def apply_plan(
    plan: RetentionPlan,
    index: SegmentIndex,
    unlink: Callable[[str], None] = os.unlink,
) -> list[Segment]:
    """Delete the planned segments from disk and from the index.

    Returns the segments actually removed, which is not always everything that was
    planned: a locked file is left for a later attempt. Callers need to know which
    ones really went, not just how many.

    A file that is already gone is not an error - the index row is still removed, so the
    catalogue converges on the truth rather than accumulating dead entries.
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
    return removed
