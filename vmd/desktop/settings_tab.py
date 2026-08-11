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
from urllib.parse import quote

from pydantic import ValidationError
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.style import PALETTE
from vmd.settings import (
    Settings,
    SettingsError,
    StreamSettings,
    load_settings,
    save_settings,
)

logger = logging.getLogger(__name__)

# name, url, enabled, reader
StreamRow = tuple[str, str, bool, str]

READERS = ["auto", "ffmpeg"]

# The file `storage_problem` writes and removes to find out whether footage can
# actually be written to the chosen folder. A name nothing else uses, and one
# that sorts away from the recordings.
PROBE_NAME = ".vmd-write-test"

# The rectangle itself, kept on the list item rather than parsed back out of the
# words shown to the operator. The words are for reading; the numbers are the
# setting, and re-deriving one from the other is how a patch quietly moves.
_REGION_ROLE = int(Qt.ItemDataRole.UserRole)

# The three positions of `classify`, in the order they are offered, with the
# value each one saves. None is first because it is the default and the right
# answer for almost everyone: "follow the sensor" in the model's words, which is
# not a sentence to put in front of the operator.
CLASSIFY_CHOICES: list[tuple[str, bool | None]] = [
    ("Work it out for me", None),
    ("Always try to name it", True),
    ("Never try to name it", False),
]

# Why the thermal head is different, in the only place the operator will ever
# read it. The numbers are from the design: at 700 m a person is about 13 dots
# across on the thermal sensor, which is not a photograph of anything.
WHY_THERMAL = (
    "This view comes from the heat camera.\n\n"
    "It matters for one reason. At about 700 m away, a person is only around "
    "13 dots across on the heat picture - far too small to tell a person from a "
    "dog from a post. So naming what moved is left switched off for the heat "
    "camera, and left on for the ordinary one.\n\n"
    "Movement is still reported either way. Naming it is a bonus, never a "
    "condition."
)

WHY_CLASSIFY = (
    "After something has moved, VMD can try to say what it was - a person, a "
    "dog, a car.\n\n"
    "\"Work it out for me\" is the safe answer: it tries on the ordinary camera "
    "and does not bother on the heat camera, where a person 700 m away is only "
    "about 13 dots across and nothing can be told apart at that size.\n\n"
    "Whatever this is set to, movement is still reported. Naming it never "
    "decides whether you are told."
)

SENSITIVITY_CHOICES: list[tuple[str, str]] = [
    ("Low - only big, obvious movement", "low"),
    ("Normal", "normal"),
    ("High - notices small or distant movement", "high"),
]

WHY_HORIZON = (
    "Everything above this line is treated as sky, so birds are not reported.\n\n"
    "The number is counted in dots (pixels) down from the TOP edge of the "
    "picture - not metres, not degrees. So 0 is the very top and a bigger "
    "number is further down.\n\n"
    "Leave this switched off unless you know the number. A line set too low "
    "throws away real movement below it and never tells you it did. Off is a "
    "perfectly good setting."
)

WHY_REGIONS = (
    "A patch listed here is never reported. It is the only reliable answer to "
    "one particular tree that sways, a flag, or a busy road you do not care "
    "about. Everything outside these patches is still watched.\n\n"
    "The four numbers are dots (pixels) in the picture: how far across from the "
    "left edge, how far down from the top edge, then how wide and how tall."
)


