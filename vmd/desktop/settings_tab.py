"""Everything the operator configures, and nothing that is not configuration.

A save either writes exactly what is on screen or writes nothing and says why.
Silently dropping a field the operator just typed - which the browser form did
with stream names - is worse than refusing.

Two rules follow from that, and both are load-bearing:

* The stream rows are real widgets, and `streams()` reads them. The browser form
  rendered two fixed rows called "thermal" and "visible", so a camera whose
  streams were called anything else lost them the moment anyone pressed Save.
* A save starts from the settings that were loaded, so the fields this form does
  not show - the link ceiling, the video mode, the segment length - survive it.
  Resetting them to their defaults would be the same failure one field along.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import PALETTE
from vmd.settings import Settings, load_settings, save_settings

logger = logging.getLogger(__name__)

# name, url, enabled, reader
StreamRow = tuple[str, str, bool, str]

READERS = ["auto", "ffmpeg"]


class StreamRowWidget(QWidget):
    """One stream, as the operator sees it: a name, an address, whether it is
    recorded, and which client reads it. Nothing about it is fixed - a camera
    calls its streams whatever it likes and the form has to keep up."""

    def __init__(
        self,
        name: str = "",
        url: str = "",
        enabled: bool = True,
        reader: str = "auto",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.name_field = QLineEdit(name)
        self.name_field.setPlaceholderText("name")
        self.name_field.setFixedWidth(140)

        self.url_field = QLineEdit(url)
        self.url_field.setPlaceholderText("rtsp://address/path")

        self.record_field = QCheckBox("record")
        self.record_field.setChecked(enabled)

        self.reader_field = QComboBox()
        self.reader_field.addItems(READERS)
        self.reader_field.setCurrentText(reader if reader in READERS else "auto")

        self.remove_button = QPushButton("Remove")

        layout.addWidget(self.name_field)
        layout.addWidget(self.url_field, 1)
        layout.addWidget(self.record_field)
        layout.addWidget(self.reader_field)
        layout.addWidget(self.remove_button)

    def values(self) -> StreamRow:
        return (
            self.name_field.text().strip(),
            self.url_field.text().strip(),
            self.record_field.isChecked(),
            self.reader_field.currentText(),
        )


class _ToolSignals(QObject):
    """A background tool talking back to the window it cannot touch directly."""

    progress = Signal(str)
    done = Signal(list)


class _ToolJob(QRunnable):
    def __init__(self, work, signals: _ToolSignals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

    def run(self) -> None:
        try:
            lines = list(self._work())
        except Exception as exc:  # noqa: BLE001 - a failed tool must not end the console
            logger.exception("a camera tool failed")
            lines = [f"That did not finish: {exc}"]
        self._signals.done.emit(lines)


class SettingsTab(QWidget):
    def __init__(
        self,
        settings_path: str | Path,
        tools: "CameraTools | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path)
        self.message = ""
        # What was on disk when this form was filled. A save is this, with the
        # form's fields written over it, so nothing off-screen is lost.
        self._loaded = Settings()
        self._rows: list[StreamRowWidget] = []
        self._tools = tools
        # One at a time, and never on the UI thread: finding the right path
        # probes two dozen addresses and takes up to a minute. A console that
        # stops repainting for a minute is a console the operator restarts.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._running: list[_ToolSignals] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        camera_box = QGroupBox("Camera")
        camera_form = QFormLayout(camera_box)
        self._host = QLineEdit()
        self._username = QLineEdit()
        # Shown, never masked: this machine is offline and physically controlled,
        # and the failure this form actually suffers is a typo nobody can see.
        self._password = QLineEdit()
        camera_form.addRow("Address", self._host)
        camera_form.addRow("Username", self._username)
        camera_form.addRow("Password", self._password)
        layout.addWidget(camera_box)

        streams_box = QGroupBox("Streams")
        streams_outer = QVBoxLayout(streams_box)
        self._streams_layout = QVBoxLayout()
        self._streams_layout.setSpacing(4)
        streams_outer.addLayout(self._streams_layout)
        self.add_stream_button = QPushButton("Add a stream")
        self.add_stream_button.clicked.connect(lambda: self.add_stream_row())
        streams_outer.addWidget(self.add_stream_button)
        layout.addWidget(streams_box)

        storage_box = QGroupBox("Storage")
        storage_form = QFormLayout(storage_box)
        self._root = QLineEdit()
        self._budget = QLineEdit()
        self._days = QLineEdit()
        self._days.setPlaceholderText("empty means never delete by age")
        storage_form.addRow("Folder", self._root)
        storage_form.addRow("Budget (GB)", self._budget)
        storage_form.addRow("Delete older than (days)", self._days)
        layout.addWidget(storage_box)

        radio_box = QGroupBox("Radio")
        radio_form = QFormLayout(radio_box)
        self._radio_host = QLineEdit()
        self._radio_user = QLineEdit()
        self._radio_password = QLineEdit()
        radio_form.addRow("Address", self._radio_host)
        radio_form.addRow("Username", self._radio_user)
        radio_form.addRow("Password", self._radio_password)
        layout.addWidget(radio_box)

        tools_box = QGroupBox("The camera")
        tools_outer = QVBoxLayout(tools_box)
        tools_buttons = QHBoxLayout()
        self.test_button = QPushButton("Test the camera")
        self.test_button.clicked.connect(self.test_camera)
        self.find_button = QPushButton("Find the right path")
        self.find_button.clicked.connect(self.find_paths)
        self.fit_button = QPushButton("Fit the camera to the link")
        self.fit_button.clicked.connect(self.fit_to_link)
        self.report_button = QPushButton("Save a report")
        self.report_button.clicked.connect(lambda: self.save_report())
        for button in (self.test_button, self.find_button, self.fit_button, self.report_button):
            tools_buttons.addWidget(button)
        tools_buttons.addStretch(1)
        tools_outer.addLayout(tools_buttons)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setMinimumHeight(160)
        tools_outer.addWidget(self._output)
        layout.addWidget(tools_box)

        self._message = QLabel("")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch(1)

    # ------------------------------------------------------------- properties

    @property
    def camera_host(self) -> str:
        return self._host.text()

    @camera_host.setter
    def camera_host(self, value: str) -> None:
        self._host.setText(str(value))

    @property
    def camera_username(self) -> str:
        return self._username.text()

    @camera_username.setter
    def camera_username(self, value: str) -> None:
        self._username.setText(str(value))

    @property
    def camera_password(self) -> str:
        return self._password.text()

    @camera_password.setter
    def camera_password(self, value: str) -> None:
        self._password.setText(str(value))

    @property
    def budget_gb(self) -> str:
        return self._budget.text()

    @budget_gb.setter
    def budget_gb(self, value: str) -> None:
        self._budget.setText(str(value))

    @property
    def storage_root(self) -> str:
        return self._root.text()

    @storage_root.setter
    def storage_root(self, value: str) -> None:
        self._root.setText(str(value))

    @property
    def retention_days(self) -> str:
        return self._days.text()

    @retention_days.setter
    def retention_days(self, value: str) -> None:
        self._days.setText(str(value))

    @property
    def radio_host(self) -> str:
        return self._radio_host.text()

    @radio_host.setter
    def radio_host(self, value: str) -> None:
        self._radio_host.setText(str(value))

    @property
    def radio_username(self) -> str:
        return self._radio_user.text()

    @radio_username.setter
    def radio_username(self, value: str) -> None:
        self._radio_user.setText(str(value))

    @property
    def radio_password(self) -> str:
        return self._radio_password.text()

    @radio_password.setter
    def radio_password(self, value: str) -> None:
        self._radio_password.setText(str(value))

    def credential_fields(self) -> list[QLineEdit]:
        """Every field holding a password. They are all plain text on purpose."""
        return [self._password, self._radio_password]

    # ---------------------------------------------------------------- streams

    def add_stream_row(
        self, name: str = "", url: str = "", enabled: bool = True, reader: str = "auto"
    ) -> StreamRowWidget:
        row = StreamRowWidget(name, url, enabled, reader)
        row.remove_button.clicked.connect(lambda: self.remove_stream_row(row))
        self._rows.append(row)
        self._streams_layout.addWidget(row)
        return row

    def remove_stream_row(self, row: StreamRowWidget) -> None:
        if row not in self._rows:
            return
        self._rows.remove(row)
        self._streams_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def stream_rows(self) -> list[StreamRowWidget]:
        return list(self._rows)

    def set_streams(self, rows: list[StreamRow]) -> None:
        for row in list(self._rows):
            self.remove_stream_row(row)
        for name, url, enabled, reader in rows:
            self.add_stream_row(name, url, enabled, reader)

    def streams(self) -> list[StreamRow]:
        """What is on screen. Never a remembered list: the widgets are the truth."""
        return [row.values() for row in self._rows]

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        settings = load_settings(self.settings_path)
        self._loaded = settings
        self.camera_host = settings.camera.host
        self.camera_username = settings.camera.username
        self.camera_password = settings.camera.password
        self.storage_root = str(settings.storage.root)
        self.budget_gb = str(settings.storage.budget_gb)
        self.retention_days = (
            "" if settings.storage.retention_days is None else str(settings.storage.retention_days)
        )
        self.radio_host = settings.radio.host
        self.radio_username = settings.radio.username
        self.radio_password = settings.radio.password
        self.set_streams(
            [(s.name, s.url, s.enabled, s.reader) for s in settings.camera.streams]
        )
        self._set_message("")

    # ------------------------------------------------------------------ save

    def save(self) -> bool:
        settings = self.settings_from_form()
        if settings is None:
            return False

        try:
            save_settings(settings, self.settings_path)
        except OSError as exc:
            self._set_message(f"Could not write the settings file: {exc}")
            return False

        self._loaded = settings
        self._set_message("Saved.")
        return True

    def settings_from_form(self) -> Settings | None:
        """The form as a Settings, or None with `self.message` saying why not."""
        problem = self._problem()
        if problem:
            self._set_message(problem)
            return None

        payload = self._loaded.model_dump()
        payload["camera"] = dict(payload.get("camera", {}))
        payload["camera"].update(
            host=self.camera_host.strip(),
            username=self.camera_username.strip(),
            password=self.camera_password,
            streams=[
                {"name": name, "url": url, "enabled": enabled, "reader": reader}
                for name, url, enabled, reader in self.streams()
            ],
        )
        radio_host = self.radio_host.strip()
        payload["radio"] = dict(payload.get("radio", {}))
        payload["radio"].update(
            host=radio_host,
            username=self.radio_username.strip(),
            password=self.radio_password,
            # A radio with no address cannot be asked anything, so it is off.
            enabled=bool(radio_host),
        )
        payload["storage"] = dict(payload.get("storage", {}))
        payload["storage"].update(
            root=self.storage_root.strip() or "recordings",
            budget_gb=self.budget_gb.strip() or "100",
            retention_days=self.retention_days.strip() or None,
        )

        try:
            return Settings.model_validate(payload)
        except ValidationError as exc:
            self._set_message(_first_problem(exc))
            return None

    def _problem(self) -> str:
        seen: set[str] = set()
        for name, url, enabled, _reader in self.streams():
            if enabled and not url:
                return f'"{name or "A stream"}" is ticked to record but has no address.'
            if url and not name:
                return "A stream has an address but no name."
            if name and name in seen:
                return f'Two streams are both called "{name}".'
            if name:
                seen.add(name)
        return ""

    def _set_message(self, text: str) -> None:
        self.message = text
        self._message.setText(text)
        colour = PALETTE["muted"] if text in ("", "Saved.") else PALETTE["warn"]
        self._message.setStyleSheet(f"color: {colour};")

    # ----------------------------------------------------------- camera tools

    def output_text(self) -> str:
        return self._output.toPlainText()

    def test_camera(self) -> None:
        self._start(self.test_button, "Testing the camera", lambda tools, s: tools.diagnose(s))

    def find_paths(self) -> None:
        self._start(
            self.find_button,
            "Trying the common paths. This takes up to a minute.",
            lambda tools, s: tools.find_paths(s),
        )

    def fit_to_link(self) -> None:
        self._start(
            self.fit_button,
            "Asking the camera to fit the link",
            lambda tools, s: tools.fit_to_link(s),
        )

    def save_report(self, path: str | Path | None = None) -> None:
        """Write everything about this installation to a file that can be sent on."""
        if path is None:
            chosen, _filter = QFileDialog.getSaveFileName(
                self, "Save a report", "vmd-report.txt", "Text files (*.txt)"
            )
            if not chosen:
                return
            path = chosen
        target = Path(path)

        def work(tools: "CameraTools", settings: Settings) -> list[str]:
            written = tools.write_report(settings, target, extra=_report_header(settings))
            return [f"Report written to {written}"]

        self._start(self.report_button, "Writing the report", work)

    def _start(self, button: QPushButton, heading: str, work) -> None:
        """Run one camera tool off the UI thread and print what it says.

        The form is turned into settings first: there is nothing worth asking a
        camera while the address on screen is not one the console could save.
        """
        settings = self.settings_from_form()
        if settings is None:
            self._output.setPlainText(f"Fix this first: {self.message}")
            return

        tools = self._camera_tools(settings)
        signals = _ToolSignals()
        signals.progress.connect(self._append_line)
        signals.done.connect(lambda lines: self._tool_finished(button, signals, lines))
        tools.on_progress = signals.progress.emit

        self._running.append(signals)
        button.setEnabled(False)
        self._output.setPlainText(heading)
        self._pool.start(_ToolJob(lambda: work(tools, settings), signals))

    def _append_line(self, line: str) -> None:
        self._output.appendPlainText(line)

    def _tool_finished(self, button: QPushButton, signals: _ToolSignals, lines: list) -> None:
        for line in lines:
            self._output.appendPlainText(str(line))
        button.setEnabled(True)
        if signals in self._running:
            self._running.remove(signals)

    def _camera_tools(self, settings: Settings) -> "CameraTools":
        if self._tools is not None:
            return self._tools
        # Imported here so that opening the console does not pay for the camera
        # stack, and so this module stays testable with nothing installed.
        from vmd.ptz.service import PtzService
        from vmd.streaming.diagnose import diagnose, find_paths

        return CameraTools(ptz=PtzService(settings), find_paths=find_paths, diagnose=diagnose)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Let a running tool finish before the widget it reports to disappears."""
        self._pool.waitForDone(5000)
        super().closeEvent(event)


