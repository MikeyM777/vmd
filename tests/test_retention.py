from vmd.storage.index import Segment
from vmd.storage.retention import plan_retention

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