class StreamRowWidget(QWidget):
    """One stream, as the operator sees it: a name, an address, whether it is
    recorded, which client reads it, and - since detection exists - whether it is
    watched and how. Nothing about it is fixed: a camera calls its streams
    whatever it likes and the form has to keep up.

    Every one of those choices lives on this widget rather than in a list held
    beside it, because that is what makes removing, adding and reordering rows
    safe. A detection setting matched to a stream by position is a thermal flag
    waiting to land on the wrong head.
    """

    def __init__(
        self,
        name: str = "",
        url: str = "",
        enabled: bool = True,
        reader: str = "auto",
        stream: StreamSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if stream is None:
            stream = StreamSettings(name=name, url=url, enabled=enabled, reader=reader)
        # Everything this stream arrived with. A save is this with the widgets
        # written over it, so a field added to StreamSettings later is carried
        # across rather than reset the first time anyone presses Save.
        self._base = stream.model_dump(mode="json")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # --- what it is ------------------------------------------------------
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        self.name_field = QLineEdit(stream.name)
        self.name_field.setPlaceholderText("name")
        self.name_field.setFixedWidth(140)

        self.url_field = QLineEdit(stream.url)
        self.url_field.setPlaceholderText("rtsp://address/path")

        self.record_field = QCheckBox("record")
        self.record_field.setChecked(stream.enabled)

        self.reader_field = QComboBox()
        self.reader_field.addItems(READERS)
        self.reader_field.setCurrentText(stream.reader if stream.reader in READERS else "auto")

        self.remove_button = QPushButton("Remove")

        top.addWidget(self.name_field)
        top.addWidget(self.url_field, 1)
        top.addWidget(self.record_field)
        top.addWidget(self.reader_field)
        top.addWidget(self.remove_button)
        outer.addLayout(top)

        # --- whether it is watched, and how ----------------------------------
        #
        # A second line under the stream rather than a separate panel: these
        # belong to this stream and nothing else, and a panel somewhere else on
        # the tab is how the wrong stream gets marked thermal.
        watch = QHBoxLayout()
        watch.setContentsMargins(146, 0, 0, 0)  # under the address, not the name
        watch.setSpacing(6)

        self.detect_field = QCheckBox("Watch for movement")
        self.detect_field.setChecked(stream.detect)
        self.detect_field.setToolTip(
            "Watch this view and raise an alert when something moves in it.\n\n"
            "Off until you turn it on. A detector pointed at a treeline before "
            "anyone has told it about the trees alarms all day, and an alarm "
            "nobody believes is worse than none."
        )

        self.thermal_field = QCheckBox("Heat camera")
        self.thermal_field.setChecked(stream.thermal)
        self.thermal_field.setToolTip(WHY_THERMAL)

        self.classify_field = QComboBox()
        for label, value in CLASSIFY_CHOICES:
            self.classify_field.addItem(label, value)
        self.classify_field.setToolTip(WHY_CLASSIFY)
        self.set_classify(stream.classify)
        classify_label = QLabel("Name what moved:")
        classify_label.setToolTip(WHY_CLASSIFY)

        self.sensitivity_field = QComboBox()
        for label, value in SENSITIVITY_CHOICES:
            self.sensitivity_field.addItem(label, value)
        self.sensitivity_field.setToolTip(
            "How much movement it takes before you are told.\n\n"
            "High notices more, including more wind, rain and shadows. Low "
            "notices only large, clear movement. Start at Normal."
        )
        self.set_sensitivity(stream.sensitivity)
        sensitivity_label = QLabel("How touchy:")
        sensitivity_label.setToolTip(self.sensitivity_field.toolTip())

        self.details_button = QPushButton("Sky line and ignored patches")
        self.details_button.setCheckable(True)
        self.details_button.setToolTip(
            "The two settings for a view that keeps alarming on something you "
            "do not care about."
        )

        watch.addWidget(self.detect_field)
        watch.addWidget(self.thermal_field)
        watch.addWidget(classify_label)
        watch.addWidget(self.classify_field)
        watch.addWidget(sensitivity_label)
        watch.addWidget(self.sensitivity_field)
        watch.addWidget(self.details_button)
        watch.addStretch(1)
        outer.addLayout(watch)

        # --- the two that need explaining ------------------------------------
        self.details = QFrame()
        self.details.setFrameShape(QFrame.Shape.StyledPanel)
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(8, 6, 8, 6)
        details_layout.setSpacing(4)

        horizon_line = QHBoxLayout()
        horizon_line.setSpacing(6)
        self.horizon_enabled_field = QCheckBox("Ignore everything above a sky line")
        self.horizon_enabled_field.setToolTip(WHY_HORIZON)
        self.horizon_field = QSpinBox()
        self.horizon_field.setRange(0, 100000)
        self.horizon_field.setSuffix(" dots from the top")
        self.horizon_field.setToolTip(WHY_HORIZON)
        self.set_horizon(stream.horizon_y)
        self.horizon_enabled_field.toggled.connect(self.horizon_field.setEnabled)
        self.horizon_field.setEnabled(self.horizon_enabled_field.isChecked())
        horizon_line.addWidget(self.horizon_enabled_field)
        horizon_line.addWidget(self.horizon_field)
        horizon_line.addStretch(1)
        details_layout.addLayout(horizon_line)

        horizon_help = QLabel(
            "There is no picture on this screen to measure against, so leave "
            "the sky line off unless someone has read the number off a frame "
            "for you. Off is the safe setting: a wrong line deletes real "
            "movement without saying so."
        )
        horizon_help.setWordWrap(True)
        horizon_help.setStyleSheet(f"color: {PALETTE['muted']};")
        details_layout.addWidget(horizon_help)
        self.horizon_help = horizon_help

        self.regions_help = QLabel(WHY_REGIONS)
        self.regions_help.setWordWrap(True)
        self.regions_help.setStyleSheet(f"color: {PALETTE['muted']};")
        details_layout.addWidget(self.regions_help)

        self.regions_list = QListWidget()
        self.regions_list.setToolTip(WHY_REGIONS)
        self.regions_list.setMaximumHeight(90)
        details_layout.addWidget(self.regions_list)

        region_line = QHBoxLayout()
        region_line.setSpacing(6)
        self.region_x = _region_box("across")
        self.region_y = _region_box("down")
        self.region_w = _region_box("wide")
        self.region_h = _region_box("tall")
        self.add_region_button = QPushButton("Add this patch")
        self.remove_region_button = QPushButton("Delete the selected patch")
        for box in (self.region_x, self.region_y, self.region_w, self.region_h):
            region_line.addWidget(box)
        region_line.addWidget(self.add_region_button)
        region_line.addWidget(self.remove_region_button)
        region_line.addStretch(1)
        details_layout.addLayout(region_line)

        self.add_region_button.clicked.connect(self.add_region)
        self.remove_region_button.clicked.connect(self.remove_selected_region)
        self.set_regions([r.as_tuple() for r in stream.ignore_regions])

        self.details.setVisible(False)
        self.details_button.toggled.connect(self.details.setVisible)
        outer.addWidget(self.details)

        # What to say when a patch cannot be added. Set by SettingsTab so the
        # row does not need to know where the message line lives.
        self.on_problem = lambda text: None

    # ------------------------------------------------------------- the values

    def values(self) -> StreamRow:
        return (
            self.name_field.text().strip(),
            self.url_field.text().strip(),
            self.record_field.isChecked(),
            self.reader_field.currentText(),
        )

    def classify(self) -> bool | None:
        return self.classify_field.currentData()

    def set_classify(self, value: bool | None) -> None:
        # `is`, not findData: findData compares with ==, and False == 0 == None
        # in enough of Qt's variant handling to land "never name it" on "work it
        # out for me" without a word of complaint.
        for index in range(self.classify_field.count()):
            if self.classify_field.itemData(index) is value:
                self.classify_field.setCurrentIndex(index)
                return
        self.classify_field.setCurrentIndex(0)

    def sensitivity(self) -> str:
        return self.sensitivity_field.currentData()

    def set_sensitivity(self, value: str) -> None:
        for index in range(self.sensitivity_field.count()):
            if self.sensitivity_field.itemData(index) == value:
                self.sensitivity_field.setCurrentIndex(index)
                return
        self.sensitivity_field.setCurrentIndex(1)  # normal

    def horizon(self) -> int | None:
        """The sky line, or None when the rule is off - which is not the same as
        zero. Zero would mean the whole picture is sky."""
        if not self.horizon_enabled_field.isChecked():
            return None
        return int(self.horizon_field.value())

    def set_horizon(self, value: int | None) -> None:
        self.horizon_enabled_field.setChecked(value is not None)
        self.horizon_field.setValue(int(value) if value is not None else 0)

    def regions(self) -> list[tuple[int, int, int, int]]:
        return [
            self.regions_list.item(i).data(_REGION_ROLE) for i in range(self.regions_list.count())
        ]

    def set_regions(self, regions) -> None:
        self.regions_list.clear()
        for region in regions:
            self._append_region(tuple(int(n) for n in region))

    def add_region(self) -> bool:
        """Add the patch in the four boxes, or say why it is not a patch."""
        x, y = self.region_x.value(), self.region_y.value()
        w, h = self.region_w.value(), self.region_h.value()
        if w <= 0 or h <= 0:
            self.on_problem(
                "A patch to ignore needs a width and a height greater than zero."
            )
            return False
        self._append_region((x, y, w, h))
        return True

    def remove_selected_region(self) -> None:
        row = self.regions_list.currentRow()
        if row < 0:
            self.on_problem("Select the patch to delete first.")
            return
        self.regions_list.takeItem(row)

    def _append_region(self, region: tuple[int, int, int, int]) -> None:
        x, y, w, h = region
        item = QListWidgetItem(f"{w} x {h} dots, at {x} across and {y} down")
        item.setData(_REGION_ROLE, region)
        self.regions_list.addItem(item)

    def stream_values(self) -> dict:
        """Everything this row knows, as StreamSettings would take it.

        Built on top of what the stream arrived with, so a field this form has
        never heard of survives a save rather than being reset to its default.
        """
        name, url, enabled, reader = self.values()
        payload = dict(self._base)
        payload.update(
            name=name,
            url=url,
            enabled=enabled,
            reader=reader,
            detect=self.detect_field.isChecked(),
            thermal=self.thermal_field.isChecked(),
            classify=self.classify(),
            sensitivity=self.sensitivity(),
            horizon_y=self.horizon(),
            ignore_regions=[
                {"x": x, "y": y, "w": w, "h": h} for x, y, w, h in self.regions()
            ],
        )
        return payload


def storage_problem(root, beside: Path) -> str:
    """Why this folder cannot hold the recordings, in one plain sentence.

    Pointing the recordings at a drive letter with nothing behind it is the most
    likely mistake a non-technical operator can make while setting this up, and
    it used to be accepted silently: the form said "Saved.", the Logs tab filled
    with a traceback through pathlib ending "FileNotFoundError: [WinError 3]",
    and the Playback tab was replaced by an apology quoting the same error.

    Made rather than merely checked, because "it does not exist yet" is the
    ordinary first-run state - the recorder creates it - and refusing that would
    refuse the common case. What is actually being asked is the useful question:
    can this machine write footage there. So it is created and written to, with
    a probe file that is removed again.

    `beside` anchors a relative folder to the settings file, exactly as
    `load_settings` does, so that "recordings" means the same folder here as it
    does to the recorder however the console was started.
    """
    root = Path(root)
    if not root.is_absolute():
        root = Path(beside) / root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return (
            f'The recordings folder "{root}" could not be made: {_why(exc)}. '
            f"Choose a folder on a drive this machine can write to."
        )
    probe = root / PROBE_NAME
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        return (
            f'Nothing can be written to the recordings folder "{root}": {_why(exc)}. '
            f"Choose a folder this machine can write to."
        )
    return ""


def _why(exc: OSError) -> str:
    """The operating system's own words, without the code and the traceback."""
    return (exc.strerror or str(exc)).rstrip(". ")


def _region_box(what: str) -> QSpinBox:
    box = QSpinBox()
    box.setRange(0, 100000)
    box.setPrefix(f"{what} ")
    box.setToolTip(WHY_REGIONS)
    return box


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
    # What was just written, for whoever is still pointed at the old file.
    # Nothing in the console re-reads settings.json on its own: go2rtc parses
    # its configuration once at startup, and the PTZ and radio services hold the
    # address and password they were built with. Without this signal a save
    # writes the file and changes nothing that is running, which for an operator
    # with no terminal and no second machine means the camera they have just
    # corrected the address of stays dark until the laptop is rebooted.
    saved = Signal(object)

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

        detection_box = QGroupBox("Movement detection")
        detection_outer = QVBoxLayout(detection_box)
        detection_help = QLabel(
            "These apply to every view at once. Which views are watched, and "
            "how, is set on each stream above."
        )
        detection_help.setWordWrap(True)
        detection_help.setStyleSheet(f"color: {PALETTE['muted']};")
        detection_outer.addWidget(detection_help)

        self._detection_enabled = QCheckBox("Watch for movement at all")
        self._detection_enabled.setToolTip(
            "The master switch. Turning it off stops movement detection and "
            "nothing else - recording keeps running, because it is a separate "
            "program that shares nothing with this one."
        )
        detection_outer.addWidget(self._detection_enabled)

        self._detection_classify = QCheckBox("Allow VMD to try to name what moved")
        self._detection_classify.setToolTip(
            "The master switch for naming things. With this off, nothing is "
            "ever named, whatever the individual views are set to. With it on, "
            "each view decides for itself.\n\n"
            "It needs an extra download to work, and at 700 m a person is only "
            "about 13 dots across, so it is off to begin with. You are told "
            "about the movement either way."
        )
        detection_outer.addWidget(self._detection_classify)

        travel_line = QFormLayout()
        self._min_travel = QLineEdit()
        self._min_travel.setPlaceholderText("empty means use the touchiness setting")
        self._min_travel.setToolTip(
            "How far a thing must travel across the picture, in dots, before "
            "you are told about it. This is what separates a person walking "
            "from a branch waving in one place.\n\n"
            "Leave it empty. The touchiness setting already carries a measured "
            "number for each view, and typing one here overrules a measurement."
        )
        travel_line.addRow("Must travel at least (dots)", self._min_travel)
        detection_outer.addLayout(travel_line)
        layout.addWidget(detection_box)

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

    @property
    def detection_enabled(self) -> bool:
        return self._detection_enabled.isChecked()

    @detection_enabled.setter
    def detection_enabled(self, value: bool) -> None:
        self._detection_enabled.setChecked(bool(value))

    @property
    def detection_classify(self) -> bool:
        return self._detection_classify.isChecked()

    @detection_classify.setter
    def detection_classify(self, value: bool) -> None:
        self._detection_classify.setChecked(bool(value))

    @property
    def min_travel_px(self) -> str:
        return self._min_travel.text()

    @min_travel_px.setter
    def min_travel_px(self, value: str) -> None:
        self._min_travel.setText(str(value))

    def credential_fields(self) -> list[QLineEdit]:
        """Every field holding a password. They are all plain text on purpose."""
        return [self._password, self._radio_password]

    # ---------------------------------------------------------------- streams

    def add_stream_row(
        self,
        name: str = "",
        url: str = "",
        enabled: bool = True,
        reader: str = "auto",
        stream: StreamSettings | None = None,
    ) -> StreamRowWidget:
        row = StreamRowWidget(name, url, enabled, reader, stream=stream)
        row.remove_button.clicked.connect(lambda: self.remove_stream_row(row))
        row.on_problem = self._set_message
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

    def set_streams(self, rows: list[StreamRow] | list[StreamSettings]) -> None:
        """Replace every row. A `StreamSettings` brings its detection choices
        with it; the four-part tuple is the older shorthand and means a stream
        with detection left at its defaults."""
        for row in list(self._rows):
            self.remove_stream_row(row)
        for item in rows:
            if isinstance(item, StreamSettings):
                self.add_stream_row(stream=item)
            else:
                name, url, enabled, reader = item
                self.add_stream_row(name, url, enabled, reader)

    def streams(self) -> list[StreamRow]:
        """What is on screen. Never a remembered list: the widgets are the truth."""
        return [row.values() for row in self._rows]

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        """Fill the form from the file, or from the defaults and say why.

        A file that cannot be read must not cost this tab. It is the only tool
        on the machine that can fix that file, and an operator with no terminal
        who loses it has no way back at all - so a broken file fills the boxes
        with the defaults and puts the reason under them, where "Save" replaces
        the file with something that loads.
        """
        problem = ""
        try:
            settings = load_settings(self.settings_path)
        except SettingsError as exc:
            logger.exception("the settings file could not be read")
            settings = Settings()
            problem = (
                f"The settings file could not be read, so the boxes below show "
                f"the standard settings rather than yours. Correct them and "
                f"press Save to replace the file. ({exc})"
            )
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
        self.detection_enabled = settings.detection.enabled
        self.detection_classify = settings.detection.classify
        self.min_travel_px = (
            "" if settings.detection.min_travel_px is None else str(settings.detection.min_travel_px)
        )
        # The whole stream, not four of its fields: the detection choices belong
        # to the row that shows them, and a row that was handed only a name and
        # an address would write the defaults back over them at the next save.
        self.set_streams(list(settings.camera.streams))
        self._set_message(problem)

    # ------------------------------------------------------------------ save

    def save(self) -> bool:
        settings = self.settings_from_form()
        if settings is None:
            return False

        # Checked here and not in `settings_from_form`, because that is also
        # what the camera tools read the form through and none of them writes
        # anything to the disk.
        problem = storage_problem(settings.storage.root, self.settings_path.parent)
        if problem:
            self._set_message(problem)
            return False

        try:
            save_settings(settings, self.settings_path)
        except OSError as exc:
            self._set_message(f"Could not write the settings file: {exc}")
            return False

        self._loaded = settings
        self._set_message("Saved.")
        try:
            self.saved.emit(settings)
        except Exception:  # noqa: BLE001 - the file is written; the rest is not the save
            logger.exception("the saved settings could not be handed to the console")
        return True

    def report_after_save(self, text: str) -> None:
        """Replace "Saved." with what the console could not make true.

        The file was written; that is what "Saved." means and it is not a lie.
        But a child that would not restart is still running the settings the
        operator just replaced, and this line is the only place on this machine
        where that can be said to them.
        """
        self._set_message(text)

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
            streams=[row.stream_values() for row in self._rows],
        )
        payload["detection"] = dict(payload.get("detection", {}))
        payload["detection"].update(
            enabled=self.detection_enabled,
            classify=self.detection_classify,
            min_travel_px=self.min_travel_px.strip() or None,
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
        context more than on hard problems. No password is ever included: they
        are the one thing in here that must not travel, and this file exists to
        be handed to somebody else.

        Both forms of each, because a password does not reach this text the way
        it was typed. RTSP carries credentials in the URL, so `with_credentials`
        percent-encodes them: `p@ss:w/rd` appears as `p%40ss%3Aw%2Frd`, which a
        search for the typed form does not match at all. The camera's and the
        radio's alike - the radio's is in the same report and travels the same
        way.
        """
        path = Path(path)
        lines = ["VMD report", ""]
        lines.extend(extra)
        lines.append("")
        lines.extend(self.diagnose(settings))
        text = "\n".join(lines)
        for secret in _secrets(settings):
            text = text.replace(secret, "****")
        path.write_text(text, encoding="utf-8")
        return path


def _secrets(settings: Settings) -> set[str]:
    """Every string that must never leave this machine, in every form it takes.

    The empty ones are dropped rather than replaced: `"".replace("", "****")`
    puts the redaction between every character of the file, and a laptop on
    which nobody has typed a password yet is the ordinary first-run state.
    """
    secrets: set[str] = set()
    for password in (settings.camera.password, settings.radio.password):
        if password:
            secrets.add(password)
            secrets.add(quote(password, safe=""))
    return secrets


def _first_problem(exc: Exception) -> str:
    """One readable sentence out of a validation error."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "settings"
        return f"{where}: {first['msg']}"
    return str(exc)
