"""last_restart.json: when this console last restarted its camera."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from vmd.ptz.restart_record import FILENAME, path_beside, read, restarted_within, write


def test_it_lives_beside_the_settings(tmp_path: Path) -> None:
    assert path_beside(tmp_path / "settings.json") == tmp_path / FILENAME


def test_what_is_written_is_what_is_read(tmp_path: Path) -> None:
    path = tmp_path / FILENAME
    when = datetime(2026, 11, 11, 16, 0, 1)
    write(path, when, "scheduled: every day at 16:00")
    assert read(path) == when


def test_no_file_is_no_restart(tmp_path: Path) -> None:
    assert read(tmp_path / FILENAME) is None
    assert restarted_within(tmp_path / FILENAME, 300) is False


def test_a_broken_file_is_no_restart_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert read(path) is None


def test_restarted_within_goes_by_the_age_of_the_file(tmp_path: Path) -> None:
    path = tmp_path / FILENAME
    write(path, datetime(2026, 11, 11, 16, 0), "by hand")
    assert restarted_within(path, 300) is True
    old = time.time() - 600
    os.utime(path, (old, old))
    assert restarted_within(path, 300) is False
