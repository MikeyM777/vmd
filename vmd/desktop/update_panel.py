"""The Update button, and the only control on this machine that changes it.

At the bottom of the Settings tab and not behind a fold: on an air-gapped
console this is now the whole of maintenance, and a control somebody has to
know about is a control somebody will not find.

Nothing here applies anything. It reads which version this is, what is on the
stick, and whether there is a version to go back to; the work is done by a
separate process - see `vmd/update/runner.py` - because the files being
replaced include this one.

Everything this panel reads about the software - VERSION, previous\\, the
status file under bin\\logs\\ - is read out of the install root, which is the
program. The settings file is the console's own and lives somewhere else
entirely: a multi-camera install runs one console per camera out of
cameras\\<name>\\settings.json against one copy of the program. So the two
paths are separate arguments and neither is derived from the other.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.update.apply import LOGS, MARKER, STATUS
from vmd.update.runner import TIMEOUT_SECONDS
from vmd.update.runner import start as start_update
from vmd.update.stick import look, removable_drives
from vmd.update.version import describe, read_version

logger = logging.getLogger(__name__)

WATCH_MS = 1000

# How long the panel will wait for the updater to say anything at all before it
# decides there is no updater. `runner.start` deletes the last update's status
# file before it spawns, so a process that dies in its first second - a copy
# that could not be made, an interpreter Windows refused - leaves no file
# behind and nothing to read. Without a deadline the panel sits on "the console
# will close and start again" for the rest of the day, in front of somebody
# whose console is not going to close. Ninety seconds because the first thing
# the updater does is write a note onto the stick, and a slow stick is still
# measured in seconds.
DEADLINE_SECONDS = 90


class UpdatePanel(QGroupBox):
    def __init__(
        self,
        root: Path,
        settings_path: Path,
        drives=removable_drives,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Software", parent)
        self._root = Path(root)
        self._settings_path = Path(settings_path)
        self._drives = drives
        # Named so a test can wind it forward. The alternative is a test that
        # really waits a minute and a half, which nobody would run.
        self.clock = time.monotonic

        outer = QVBoxLayout(self)

        self.this_system = QLabel()
        outer.addWidget(self.this_system)

        line = QHBoxLayout()
        self.stick_line = QLabel()
        self.stick_line.setWordWrap(True)
        line.addWidget(self.stick_line, 1)

        self.look_button = QPushButton("Look again")
        self.look_button.clicked.connect(self.look)
        line.addWidget(self.look_button)

        self.update_button = QPushButton("Update now")
        self.update_button.clicked.connect(self.update_now)
        line.addWidget(self.update_button)
        outer.addLayout(line)

        self.back_button = QPushButton("Go back")
        self.back_button.clicked.connect(self.go_back)
        outer.addWidget(self.back_button)

        self._state = None
        # When to give up on an updater that has written nothing, or None when
        # nothing is being waited for.
        self._deadline: float | None = None
        # Parented to the panel on purpose. A QTimer with no parent outlives the
        # widget whose method it calls, and a timeout delivered into a deleted
        # widget takes the console down rather than raising anything anybody
        # could read. Qt stops and deletes a child timer with its parent, which
        # is the whole of what stops this when the tab is destroyed.
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._read_status)

    # ------------------------------------------------------------- describing

    def previous_version(self) -> int | None:
        """The version to go back to, or None if there is not one.

        Ordinarily never this version. A rollback leaves the copy it put back
        where it was - nothing deletes previous\\7 - so after going back to
        VMD 7 the folder still offers VMD 7, and "Go back to VMD 7" printed on
        a console already running VMD 7 is a button that means nothing on the
        one panel where every control has to mean something.

        That filter is dropped the moment the marker is up, and dropping it is
        the whole of a fault that could leave this machine with no working
        control at all. VERSION is one of the files an update copies in, so an
        update cut off during the copy leaves VERSION still reading 7 with
        previous\\7 beside it: the filter then removed the only kept copy
        there was, and Go back disappeared underneath a line telling the
        operator to press it. While the marker is up, what VERSION says is not
        evidence of anything - it is a file that may or may not have been
        replaced yet - so the marker's own account is used instead. It says
        which version was going on, and a rollback's marker says which copy it
        was putting back; either is a better answer than the highest number in
        the folder, which on a machine that has been updated twice is the
        version before the one this update kept.
        """
        folder = self._root / "previous"
        if not folder.is_dir():
            return None
        versions = [int(item.name) for item in folder.iterdir() if item.name.isdigit()]
        if not versions:
            return None

        stopped = self.interrupted()
        if stopped is None:
            mine = read_version(self._root)
            kept = [version for version in versions if version != mine]
            return max(kept) if kept else None

        putting_back = stopped.get("kept")
        if isinstance(putting_back, int) and putting_back in versions:
            # A rollback that was cut off. The way out of it is to finish it.
            return putting_back
        going_on = stopped.get("to")
        kept = [version for version in versions if version != going_on]
        # `or versions`: an interrupted update always has a way out. A folder
        # that holds nothing but the version that was going on is not a state
        # anything writes, but answering None to it would be the dead end this
        # whole method exists to close.
        return max(kept or versions)

    def interrupted(self) -> dict | None:
        marker = self._root / LOGS / MARKER
        if not marker.is_file():
            return None
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def already_running(self) -> bool:
        """Whether an update started somewhere else is still going.

        One install can run two consoles - one per camera - out of one copy of
        the program, and both draw this panel. Two updates at once is the
        second one copying files in while the first is halfway through taking
        the backup it would need to undo them, so the second press is refused
        rather than served.

        A status file that has not been written to for longer than any of the
        updater's own steps can take was written by something that is no longer
        alive: an updater killed by a power cut before it reached the marker
        leaves exactly that, and read as "an update is running" it would refuse
        every future update on this machine for ever. TIMEOUT_SECONDS is the
        bound on the longest step there is, so past it the file is a leftover.
        """
        path = self._root / LOGS / STATUS
        # Absent and stale are both "nothing is running", and they are asked
        # first: below this, a file that cannot be read counts as a file
        # something is writing, and a file that is not there would have been
        # read as an update in progress on every machine that has never run
        # one.
        if not path.is_file() or self._stale(path):
            return False
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A file caught halfway through being written is a file something
            # is writing, which is the question that was asked.
            return True
        return isinstance(status, dict) and not status.get("finished")

    def _stale(self, path: Path) -> bool:
        """Whether nothing has written to that file for longer than a step.

        The one rule, in one place, because it is asked twice: of a status file
        found lying there when the panel is drawn, and of the one this panel is
        watching. Every subprocess the updater runs is bounded by
        TIMEOUT_SECONDS - that is what the constant is - so a file untouched
        for longer than that was written by a process that is no longer alive.
        A missing file is not stale, it is absent, and that is the deadline's
        question rather than this one's.
        """
        try:
            written = path.stat().st_mtime
        except OSError:
            return False
        return (time.time() - written) >= TIMEOUT_SECONDS

    def look(self) -> None:
        """Read everything and draw it. Cheap enough to call on every show."""
        if self._watch.isActive():
            # Something is being watched. Redrawing now would paint over the
            # line saying so and re-enable the button that would start a second
            # one - and the tab reloads itself for reasons that have nothing to
            # do with this box.
            return
        self.this_system.setText(f"This system: {describe(self._root)}")

        previous = self.previous_version()
        self.back_button.setVisible(previous is not None)
        self.back_button.setEnabled(True)
        if previous is not None:
            self.back_button.setText(f"Go back to VMD {previous}")

        # Asked before the marker is read, and that order is load-bearing: an
        # update that is running right now has its marker up, and read the
        # other way round the other console's panel calls a live update an
        # interrupted one and hands the operator a Go back button that would
        # put the old files over it.
        if self.already_running():
            self._state = None
            self.stick_line.setText(
                "An update is already running on this machine. Wait for it to "
                "finish - this console will close and start again by itself."
            )
            self.update_button.setEnabled(False)
            # Neither button, and Go back for the worse reason of the two: it
            # would put the old files back over an update that is halfway
            # through writing the new ones.
            self.back_button.setEnabled(False)
            return

        stopped = self.interrupted()
        if stopped is not None and previous is not None:
            self.stick_line.setText(self._interrupted_line(stopped, previous))
            self.update_button.setEnabled(False)
            return

        self._state = look(self._root, self._drives())
        message = self._state.message
        if stopped is not None:
            # Nothing kept, so Go back is not the way out and the stick is: an
            # interrupted update under a disabled Update button is a panel on
            # which nothing at all can be pressed.
            message = (
                f"{self._what_was_interrupted(stopped)} was interrupted before it "
                f"finished, and there is no kept copy on this machine to go back "
                f"to. Updating again from a stick is the way to put this right. "
                f"{message}"
            )
        self.stick_line.setText(message)
        self.update_button.setEnabled(self._state.kind == "ready")

    def _what_was_interrupted(self, stopped: dict) -> str:
        """"An update to VMD 8", or "An update" when the marker says nothing."""
        going_on = stopped.get("to")
        if stopped.get("rollback"):
            return f"Going back to VMD {going_on}" if isinstance(going_on, int) else "Going back"
        return f"An update to VMD {going_on}" if isinstance(going_on, int) else "An update"

    def _interrupted_line(self, stopped: dict, previous: int) -> str:
        """Both versions by name.

        "An update was interrupted" leaves the operator knowing neither which
        version this machine is now nor which one the button would bring back,
        which are the only two things they need in order to decide anything.
        """
        return (
            f"{self._what_was_interrupted(stopped)} was interrupted before it "
            f"finished. VMD {previous} is still on this machine - press Go "
            f"back to VMD {previous} to put it back."
        )

    # --------------------------------------------------------------- updating

    def update_now(self) -> None:
        if self._state is None or self._state.kind != "ready":
            return
        # Asked again here, and not only in `look`: the other console can have
        # started its update in the minutes since this panel was drawn, and the
        # button would still be live.
        if self.already_running():
            self.look()
            return
        started, why = self.start_update(self._state.stick)
        if not started:
            self.stick_line.setText(why)
            return
        self._wait_for_the_updater("Updating. The console will close and start again.")

    def start_update(self, stick: Path) -> tuple[bool, str]:
        """Overridden in tests. The real one starts a detached process."""
        return start_update(self._root, stick, self._settings_path)

    def go_back(self) -> None:
        previous = self.previous_version()
        if previous is None:
            return
        # The other console on this machine may have started an update since
        # this panel was drawn. Restoring the old files over an update that is
        # halfway through writing the new ones is the one combination that
        # leaves an install nothing can repair.
        if self.already_running():
            self.look()
            return
        answer = QMessageBox.question(
            self,
            "Go back to the previous version?",
            f"Put VMD {previous} back?\n\n"
            f"The console will close and start again. Your settings, cameras and "
            f"recordings are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.start_rollback(previous)

    def start_rollback(self, version: int) -> None:
        """Overridden in tests. The real one starts the same detached updater.

        The panel watches a rollback exactly as it watches an update: it is the
        same process writing the same status file, and the operator who pressed
        Go back has the same question - is anything happening?
        """
        from vmd.update.runner import start_rollback

        started, why = start_rollback(self._root, version, self._settings_path)
        if not started:
            self.stick_line.setText(why)
            return
        self._wait_for_the_updater(
            f"Putting VMD {version} back. The console will close and start again."
        )

    # ---------------------------------------------------------------- waiting

    def _wait_for_the_updater(self, message: str) -> None:
        """Shut the buttons, say what is happening, and start watching."""
        self.update_button.setEnabled(False)
        self.look_button.setEnabled(False)
        self.back_button.setEnabled(False)
        self.stick_line.setText(message)
        self._deadline = self.clock() + DEADLINE_SECONDS
        self._watch.start(WATCH_MS)

    def _read_status(self) -> None:
        path = self._root / LOGS / STATUS
        if not path.is_file():
            self._give_up_if_nothing_ever_started()
            return
        if self._stale(path):
            self._stop_watching_something_that_died()
            return
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A file caught halfway through being written, which happens: it is
            # rewritten on every step and read every second. The next tick has
            # the whole of it.
            return
        # Something is writing, so this is an updater that started. Whatever it
        # takes from here is its own business - a stick checksummed over USB 2
        # and a uv sync are both minutes, and neither is a reason to tell the
        # operator nothing happened.
        self._deadline = None
        if status.get("finished"):
            self._watch.stop()
            self.look_button.setEnabled(True)
            self.look()
        self.stick_line.setText(status.get("message") or status.get("step") or "")

    def _stop_watching_something_that_died(self) -> None:
        """Stop waiting on an updater that wrote a step and then stopped.

        The deadline covers an updater that never wrote a word; this covers the
        other half, and without it the panel watched a file nothing would ever
        touch again with all three buttons dead. What is said afterwards is
        whatever `look` finds - if the marker is up it names the version that
        was going on and the one to press Go back for, which is exactly the
        state this ends in - with one sentence in front of it saying why the
        console is still here.
        """
        self._watch.stop()
        self._deadline = None
        self.look_button.setEnabled(True)
        self.back_button.setEnabled(True)
        self.look()
        self.stick_line.setText(f"The updater stopped without finishing. {self.stick_line.text()}")

    def _give_up_if_nothing_ever_started(self) -> None:
        """Stop waiting on an updater that has never written a word.

        Nothing has been changed and that is said plainly, because the operator
        is standing in front of a console that was supposed to close itself and
        did not, and the question is whether the machine is now half-updated.
        It is not: the updater writes its first status before it touches
        anything at all.
        """
        if self._deadline is None or self.clock() < self._deadline:
            return
        self._watch.stop()
        self._deadline = None
        self.look_button.setEnabled(True)
        self.back_button.setEnabled(True)
        self.update_button.setEnabled(self._state is not None and self._state.kind == "ready")
        self.stick_line.setText("The updater did not start. Nothing has been changed.")
