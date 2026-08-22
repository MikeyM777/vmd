"""The check that decides whether an update is kept or thrown away."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run_selftest(settings: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "vmd.selftest", "--settings", str(settings)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_working_copy_passes_and_says_so(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"camera": {"host": "192.0.2.10"}}', encoding="utf-8")

    result = run_selftest(settings)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest ok" in result.stdout


def test_a_settings_file_that_will_not_parse_fails_the_test_with_the_reason(
    tmp_path: Path,
) -> None:
    """The one failure this catches that an import check cannot: an update that
    changed the settings model in a way this machine's own file no longer
    satisfies. The console would open on that and be unusable."""
    settings = tmp_path / "settings.json"
    settings.write_text("{ not json", encoding="utf-8")

    result = run_selftest(settings)

    assert result.returncode != 0
    assert "settings" in (result.stdout + result.stderr).lower()


def test_a_missing_settings_file_is_not_a_failure(tmp_path: Path) -> None:
    """A console that has never been set up has no settings file, and that is
    an ordinary state - `load_settings` answers it with defaults. Failing here
    would make the first update of a fresh install impossible."""
    result = run_selftest(tmp_path / "nothing.json")
    assert result.returncode == 0
