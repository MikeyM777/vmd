from vmd.storage.index import SegmentIndex
from vmd.storage.retention import apply_plan, plan_retention

GB = 1024**3


def make_file(tmp_path, name, size=1024):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def test_deletes_files_and_index_rows(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    paths = []
    for i in range(4):
        path = make_file(tmp_path, f"seg{i}.mp4")
        paths.append(path)
        index.add("thermal", str(path), start=i * 300.0, end=i * 300.0 + 300.0, size_bytes=GB)

    plan = plan_retention(
        index.all(), now=10000.0, budget_bytes=2 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    removed = apply_plan(plan, index)

    assert removed == 2
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert [s.path for s in index.all()] == [str(paths[2]), str(paths[3])]
    index.close()


def test_missing_file_still_clears_the_index_row(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    index.add("thermal", str(tmp_path / "ghost.mp4"), 0.0, 300.0, size_bytes=GB)
    plan = plan_retention(
        index.all(), now=10000.0, budget_bytes=1, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert apply_plan(plan, index) == 1
    assert index.all() == []
    index.close()


def test_undeletable_file_keeps_its_row_for_a_later_attempt(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    path = make_file(tmp_path, "locked.mp4")
    index.add("thermal", str(path), 0.0, 300.0, size_bytes=GB)
    plan = plan_retention(
        index.all(), now=10000.0, budget_bytes=1, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )

    def refuse(_path):
        raise PermissionError("file is in use")

    assert apply_plan(plan, index, unlink=refuse) == 0
    assert len(index.all()) == 1
    index.close()


def test_empty_plan_does_nothing(tmp_path):
    index = SegmentIndex(tmp_path / "segments.db")
    path = make_file(tmp_path, "keep.mp4")
    index.add("thermal", str(path), 0.0, 300.0, size_bytes=1024)
    plan = plan_retention(
        index.all(), now=400.0, budget_bytes=100 * GB, budget_enabled=True,
        retention_days=None, warn_at_fraction=0.9, bytes_per_second=1000.0,
    )
    assert apply_plan(plan, index) == 0
    assert path.exists()
    index.close()
