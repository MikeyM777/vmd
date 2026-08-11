"""The Update button: what it reports, and what it refuses to do."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from vmd.updater import Updater


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A real local checkout with a real upstream, so the pull is a real pull."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "--bare", "--initial-branch=main", cwd=origin)

    work = tmp_path / "work"
    git("clone", str(origin), str(work), cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    (work / "file.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-m", "first", cwd=work)
    git("push", "origin", "main", cwd=work)
    return work


def wait_for(updater: Updater, seconds: float = 30) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = updater.snapshot()
        if not state["running"]:
            return state
        time.sleep(0.1)
    raise AssertionError("the update never finished")


def test_a_zip_download_says_it_cannot_update(tmp_path: Path) -> None:
    """No .git means no way to pull, and the operator is told exactly that."""
    updater = Updater(tmp_path)
    info = updater.version()
    assert info["known"] is False
    assert "ZIP" in info["reason"]
    started, why_not = updater.start()
    assert not started
    assert why_not


def test_it_reports_the_version_it_is_running(checkout: Path) -> None:
    info = Updater(checkout).version()
    assert info["known"] is True
    assert info["can_update"] is True
    assert len(info["version"]) > 6


def test_up_to_date_is_reported_as_a_success_not_a_change(checkout: Path) -> None:
    updater = Updater(checkout)
    assert updater.start()[0]
    state = wait_for(updater)
    assert state["ok"] is True
    assert "up to date" in state["message"].lower()


def test_local_edits_are_refused_rather_than_discarded(checkout: Path) -> None:
    """The machine this runs on is not backed up. A pull that could throw away
    someone's edit is worse than no update button at all."""
    # Upstream moves on, and the same file is edited here.
    other = checkout.parent / "other"
    git("clone", str(checkout.parent / "origin"), str(other), cwd=checkout.parent)
    git("config", "user.email", "test@example.com", cwd=other)
    git("config", "user.name", "Test", cwd=other)
    (other / "file.txt").write_text("upstream\n", encoding="utf-8")
    git("add", "-A", cwd=other)
    git("commit", "-m", "upstream change", cwd=other)
    git("push", "origin", "main", cwd=other)

    (checkout / "file.txt").write_text("mine\n", encoding="utf-8")

    updater = Updater(checkout)
    assert updater.start()[0]
    state = wait_for(updater)
    assert state["ok"] is False
    assert "local edits" in state["message"] or "refused" in state["message"]
    assert (checkout / "file.txt").read_text(encoding="utf-8") == "mine\n", "the edit was destroyed"


def test_two_updates_at_once_are_refused(checkout: Path) -> None:
    updater = Updater(checkout)
    assert updater.start()[0]
    started, why_not = updater.start()
    assert not started
    assert "already running" in why_not
    wait_for(updater)


def test_the_output_shows_the_commands_that_were_run(checkout: Path) -> None:
    """An updater that hides what it executed cannot be trusted."""
    updater = Updater(checkout)
    updater.start()
    state = wait_for(updater)
    assert any("git pull" in line for line in state["output"])


def test_a_real_new_commit_is_pulled_and_reported(checkout: Path) -> None:
    """The case that matters: someone pushed, and this copy takes it."""
    other = checkout.parent / "publisher"
    git("clone", str(checkout.parent / "origin"), str(other), cwd=checkout.parent)
    git("config", "user.email", "test@example.com", cwd=other)
    git("config", "user.name", "Test", cwd=other)
    (other / "file.txt").write_text("second version\n", encoding="utf-8")
    (other / "added.txt").write_text("new file\n", encoding="utf-8")
    git("add", "-A", cwd=other)
    git("commit", "-m", "a change worth pulling", cwd=other)
    git("push", "origin", "main", cwd=other)

    before = Updater(checkout).version()["version"]

    updater = Updater(checkout)
    assert updater.start()[0]
    state = wait_for(updater)

    assert state["ok"] is True, state["message"]
    assert "start vmd.exe again" in state["message"].lower()
    assert (checkout / "file.txt").read_text(encoding="utf-8") == "second version\n"
    assert (checkout / "added.txt").exists(), "a new file did not arrive"
    assert updater.version()["version"] != before, "the reported version did not change"


def push_a_commit(checkout: Path, name: str = "publisher") -> None:
    other = checkout.parent / name
    git("clone", str(checkout.parent / "origin"), str(other), cwd=checkout.parent)
    git("config", "user.email", "test@example.com", cwd=other)
    git("config", "user.name", "Test", cwd=other)
    (other / "file.txt").write_text(f"changed by {name}\n", encoding="utf-8")
    git("add", "-A", cwd=other)
    git("commit", "-m", f"a change from {name}", cwd=other)
    git("push", "origin", "main", cwd=other)


def test_a_pull_that_exited_zero_but_changed_nothing_is_not_called_an_update(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code zero is not new code on the disk.

    Whether anything was pulled was decided by looking for the English words
    "Already up to date" in git's output. Git is translated, `pull.rebase` and
    a detached HEAD both produce output that says neither thing, and a
    filesystem that refused the write leaves git cheerful. Any of those had the
    console telling the operator to restart to run the new version, on a copy
    that was the same copy. The operator restarts, believes they are running
    the fix, and nothing anywhere contradicts them.

    The commit this copy is on before and after is the only thing that answers
    the question that was being asked.
    """
    updater = Updater(checkout)
    passthrough = updater._run

    def pull_that_did_nothing(command: list[str]) -> subprocess.CompletedProcess:
        if command[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="Updating 1234567..89abcde\nFast-forward\n", stderr=""
            )
        return passthrough(command)

    monkeypatch.setattr(updater, "_run", pull_that_did_nothing)
    assert updater.start()[0]
    state = wait_for(updater)

    assert state["ok"] is True
    assert "start vmd.exe again" not in state["message"].lower(), state["message"]
    assert "nothing changed" in state["message"].lower(), state["message"]


def test_the_update_says_which_version_it_moved_from_and_to(checkout: Path) -> None:
    """The evidence, not the claim. The operator can read it back over a phone."""
    push_a_commit(checkout)
    before = Updater(checkout)._head()
    assert before

    updater = Updater(checkout)
    assert updater.start()[0]
    state = wait_for(updater)

    assert state["ok"] is True, state["message"]
    assert state["from"] == before
    assert state["to"] and state["to"] != before
    assert state["to"][:7] in state["message"], state["message"]


def test_a_copy_whose_commit_cannot_be_read_is_not_reported_as_updated(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"I cannot tell" is a legitimate answer and a false "updated" is not."""
    updater = Updater(checkout)
    passthrough = updater._run

    def no_rev_parse(command: list[str]) -> subprocess.CompletedProcess:
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 128, stdout="", stderr="fatal: bad object")
        return passthrough(command)

    monkeypatch.setattr(updater, "_run", no_rev_parse)
    assert updater.start()[0]
    state = wait_for(updater)

    assert state["ok"] is None, "not knowing is not the same as it having worked"
    assert "cannot tell" in state["message"].lower(), state["message"]
