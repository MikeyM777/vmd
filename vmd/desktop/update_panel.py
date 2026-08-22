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
        """The version kept by the last update, or None if there is not one.

        Never this version. A rollback leaves the copy it put back where it
        was - nothing deletes previous\\7 - so after going back to VMD 7 the
        folder still offers VMD 7, and "Go back to VMD 7" printed on a console
        already running VMD 7 is a button that means nothing on the one panel
        where every control has to mean something.
        """
        folder = self._root / "previous"
        if not folder.is_dir():
            return None
        mine = read_version(self._root)
        versions = [
            int(item.name)
            for item in folder.iterdir()
            if item.name.isdigit() and int(item.name) != mine
        ]
        return max(versions) if versions else None

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
        try:
            written = path.stat().st_mtime
        except OSError:
            return False
        if (time.time() - written) >= TIMEOUT_SECONDS:
            return False
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A file caught halfway through being written is a file something
            # is writing, which is the question that was asked.
            return True
        return isinstance(status, dict) and not status.get("finished")

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

        stopped = self.interrupted()
        if stopped is not None:
            self.stick_line.setText(
                "An update was interrupted before it finished. The version that "
                "was here has been kept - use Go back if this console is not "
                "behaving."
            )
            self.update_button.setEnabled(False)
            return

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

        self._state = look(self._root, self._drives())
        self.stick_line.setText(self._state.message)
        self.update_button.setEnabled(self._state.kind == "ready")

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
