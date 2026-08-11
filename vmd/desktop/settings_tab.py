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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.live import WrappedNote
from vmd.desktop.style import (
    FORM_MAX_WIDTH,
    PALETTE,
    SIZE_SMALL,
    SPACE_GROUP,
    SPACE_ROOM,
    SPACE_SNUG,
    SPACE_STEP,
    SPACE_TIGHT,
)
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
        outer.setSpacing(SPACE_TIGHT)

        # --- what it is ------------------------------------------------------
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACE_SNUG)

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
        watch = QVBoxLayout()
        watch.setContentsMargins(146, 0, 0, 0)  # under the address, not the name
        watch.setSpacing(SPACE_TIGHT)

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

        # Two lines and not one. All seven of these on a single row is about
        # 1500 px of controls, and the form is a column that stops growing - so
        # on one line the tick boxes lost their last word and the button read
        # "e and ignored p". A control whose label is cut in half is a control
        # nobody can act on, and this row carries the thermal flag, which is the
        # one setting that quietly changes what gets reported.
        #
        # The switches first, then the two choices under them, because that is
        # the order they are decided in: whether this view is watched at all,
        # then how.
        switches = QHBoxLayout()
        switches.setContentsMargins(0, 0, 0, 0)
        switches.setSpacing(SPACE_ROOM)
        switches.addWidget(self.detect_field)
        switches.addWidget(self.thermal_field)
        switches.addWidget(self.details_button)
        switches.addStretch(1)
        watch.addLayout(switches)

        choices = QHBoxLayout()
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(SPACE_SNUG)
        choices.addWidget(classify_label)
        choices.addWidget(self.classify_field)
        choices.addSpacing(SPACE_ROOM)
        choices.addWidget(sensitivity_label)
        choices.addWidget(self.sensitivity_field)
        choices.addStretch(1)
        watch.addLayout(choices)
        outer.addLayout(watch)

        # --- the two that need explaining ------------------------------------
        self.details = QFrame()
        self.details.setFrameShape(QFrame.Shape.StyledPanel)
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(8, 6, 8, 6)
        details_layout.setSpacing(4)

        # The picture. Every number under it is a dot of the camera's frame, and
        # a dot of a frame is not a thing anybody can estimate by eye - so this
        # button, which fetches one frame and lets the line and the patches be
        # drawn on it, sits above them rather than beside them.
        self.pick_button = QPushButton("Show me the picture and let me draw on it")
        self.pick_button.setToolTip(
            "Fetches one still picture from this view. Click it to put the sky "
            "line, drag a box over anything to ignore.\n\n"
            "If the camera cannot be reached the boxes below still work; they "
            "are the same settings, typed instead of drawn."
        )
        # Set by SettingsTab, which is the only thing that knows how to reach a
        # camera. A row on its own can still be built and tested with nothing
        # installed.
        self.on_pick = lambda row: None
        self.pick_button.clicked.connect(lambda: self.on_pick(self))
        details_layout.addWidget(self.pick_button)

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

        horizon_help = WrappedNote(
            "Draw the line on the picture above rather than guessing the "
            "number: a line set too low throws away real movement below it and "
            "never tells you it did. If the camera cannot be reached, leave the "
            "sky line off unless someone has read the number off a picture for "
            "you. Off is a perfectly safe setting."
        )
        horizon_help.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )
        details_layout.addWidget(horizon_help)
        self.horizon_help = horizon_help

        self.regions_help = WrappedNote(WHY_REGIONS)
        self.regions_help.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )
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


