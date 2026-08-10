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
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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


class SettingsTab(QWidget):
    def __init__(self, settings_path: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings_path = Path(settings_path)
        self.message = ""
        # What was on disk when this form was filled. A save is this, with the
        # form's fields written over it, so nothing off-screen is lost.
        self._loaded = Settings()
        self._rows: list[StreamRowWidget] = []

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


def _first_problem(exc: Exception) -> str:
    """One readable sentence out of a validation error."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "settings"
        return f"{where}: {first['msg']}"
    return str(exc)