def _report_header(settings: Settings) -> list[str]:
    """The context that a report is useless without."""
    return [
        f"camera        : {settings.camera.host or '(empty)'}",
        f"folder        : {settings.storage.root}",
        f"budget        : {settings.storage.budget_gb} GB",
        f"delete after  : "
        f"{settings.storage.retention_days if settings.storage.retention_days else 'never'}",
        f"link ceiling  : {settings.bitrate.ceiling_kbps} kb/s",
        f"video         : {settings.video_mode}, {settings.video_buffer_ms} ms buffer",
    ]


class CameraTools:
    """The questions the field kept needing answered.

    "Which path actually gives video" and "does this stream fit the link" are
    both answered by asking the camera, and both were only reachable through the
    browser. They are plain calls into existing code; this is the seam that lets
    them be tested without a camera.
    """

    def __init__(self, ptz, find_paths, diagnose) -> None:
        self._ptz = ptz
        self._find_paths = find_paths
        self._diagnose = diagnose
        self.on_progress = lambda step: None

    def find_paths(self, settings: Settings) -> list[str]:
        return self._find_paths(settings, on_progress=self.on_progress)

    def diagnose(self, settings: Settings) -> list[str]:
        return self._diagnose(settings)

    def fit_to_link(self, settings: Settings) -> list[str]:
        result = self._ptz.fit_encoders_to_link(settings.bitrate.ceiling_kbps)
        if not result.get("ok"):
            return [result.get("error", "the camera refused")]
        return list(result.get("changed", []))

    def write_report(self, settings: Settings, path, extra: list[str]) -> Path:
        """Everything about this installation, in one file that can be sent on.

        Diagnosing a machine at the other end of a conversation fails on missing
        context more than on hard problems. The password is never included: it
        is the one thing in here that must not travel.
        """
        path = Path(path)
        lines = ["VMD report", ""]
        lines.extend(extra)
        lines.append("")
        lines.extend(self.diagnose(settings))
        text = "\n".join(lines)
        if settings.camera.password:
            text = text.replace(settings.camera.password, "****")
        path.write_text(text, encoding="utf-8")
        return path


def _first_problem(exc: Exception) -> str:
    """One readable sentence out of a validation error."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "settings"
        return f"{where}: {first['msg']}"
    return str(exc)
