import json

from vmd.storage.index import Segment
from vmd.storage.retention import ClockWatch, plan_retention

HOUR = 3600.0
DAY = 86400.0
GB = 1024**3


def segment(index, start, size_bytes=GB, stream="thermal"):
    return Segment(
        id=index,
        stream=stream,
        path=f"/rec/{index}.mp4",
        start=start,
        end=start + 300.0,
        size_bytes=size_bytes,
    )


def test_nothing_to_do_when_under_budget_and_within_age():
    segments = [segment(1, 0.0), segment(2, 300.0)]
    plan = plan_retention(
        segments, now=600.0, budget_bytes=100 * GB, budget_enabled=True,
        retention_days=30, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == []
    assert plan.warning is None


def test_age_rule_deletes_old_segments():
    segments = [segment(1, 0.0), segment(2, 20 * DAY)]
    plan = plan_retention(
        segments, now=21 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=13, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert [s.id for s in plan.delete] == [1]


def test_age_rule_disabled_when_days_is_none():
    segments = [segment(1, 0.0)]
    plan = plan_retention(
        segments, now=999 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == []


def test_budget_rule_deletes_oldest_first():
    segments = [segment(i, i * 300.0) for i in range(1, 6)]  # 5 GB total
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=3 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert [s.id for s in plan.delete] == [1, 2]
    assert plan.used_bytes == 5 * GB


def test_budget_rule_disabled_leaves_everything():
    segments = [segment(i, i * 300.0) for i in range(1, 6)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=1 * GB, budget_enabled=False,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == []


def test_both_rules_together_do_not_double_count():
    segments = [segment(1, 0.0), segment(2, 20 * DAY), segment(3, 20 * DAY + 300)]
    plan = plan_retention(
        segments, now=21 * DAY, budget_bytes=1 * GB, budget_enabled=True,
        retention_days=13, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    deleted = [s.id for s in plan.delete]
    assert deleted == sorted(set(deleted))
    assert 1 in deleted


def test_warning_appears_near_the_budget():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]  # 9 GB
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    assert plan.delete == []
    assert plan.warning is not None
    assert "will be deleted" in plan.warning


def test_no_warning_when_comfortably_under_budget():
    segments = [segment(1, 0.0)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=100 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.warning is None


def test_no_warning_when_budget_rule_is_off():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=False,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    assert plan.warning is None


def test_used_and_budget_are_reported():
    segments = [segment(1, 0.0, size_bytes=2 * GB)]
    plan = plan_retention(
        segments, now=10.0, budget_bytes=5 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.used_bytes == 2 * GB
    assert plan.budget_bytes == 5 * GB


def test_zero_write_rate_does_not_divide_by_zero():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=0.0,
    )
    assert plan.warning is not None


def test_warning_still_appears_while_deleting():
    # Once the budget is reached the system deletes on almost every pass. If the
    # warning were suppressed during deletion the operator would never be told that
    # footage is being lost.
    segments = [segment(i, i * 300.0) for i in range(1, 11)]  # 10 GB
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=5 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    assert plan.delete != []
    assert plan.warning is not None
    assert "being deleted continuously" in plan.warning


def test_deleting_warning_names_the_oldest_surviving_footage():
    segments = [segment(i, i * 300.0) for i in range(1, 11)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=5 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    surviving_ids = {s.id for s in segments} - {s.id for s in plan.delete}
    assert surviving_ids  # something is kept
    assert "Nothing before" in plan.warning


def test_warning_includes_the_year():
    segments = [segment(i, i * 300.0) for i in range(1, 10)]
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=10 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=GB / HOUR,
    )
    assert "1970" in plan.warning  # segment starts are epoch-relative in these tests


# --------------------------------------------------------------------------
# The clock, which on this machine is typed in by a person.
#
# The laptop is offline: there is no NTP, so nothing corrects a date entered
# wrong and nothing contradicts it. An operator who has asked for "delete
# footage older than 30 days" and then sets the year wrong used to lose every
# recording on the machine in the next retention pass - files unlinked, index
# rows deleted, movement events dropped with them. There is no undo.
# --------------------------------------------------------------------------


def test_the_age_rule_is_measured_from_the_newest_footage_not_from_the_clock():
    """A date typed a year ahead must not make the whole archive look ancient.

    "Keep thirty days" is a statement about the recordings, and the recordings
    carry their own timestamps. Measuring from the newest of them is the same
    answer as measuring from the clock whenever the clock is right, and a far
    safer one when it is not.
    """
    segments = [segment(1, 0.0), segment(2, 300.0)]
    plan = plan_retention(
        segments, now=400 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=30, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert plan.delete == [], "a clock a year out deleted the entire archive"


def test_the_age_rule_does_not_run_when_the_clock_cannot_be_believed():
    segments = [segment(i, i * DAY) for i in range(1, 40)]
    plan = plan_retention(
        segments, now=39 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=1, warn_at_fraction=0.9, bytes_per_second=1000.0,
        clock_reason="the clock moved 400 days that time did not",
    )
    assert plan.delete == []
    assert plan.declined and "400 days" in plan.declined


def test_the_budget_rule_runs_whatever_the_clock_is_doing():
    """A full disk still has to be reclaimed. The budget rule reads no clock."""
    segments = [segment(i, i * 300.0) for i in range(1, 6)]  # 5 GB
    plan = plan_retention(
        segments, now=10000.0, budget_bytes=3 * GB, budget_enabled=True,
        retention_days=30, warn_at_fraction=0.9, bytes_per_second=1000.0,
        clock_reason="the clock cannot be believed",
    )
    assert [s.id for s in plan.delete] == [1, 2]


def test_the_age_rule_will_not_empty_the_archive_in_one_pass():
    """Deleting a hundred segments at once is never the ordinary working of an
    age rule; it is a clock, or a policy that has just been shortened. Either
    way it is worth doing slowly and saying out loud."""
    segments = [segment(i, i * DAY) for i in range(1, 101)]
    plan = plan_retention(
        segments, now=100 * DAY, budget_bytes=100 * GB, budget_enabled=False,
        retention_days=1, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert 0 < len(plan.delete) < 98
    assert plan.declined and "98" in plan.declined


def test_a_clock_that_keeps_step_with_time_is_believed(tmp_path):
    ticks = {"now": 0.0}
    watch = ClockWatch(tmp_path / "clock.json", monotonic=lambda: ticks["now"])
    verdicts = []
    for step in range(4):
        ticks["now"] = step * 60.0
        verdicts.append(watch.observe(1_000_000.0 + step * 60.0))
    assert verdicts[0].reason  # nothing to compare the first one against
    assert verdicts[-1].trusted
    assert verdicts[-1].reason == ""


def test_a_date_typed_a_year_ahead_is_not_believed(tmp_path):
    ticks = {"now": 0.0}
    watch = ClockWatch(tmp_path / "clock.json", monotonic=lambda: ticks["now"])
    for step in range(4):
        ticks["now"] = step * 60.0
        watch.observe(1_000_000.0 + step * 60.0)

    ticks["now"] = 240.0
    jumped = watch.observe(1_000_000.0 + 365 * DAY)
    assert jumped.trusted is False
    assert "365" in jumped.reason or "day" in jumped.reason


def test_belief_comes_back_only_after_the_new_time_has_been_agreed(tmp_path):
    ticks = {"now": 0.0}
    watch = ClockWatch(tmp_path / "clock.json", monotonic=lambda: ticks["now"])
    watch.observe(1000.0)
    ticks["now"] = 60.0
    watch.observe(1060.0)
    ticks["now"] = 120.0
    assert watch.observe(1120.0).trusted

    ticks["now"] = 180.0
    assert watch.observe(1120.0 + 400 * DAY).trusted is False
    ticks["now"] = 240.0
    first_agreement = watch.observe(1180.0 + 400 * DAY)
    assert first_agreement.trusted is False, "one pass is not agreement"
    ticks["now"] = 300.0
    assert watch.observe(1240.0 + 400 * DAY).trusted is True


def test_the_clock_is_still_watched_across_a_restart(tmp_path):
    """The reboot is the whole scenario: a Windows update, a power cut, and an
    operator who sets the date afterwards. A watch that forgot everything it
    knew at each restart would never catch it."""
    path = tmp_path / "clock.json"
    ticks = {"now": 0.0}
    watch = ClockWatch(path, monotonic=lambda: ticks["now"])
    for step in range(3):
        ticks["now"] = step * 60.0
        watch.observe(1_000_000.0 + step * 60.0)
    assert json.loads(path.read_text(encoding="utf-8"))["seen_at"] == 1_000_120.0

    restarted = ClockWatch(path, monotonic=lambda: 0.0)
    verdict = restarted.observe(1_000_120.0 - 400 * DAY)
    assert verdict.trusted is False, "a clock set back a year across a restart"
    assert "moved" in verdict.reason, (
        f"it declined for some other reason than the clock: {verdict.reason}"
    )


def test_a_restart_after_an_ordinary_shutdown_is_believed(tmp_path):
    """A machine that was off for an hour must not have retention suspended:
    silently keeping everything until the disk fills is its own failure."""
    path = tmp_path / "clock.json"
    ticks = {"now": 0.0}
    watch = ClockWatch(path, monotonic=lambda: ticks["now"])
    for step in range(3):
        ticks["now"] = step * 60.0
        watch.observe(1_000_000.0 + step * 60.0)

    restarted = ClockWatch(path, monotonic=lambda: 0.0)
    assert restarted.observe(1_000_120.0 + 3600.0).trusted is True
