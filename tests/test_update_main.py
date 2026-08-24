"""The thin wiring in vmd.update.main - and copy-only in particular.

main() itself starts real processes (it kills the console and starts it again),
so what is tested here is only what it decides: which of run / go_back it calls,
and with what. The dangerous three - stop, sync, selftest - are captured rather
than run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vmd.update import main as main_module
from vmd.update.apply import Report


def a_stick(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files").mkdir()
    (folder / "update.json").write_text('{"version": 8}', encoding="utf-8")
    (folder / "manifest.json").write_text('{"files": []}', encoding="utf-8")
    return folder


def test_copy_only_needs_no_settings_file(tmp_path: Path, monkeypatch) -> None:
    """The one mode that does not read settings must not demand the argument.

    The applier that drives copy-only does not know which of a machine's several
    consoles it is for - it restarts them itself, by their own layout - so it
    has no single settings path to hand in, and must not be made to invent one.
    """
    stick = a_stick(tmp_path / "E")
    seen = {}

    def fake_run(root, stick_, **kwargs):
        seen.update(kwargs)
        seen["called"] = True
        return Report(ok=True, message="Updated to VMD 8.")

    monkeypatch.setattr(main_module, "run", fake_run)
    # start_console must never be reached in copy-only; if it were, this would
    # catch it starting a process.
    started = {"popen": False}
    monkeypatch.setattr(
        main_module.subprocess, "Popen", lambda *a, **k: started.__setitem__("popen", True)
    )

    code = main_module.main(["--root", str(tmp_path / "VMD"), "--stick", str(stick), "--copy-only"])

    assert code == 0
    assert seen.get("called") is True
    assert started["popen"] is False  # the applier restarts, not this process


def test_copy_only_hands_run_a_sync_and_selftest_that_do_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole point of the mode: the backup, marker and rollback are still
    apply.run's, but the sync and the self-test - the two steps that can fail on
    a machine whose environment is subtly broken - are made to pass."""
    stick = a_stick(tmp_path / "E")
    captured = {}

    def fake_run(root, stick_, *, machine, when, stop, sync, selftest):
        captured["sync"] = sync
        captured["selftest"] = selftest
        return Report(ok=True)

    monkeypatch.setattr(main_module, "run", fake_run)
    monkeypatch.setattr(main_module.subprocess, "Popen", lambda *a, **k: None)

    main_module.main(["--root", str(tmp_path / "VMD"), "--stick", str(stick), "--copy-only"])

    assert captured["sync"]("anything") == (True, "")
    assert captured["selftest"]() == (True, "")


def test_the_ordinary_update_still_insists_on_settings(tmp_path: Path) -> None:
    """Dropping the requirement for copy-only must not drop it for the path that
    genuinely runs a self-test through the settings file."""
    stick = a_stick(tmp_path / "E")
    with pytest.raises(SystemExit):
        main_module.main(["--root", str(tmp_path / "VMD"), "--stick", str(stick)])