def _form(parent: QWidget | None = None) -> QFormLayout:
    """A form laid out on the console's own rhythm.

    Rows close together and labels close to their fields: what separates two
    settings here is the panel they are on, not the distance between them.
    """
    form = QFormLayout(parent) if parent is not None else QFormLayout()
    form.setHorizontalSpacing(SPACE_ROOM)
    form.setVerticalSpacing(SPACE_SNUG)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return form


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

        # Everything on this tab is inside a scroll area, and it is not a
        # preference: this form is six boxes, a stream row per camera view, and
        # a report box, and it asks for about 1300 px of height. No laptop panel
        # has that. Fullscreen - which is how a dedicated console is run - hands
        # the window the screen's geometry whatever the layout asked for, and a
        # Qt layout given less height than its minimum does not scroll, wrap or
        # elide: it shares out what there is. Measured at 1366x768 that was an
        # address field five pixels tall, and the "Not saved..." line drawn
        # straight through the report box above the Save button - the one
        # sentence that says why the settings did not take. The Live tab's side
        # column is built the same way, for the same reason.
        self._page = QWidget()
        self._scroll = QScrollArea(self)
        self._scroll.setWidget(self._page)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

        # The form is a column, and it stops growing.
        #
        # This is most of what "the program isn't fitted right" meant. A
        # thirteen-character address field stretched across 1900 px of a 4K
        # panel puts the label and the box it belongs to at opposite ends of the
        # screen, and every one of the six boxes did it. Past FORM_MAX_WIDTH the
        # column stops and the room goes to the margins, which is what makes it
        # readable at 3840 and unchanged at 1280 - where the column is narrower
        # than the ceiling anyway and nothing here applies.
        centred = QHBoxLayout(self._page)
        centred.setContentsMargins(SPACE_GROUP, SPACE_ROOM, SPACE_GROUP, SPACE_ROOM)
        centred.setSpacing(0)
        column = QWidget()
        column.setMaximumWidth(FORM_MAX_WIDTH)
        # Expanding up to that ceiling and not past it. Without the policy the
        # column takes its own size hint and the form is narrower on a big
        # screen than it is on a small one, which is the opposite of the point:
        # what is wanted is a column that uses the room it has until using more
        # would stop helping.
        column.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        # The column outweighs the two margins, so it takes the room first and
        # what is left over goes evenly to either side of it. Without a stretch
        # of its own it would be given nothing but its own size hint, and the
        # form would be NARROWER on a big screen than on a small one.
        centred.addStretch(1)
        centred.addWidget(column, 8)
        centred.addStretch(1)

        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        # The rhythm: a wide gap BETWEEN the boxes and a tight one inside them,
        # so what separates two settings is which panel they are on rather than
        # how far apart they happen to be. They were all ten pixels from
        # everything, which reads as one undifferentiated list of eleven things.
        layout.setSpacing(SPACE_GROUP)

        camera_box = QGroupBox("Camera")
        camera_form = _form(camera_box)
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
        streams_outer.setSpacing(SPACE_STEP)
        self._streams_layout = QVBoxLayout()
        self._streams_layout.setSpacing(SPACE_ROOM)
        streams_outer.addLayout(self._streams_layout)
        self.add_stream_button = QPushButton("Add a stream")
        self.add_stream_button.clicked.connect(lambda: self.add_stream_row())
        streams_outer.addWidget(self.add_stream_button)
        layout.addWidget(streams_box)

        detection_box = QGroupBox("Movement detection")
        detection_outer = QVBoxLayout(detection_box)
        detection_outer.setSpacing(SPACE_SNUG)
        detection_help = WrappedNote(
            "These apply to every view at once. Which views are watched, and "
            "how, is set on each stream above."
        )
        detection_help.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )
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

        travel_line = _form()
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
        storage_form = _form(storage_box)
        self._root = QLineEdit()
        self._budget = QLineEdit()
        self._days = QLineEdit()
        self._days.setPlaceholderText("empty means never delete by age")
        storage_form.addRow("Folder", self._root)
        storage_form.addRow("Budget (GB)", self._budget)
        storage_form.addRow("Delete older than (days)", self._days)
        layout.addWidget(storage_box)

        radio_box = QGroupBox("Radio")
        radio_form = _form(radio_box)
        self._radio_host = QLineEdit()
        self._radio_user = QLineEdit()
        self._radio_password = QLineEdit()
        radio_form.addRow("Address", self._radio_host)
        radio_form.addRow("Username", self._radio_user)
        radio_form.addRow("Password", self._radio_password)
        layout.addWidget(radio_box)

        tools_box = QGroupBox("The camera")
        tools_outer = QVBoxLayout(tools_box)
        tools_outer.setSpacing(SPACE_SNUG)
        tools_buttons = QHBoxLayout()
        tools_buttons.setSpacing(SPACE_SNUG)
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
        # An empty report box is a black rectangle, and a black rectangle is
        # not an answer to "has anything happened?". The placeholder is drawn
        # only while there is nothing in it, so it costs nothing once a tool has
        # actually said something.
        self._output.setPlaceholderText(
            "Press one of the buttons above and what the camera says appears here."
        )
        tools_outer.addWidget(self._output)
        layout.addWidget(tools_box)

        # The one row on this page the page exists for: what went wrong, and
        # the button that writes the file. Together, and at the end, because
        # "Not saved: ..." is about the button beside it - it used to be a bare
        # line floating above a full-width button of exactly the weight of every
        # other button on the tab.
        #
        # A WrappedNote: this line carries the sentence saying WHY a save did
        # not take effect, which is three lines long and is the one sentence on
        # this tab that must never be cut in half.
        self._message = WrappedNote("")
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("primary", "true")
        self.save_button.clicked.connect(self.save)
        ending = QHBoxLayout()
        ending.setContentsMargins(0, 0, 0, 0)
        ending.setSpacing(SPACE_GROUP)
        ending.addWidget(self._message, 1)
        ending.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(ending)
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
        row.on_pick = self.open_picker
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

    def open_picker(self, row: StreamRowWidget):
        """Show one frame from this view, to draw the sky line and patches on.

        The two settings under this button are native-frame pixel coordinates.
        Typed blind they are guesses, and a wrong sky line deletes real movement
        without saying so - so the picture is the control and the boxes are what
        is left when there is no picture to be had.

        Nothing is applied unless the operator presses the dialog's own button,
        and nothing here can leave the form worse than it found it: a camera
        that cannot be reached ends in a sentence in the dialog, with every box
        in this form exactly as it was.
        """
        # Imported here for the same reason the camera tools are: opening the
        # console must not pay for the camera stack, and this module has to stay
        # importable on a machine with nothing installed.
        from vmd.desktop.picker import PickerDialog

        name = row.values()[0]
        if not name:
            self._set_message(
                "Give this stream a name before asking it for a picture."
            )
            return None
        settings = self.settings_from_form()
        if settings is None:
            return None

        tools = self._camera_tools(settings)
        dialog = PickerDialog(
            stream=name,
            horizon=row.horizon(),
            regions=row.regions(),
            grab=lambda: tools.grab_frame(settings, name),
            parent=self,
        )
        dialog.accepted.connect(lambda: self._apply_picked(row, dialog))
        # open(), never exec(): exec() runs its own event loop on the window
        # thread, and everything this console has learned about freezing says
        # not to.
        dialog.open()
        return dialog

    @staticmethod
    def _apply_picked(row: StreamRowWidget, dialog) -> None:
        """Put what was drawn into the boxes, which stay the settings.

        The sky line is switched on by drawing one: an operator who has just put
        a line on a picture has said what they want, and leaving the tick box
        off would file it away as "typed but not meant".
        """
        row.set_horizon(dialog.horizon())
        row.set_regions(dialog.regions())

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

    def __init__(self, ptz, find_paths, diagnose, grab_frame=None) -> None:
        self._ptz = ptz
        self._find_paths = find_paths
        self._diagnose = diagnose
        # Optional, and resolved on first use rather than here, so that building
        # these tools never imports the picture stack and every caller written
        # before there was a picture keeps working.
        self._grab_frame = grab_frame
        self.on_progress = lambda step: None

    def grab_frame(self, settings: Settings, stream: str) -> bytes:
        """One still picture from a stream, as the bytes of a picture."""
        if self._grab_frame is not None:
            return self._grab_frame(settings, stream)
        from vmd.desktop.picker import grab_frame

        return grab_frame(settings, stream)

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
