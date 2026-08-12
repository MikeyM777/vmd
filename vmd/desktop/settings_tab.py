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
import math
import shutil
from dataclasses import dataclass
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
    QGridLayout,
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
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.disk import bytes_in_words, recorded_bytes
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

# The console has one slider and it should keep looking like one slider. The
# look is built out of PALETTE names in the zoom bar, which is where the first
# one was needed; copying it here would be the same decision written twice, and
# style.py - where it really belongs - is owned by other work this week.
from vmd.desktop.zoombar import _SLIDER_STYLE as SLIDER_STYLE
from vmd.settings import (
    Settings,
    SettingsError,
    StreamSettings,
    load_settings,
    save_settings,
)

logger = logging.getLogger(__name__)

# name, url, enabled, reader
#
# The third and fourth are no longer things the form asks about. They stay in
# the shape because everything downstream that describes a stream describes it
# with these four - `services.py` builds the same tuple straight out of the
# settings - and because the fourth is still carried across a save even though
# nobody chooses it here. The third is always True; see `StreamRowWidget`.
StreamRow = tuple[str, str, bool, str]

# How many camera views stand side by side.
#
# Two, because the camera is one gimbal with two heads and he sets them up
# against each other: "make the vis and thermal in the settings side by side
# instead of one under the other, so it's easier". Three would put a card at
# about 240 px inside a column that stops at FORM_MAX_WIDTH, which is narrower
# than the longest control on it.
STREAM_COLUMNS = 2

# How much of the drive is never offered to the budget.
#
# A drive filled to the last byte is a Windows laptop that cannot write its own
# page file, and this one is a console nobody logs into to clear space. The
# fraction handles a big drive, where 10 GB would be a rounding error; the floor
# handles a small one, where 5% would be.
DRIVE_RESERVE_FRACTION = 0.05
DRIVE_RESERVE_FLOOR_BYTES = 10 * 1024**3

# Below this there is no budget worth suggesting, and saying so is more use than
# a number. A day of two streams at the link ceiling is about 13 GB.
SMALLEST_WORTHWHILE_BUDGET_BYTES = 5 * 1024**3

# Where the budget slider ends. Not a limit on the setting - the box beside it
# takes any number the model accepts, and a bigger one simply parks the handle
# at the end - but a slider has to have a length, and a 4 TB scale makes every
# ordinary budget the first millimetre of it.
BUDGET_SLIDER_MAX_GB = 2000

SECONDS_IN_A_DAY = 86400.0

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

# The three sentences he asked for, in the only place he will read them: on the
# form, under the control, in the ink notes are written in.
#
# "'Watch for movement' - what is that?", "'Name what moved' - what is that?",
# "'Skyline and ignore...' - what is that?" - all three asked by the person
# these labels were written for. A tooltip is read by whoever hovers over that
# one control, which on a console driven from two metres back is nobody. The
# review of this tab says it plainly: a control whose name only makes sense on
# hover is a control with the wrong name, and the tooltips here were already
# doing more work than tooltips should.
DETECT_HELP = (
    "With this on, anything that moves in this view is written into the movement "
    "list and lights the red strip across the bottom of the pictures. Recording "
    "carries on either way - this only decides whether you are told."
)

CLASSIFY_HELP = (
    "After something has moved, VMD can have a guess at what it was: a person, a "
    "vehicle, an animal. It is only ever a guess, and it never decides whether "
    "anything is recorded or reported - a thing it cannot name is still reported."
)

REGIONS_HELP = (
    "The sky, a road you do not care about, a tree that moves in the wind - "
    "anything you mark in here is not reported. Everything outside it is still "
    "watched."
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

WHICH_NUMBER_IS_WHICH = (
    "The four numbers are dots (pixels) in the picture: how far across from the "
    "left edge, how far down from the top edge, then how wide and how tall."
)

WHY_REGIONS = (
    "A patch listed here is never reported. It is the only reliable answer to "
    "one particular tree that sways, a flag, or a busy road you do not care "
    "about. Everything outside these patches is still watched.\n\n"
    + WHICH_NUMBER_IS_WHICH
)


class StreamRowWidget(QFrame):
    """One camera view, as a card: a name, an address, and - folded away until
    it is asked for - whether it is watched and how. Nothing about it is fixed:
    a camera calls its streams whatever it likes and the form has to keep up.

    Every one of those choices lives on this widget rather than in a list held
    beside it, because that is what makes removing, adding and reordering rows
    safe. A detection setting matched to a stream by position is a thermal flag
    waiting to land on the wrong head.

    Two things it used to ask and no longer does.

    **Whether the view is used at all.** It was a tick box called `Use this
    view`, and the operator's verdict was "useless, of course use that view, if
    it's added". He is right, and the reason is not taste: the reward for
    remembering the tick is the state you were already in, and the price of
    missing it is a camera that is silently not shown, not recorded and not
    watched. `enabled` is still in the settings file and everything downstream
    still reads it; this form simply always writes True, because a line on the
    list IS a view in use and the way to stop using one is `Remove`.

    **Which client reads the stream.** `auto` or `ffmpeg`, and the honest
    explanation is "try the other one if the picture will not come up", which is
    not a question to put to somebody setting a camera up for the first time.
    The setting keeps working and a file that says `ffmpeg` still says `ffmpeg`
    after a save; it is just not on the screen.

    The rest of the card is a column rather than a row because two of these
    stand side by side now, so each has about 360 px to live in - and a control
    whose label is cut in half is a control nobody can act on.
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
        # `enabled` is taken and ignored: see the class docstring. It stays in
        # the signature because the four-part shorthand tuple is what a dozen
        # callers hand `set_streams`, and dropping it there would be churn in
        # every one of them for a value that is now always the same.
        del enabled
        if stream is None:
            # `model_construct`, not the constructor: a row on screen is not a
            # setting yet. "Add a stream" starts an empty one, and an empty name
            # is a stream nothing downstream can address - so `StreamSettings`
            # refuses it, correctly, at the point where it becomes a setting.
            # That point is Save, where `_problem` already says "A stream has an
            # address but no name." in words the operator can act on. Validating
            # here instead would make the Add button raise before they had a
            # chance to type anything. Defaults for every field this does not
            # name are still filled in.
            stream = StreamSettings.model_construct(
                name=name, url=url, enabled=True, reader=reader
            )
        # Everything this stream arrived with. A save is this with the widgets
        # written over it, so a field added to StreamSettings later is carried
        # across rather than reset the first time anyone presses Save.
        self._base = stream.model_dump(mode="json")

        # A bordered card, because two of these stand side by side and the eye
        # has to be able to tell where one camera view stops and the next starts.
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_SNUG, SPACE_SNUG, SPACE_SNUG, SPACE_SNUG)
        outer.setSpacing(SPACE_TIGHT)

        # --- what it is ------------------------------------------------------
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACE_SNUG)

        self.name_field = QLineEdit(stream.name)
        self.name_field.setPlaceholderText("name")

        self.url_field = QLineEdit(stream.url)
        self.url_field.setPlaceholderText("rtsp://address/path")

        self.remove_button = QPushButton("Remove")

        top.addWidget(self.name_field, 1)
        top.addWidget(self.remove_button)
        outer.addLayout(top)
        # The address on its own line. In half a column there is no room for a
        # name and an address side by side, and the address is the longer of the
        # two by a wide margin.
        outer.addWidget(self.url_field)

        # --- whether it is watched, and how ----------------------------------
        #
        # On this card rather than in a panel of its own: these belong to this
        # view and nothing else, and a panel somewhere else on the tab is how the
        # wrong stream gets marked thermal.
        self.detect_field = QCheckBox("Watch for movement")
        self.detect_field.setChecked(stream.detect)
        self.detect_field.setToolTip(
            "Watch this view and raise an alert when something moves in it.\n\n"
            "Off until you turn it on. A detector pointed at a treeline before "
            "anyone has told it about the trees alarms all day, and an alarm "
            "nobody believes is worse than none."
        )
        outer.addWidget(self.detect_field)

        # The sentence explaining what that tick does is NOT here. It used to
        # be, and putting it here was the right instinct applied one level too
        # low: the views are side by side now, so the same three lines were
        # printed twice, next to each other, in a tab whose whole complaint was
        # "too much going on". Two copies of a paragraph do not explain a thing
        # twice as well; they make the reader check whether they differ.
        #
        # It is said once, above both cards, in `_build_streams_box`. The
        # tooltip on the tick stays for whoever hovers.

        # Everything below the switch, in one widget so that one line of code
        # shows or hides all of it.
        #
        # "Too much going on." Seven controls per camera view, six of which are
        # answers to a question - how should this view be watched - that nobody
        # is asking until the first one is ticked. Folded, never deleted: he has
        # said he wants to test movement detection in the next days, and a
        # setting that has been removed is not a setting that can be tested.
        self.watched = QWidget()
        watch = QVBoxLayout(self.watched)
        watch.setContentsMargins(SPACE_ROOM, SPACE_TIGHT, 0, 0)
        watch.setSpacing(SPACE_TIGHT)

        self.thermal_field = QCheckBox("Heat camera")
        self.thermal_field.setChecked(stream.thermal)
        self.thermal_field.setToolTip(WHY_THERMAL)
        watch.addWidget(self.thermal_field)

        self.classify_field = QComboBox()
        for label, value in CLASSIFY_CHOICES:
            self.classify_field.addItem(label, value)
        self.classify_field.setToolTip(WHY_CLASSIFY)
        self.set_classify(stream.classify)
        # It said "Name what moved", which reads as an instruction to the
        # operator rather than as something the software attempts - and he asked
        # what it was.
        self.classify_label = QLabel("Try to say what it was:")
        self.classify_label.setToolTip(WHY_CLASSIFY)
        watch.addWidget(self.classify_label)
        watch.addWidget(self.classify_field)
        # And here too the paragraph is NOT on the card: it is said once above
        # both of them, in `__init__` below. Same reason as the one above -
        # guessing what moved means the same thing on both heads of one gimbal,
        # so printing it on each card is one paragraph twice, side by side and
        # word for word identical, on the tab he called too busy.

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
        watch.addWidget(sensitivity_label)
        watch.addWidget(self.sensitivity_field)

        # It said "Sky line and ignored patches": two nouns lifted out of the
        # source and joined by an "and", naming neither what it is for nor what
        # it acts on. He asked what it was.
        self.details_button = QPushButton("Ignore parts of the picture")
        self.details_button.setCheckable(True)
        self.details_button.setToolTip(
            "The two settings for a view that keeps alarming on something you "
            "do not care about."
        )
        watch.addWidget(self.details_button)
        # The third of the three, and the last one that was still per-card. What
        # a patch to ignore is for does not change between the two heads either.

        # A label above its box rather than beside it. In half a column there is
        # no room for both, and the thing that has to be readable is the choice
        # itself: "High - notices small or distant movement" is 250 px of words
        # and it is what the operator is picking between.
        outer.addWidget(self.watched)
        self.detect_field.toggled.connect(
            lambda shown: self._unfold(self.watched, shown)
        )
        self.watched.setVisible(self.detect_field.isChecked())

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

        self.horizon_enabled_field = QCheckBox("Ignore everything above a sky line")
        self.horizon_enabled_field.setToolTip(WHY_HORIZON)
        self.horizon_field = QSpinBox()
        self.horizon_field.setRange(0, 100000)
        self.horizon_field.setSuffix(" dots from the top")
        self.horizon_field.setToolTip(WHY_HORIZON)
        self.set_horizon(stream.horizon_y)
        self.horizon_enabled_field.toggled.connect(self.horizon_field.setEnabled)
        self.horizon_field.setEnabled(self.horizon_enabled_field.isChecked())
        # The tick and its number stacked rather than side by side. Together
        # they are about 400 px of controls, and half a form column is 360.
        details_layout.addWidget(self.horizon_enabled_field)
        horizon_line = QHBoxLayout()
        horizon_line.setSpacing(SPACE_SNUG)
        horizon_line.addWidget(self.horizon_field)
        horizon_line.addStretch(1)
        details_layout.addLayout(horizon_line)

        self.horizon_help = _note(
            "Draw the line on the picture above rather than guessing the "
            "number: a line set too low throws away real movement below it and "
            "never tells you it did. If the camera cannot be reached, leave the "
            "sky line off unless someone has read the number off a picture for "
            "you. Off is a perfectly safe setting."
        )
        details_layout.addWidget(self.horizon_help)

        # Only the half of WHY_REGIONS that is not already said above. What a
        # patch is FOR is now written under the button that opens this panel, and
        # having it twice on one card, in two wordings, is exactly the "too much
        # going on" this tab was cut down for. What is left is the half that
        # cannot be said anywhere else: which number is which.
        self.regions_help = _note(WHICH_NUMBER_IS_WHICH)
        details_layout.addWidget(self.regions_help)

        self.regions_list = QListWidget()
        self.regions_list.setToolTip(WHY_REGIONS)
        self.regions_list.setMaximumHeight(90)
        details_layout.addWidget(self.regions_list)

        # Two by two, and for the same reason this row was split off the row
        # above it before: the form column stops growing, so a row that asks for
        # more than it can have is not shrunk by Qt - it is clipped, at both
        # ends, and what came out was `elete the selected patc` beside four spin
        # boxes that had lost the words saying which number was which. Half a
        # column is 360 px and four of these need about 500, so the four numbers
        # are two lines now, and the button that lost its first and last letters
        # says the two words that matter.
        #
        # The four numbers first, then the two buttons that act on them, which
        # is also the order they are used in.
        numbers = QGridLayout()
        numbers.setHorizontalSpacing(SPACE_SNUG)
        numbers.setVerticalSpacing(SPACE_TIGHT)
        self.region_x = _region_box("across")
        self.region_y = _region_box("down")
        self.region_w = _region_box("wide")
        self.region_h = _region_box("tall")
        for index, box in enumerate(
            (self.region_x, self.region_y, self.region_w, self.region_h)
        ):
            numbers.addWidget(box, index // 2, index % 2)
        details_layout.addLayout(numbers)

        region_buttons = QHBoxLayout()
        region_buttons.setSpacing(SPACE_SNUG)
        self.add_region_button = QPushButton("Add this patch")
        self.remove_region_button = QPushButton("Delete patch")
        region_buttons.addWidget(self.add_region_button)
        region_buttons.addWidget(self.remove_region_button)
        region_buttons.addStretch(1)
        details_layout.addLayout(region_buttons)

        self.add_region_button.clicked.connect(self.add_region)
        self.remove_region_button.clicked.connect(self.remove_selected_region)
        self.set_regions([r.as_tuple() for r in stream.ignore_regions])

        self.details.setVisible(False)
        self.details_button.toggled.connect(
            lambda shown: self._unfold(self.details, shown)
        )
        # Inside the folded block, not beside it: a panel about the sky line of a
        # view nobody is watching is furniture.
        watch.addWidget(self.details)

        # What to say when a patch cannot be added. Set by SettingsTab so the
        # row does not need to know where the message line lives.
        self.on_problem = lambda text: None
        # And who to tell when this card changes size. Set by SettingsTab for
        # the same reason: the layout that has to be told is the one holding the
        # cards side by side, which belongs to the form and not to a card.
        # Harmless on a row built on its own, which is how the tests build one.
        self.on_refold = lambda: None

    # -------------------------------------------------------------- the folds

    def _unfold(self, block: QWidget, shown: bool) -> None:
        """Show or hide a folded block, and say so loudly enough to be believed.

        `setVisible` on its own is what drew text over text when **Ignore parts
        of the picture** was pressed: "How touchy:" landed on the last line of
        the note above it, and the sky-line note and the one under it were drawn
        through each other and through **Add a stream**.

        The card grows by about 420 px when this block opens, and it says so:
        asked directly, it answers with the new height the moment the block is
        shown. What never hears the answer is the grid that stands the cards
        side by side. Every sentence on this form is word-wrapped, so the whole
        column is laid out from heights-for-widths rather than from plain
        minimums, and each layout on the way up keeps the last height it worked
        out for the width it was asked about. Qt drops those along a chain of
        parent WIDGETS, and the grid is a layout inside a layout - not a widget,
        so it is not on the chain. It went on answering with the height the row
        was before the fold opened. The box above it was sized from that, and
        every control on the card was squeezed into a third of the room its own
        words need: at 1366x768 the card was 371 px of the 794 it asked for.

        Hence `on_refold`, which the form sets, and which puts the cards into
        the grid again from scratch - the same thing the form already does when
        a view is added or removed, and the one operation that leaves nothing
        remembered from the shape the row used to be. A card cannot reach the
        grid it sits in; the form can. The cost of getting this wrong is not
        cosmetic: it is the only screen the camera is set up from, drawn so that
        two settings cannot be told apart.
        """
        block.setVisible(shown)
        self.on_refold()

    # ------------------------------------------------------------- the values

    def values(self) -> StreamRow:
        """Name, address, used, reader - the last two no longer asked about.

        `True` and not a remembered value: a line on the list is a view in use.
        The reader comes back out of what this row arrived with, so a camera set
        up to read with ffmpeg keeps reading with ffmpeg across a save made by a
        form that never showed the choice.
        """
        return (
            self.name_field.text().strip(),
            self.url_field.text().strip(),
            True,
            self._base.get("reader", "auto"),
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


# What the tab says before anybody has pressed the button. An empty box is a
# black rectangle, and a black rectangle is not an answer to "what is the
# storage situation on this PC".
SCAN_INVITATION = (
    "Press Scan this PC and it will look at the drive this folder is on, then "
    "fill in a budget and an age rule that fit it. Nothing is written until you "
    "press Save."
)


@dataclass(frozen=True)
class DriveScan:
    """What a look at this computer's drive came to.

    `budget_gb` and `days` are None when nothing can be suggested - a drive that
    cannot be read, or one with no room left worth having. `words` is never
    empty: it is the whole point of the button. The operator asked for "a button
    that scans the PC storage situation and then automatically adjusts the
    parameters", and half of that is the scan saying what it found, in a
    sentence, rather than two numbers silently changing in two boxes.
    """

    words: str
    budget_gb: float | None = None
    days: int | None = None
    # The whole drive, in the units the budget is set in, so the slider can be
    # given a scale that means something physical rather than an invented one.
    drive_gb: float | None = None


def scan_drive(
    root: Path,
    rate_bytes_per_second: float,
    usage=None,
    recorded=None,
) -> DriveScan:
    """Look at the drive the recordings go on, and suggest what to do with it.

    The suggestion is the whole drive's free room, plus whatever VMD's own
    footage is already occupying - retention can delete that to make space, so
    it is not lost to us - less a slice kept back so the drive is never filled
    right up. A Windows laptop with no room at all cannot write its own page
    file, and this one is a console nobody logs into to clear space.

    The age rule is set to match what the budget holds rather than to some round
    number. The two rules delete for different reasons, and an age rule shorter
    than the budget throws away footage there was room for, while a longer one
    does nothing at all. Set equal, footage goes for one reason and the operator
    only has to understand one of them.

    Nothing is guessed when the drive cannot be read. A recordings folder on a
    drive letter with nothing behind it is the most likely mistake anybody makes
    setting this up, and answering it with a confident number would be worse
    than answering it with nothing.

    `usage` and `recorded` are the two touches of the filesystem, taken as
    arguments so this can be tested against a 1 TB drive on a machine that has
    not got one.
    """
    usage = usage or shutil.disk_usage
    recorded = recorded or recorded_bytes

    # The folder does not have to exist yet - first run is exactly the moment
    # this button is useful - so the question is asked of the nearest parent
    # that does, which is on the same drive.
    probe = Path(root)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        space = usage(str(probe))
    except OSError as exc:
        return DriveScan(
            f"The drive this folder is on could not be read: {_why(exc)}. "
            f"Nothing was changed. Check the folder above is on a drive this "
            f"computer can reach."
        )

    ours = recorded(root) or 0
    room = space.free + ours
    reserve = max(space.total * DRIVE_RESERVE_FRACTION, DRIVE_RESERVE_FLOOR_BYTES)
    budget_bytes = room - reserve
    drive_gb = space.total / 1024**3
    # A line per finding rather than one solid paragraph. This is a report, and
    # a report read once by somebody who is not sure what he is looking for is
    # read line by line or not at all. "0 KB" is not an amount of footage; the
    # first run has none, and saying so is shorter and truer.
    found = (
        f"This computer's drive holds {bytes_in_words(space.total)} and "
        f"{bytes_in_words(space.free)} of it is free. VMD's own footage on it "
        f"comes to {bytes_in_words(ours) if ours else 'nothing yet'}."
    )
    if budget_bytes < SMALLEST_WORTHWHILE_BUDGET_BYTES:
        return DriveScan(
            found + "\nThere is not enough room left on it to suggest anything. "
            "Free some space on this drive, or point the folder above at "
            "another one, and scan again.",
            drive_gb=drive_gb,
        )

    budget_gb = float(math.floor(budget_bytes / 1024**3))
    days = _days_of_footage(budget_gb, rate_bytes_per_second)
    holds = (
        "" if days is None else f" - about {_days_in_words(days)} of footage at "
        f"the rate this camera records"
    )
    lines = [
        found,
        f"Suggested budget: {budget_gb:.0f} GB{holds}. That is everything free "
        f"apart from a slice of the drive left alone, so it can never be filled "
        f"right up.",
    ]
    if days is not None:
        lines.append(
            f"Suggested delete older than: {_days_in_words(days)}, the same as "
            f"the budget holds, so footage goes for one reason and not two."
        )
    lines.append(
        "Both are suggestions and both are in the boxes below now. Change "
        "either one if it is not what you want, then press Save."
    )
    return DriveScan(
        "\n".join(lines), budget_gb=budget_gb, days=days, drive_gb=drive_gb
    )


def _days_of_footage(budget_gb: float, rate_bytes_per_second: float) -> int | None:
    """How far back a budget of this size lets him look, in whole days.

    None when there is nothing to divide by, and never zero: a budget that holds
    less than a day still holds something, and "0 days of footage" reads as a
    setting that does not work.
    """
    if rate_bytes_per_second <= 0 or budget_gb <= 0:
        return None
    return max(
        1, int(budget_gb * 1024**3 / rate_bytes_per_second / SECONDS_IN_A_DAY)
    )


def _days_in_words(days: int) -> str:
    """"1 day", not "1 days".

    The smallest possible carelessness, and it shows up in the state that
    matters most - a budget that holds a single day is the one worth reading
    twice. The storage panel has the same bug in "Roughly 1 minutes left" and it
    is not going to be copied into a control that did not have it.
    """
    return "1 day" if days == 1 else f"{days} days"


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


def _note(text: str) -> WrappedNote:
    """A sentence under a control, in the ink notes are written in.

    Every one of these used to be a tooltip and nothing else, and the operator
    read none of them: he went through this tab and asked, out loud, what four
    of its labels meant. The colour and the size come from the palette and the
    type scale, as everything drawn here does.
    """
    note = WrappedNote(text)
    note.setStyleSheet(f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;")
    return note


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
        # The folder and budget the operator has already been warned about, so
        # that the second press of Save goes ahead. See `_budget_warning`.
        self._warned_about: tuple[str, float] | None = None
        self._tools = tools
        # How `Scan this PC` asks the operating system about the drive. An
        # attribute so a test can hand it a 1 TB drive on a machine that has not
        # got one, and so the one filesystem call this tab makes from a button
        # press is in a place anybody can find.
        self.disk_usage = shutil.disk_usage
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
        # On the form rather than only in a tooltip. It used to explain the tick
        # box beside each view; with the tick gone it has to explain the rule
        # that replaced it, because "how do I stop using this camera" now has
        # exactly one answer and it is not on the card.
        self.streams_help = _note(
            "One card for each of the camera's views. Every view on this list is "
            "used: it is shown in the Live tab, it is recorded, and it is "
            "watched if you ask for that below. To stop using one, remove it."
        )
        streams_outer.addWidget(self.streams_help)
        # What "Watch for movement" means, said once for both cards rather than
        # printed on each of them. See the note where it used to live: with the
        # views side by side, per-card help is the same paragraph twice, six
        # inches apart, on the tab he called too busy.
        self.detect_help = _note(DETECT_HELP)
        streams_outer.addWidget(self.detect_help)
        # The other two paragraphs that used to be printed once per card, moved
        # here for the same reason and by the same argument. `fcd32f2` moved the
        # first of the three and left these; on screen they were the plainest
        # duplication left in the console - two paragraphs, side by side, word
        # for word identical, six inches apart. Everything they say is true of
        # both heads of the gimbal, so this is where they belong.
        #
        # Shown only while at least one view is being watched, which is what
        # `_show_stream_help` decides. Moving them here stopped them being
        # printed twice; it did not stop them being printed at all, and with
        # nothing watched these two paragraphs explain a control that is not on
        # the screen - three paragraphs of preamble before the first box, on the
        # tab whose complaint was that there is too much on it.
        #
        # The tick's own sentence above stays whatever happens: that tick is
        # always visible, and it is the one he asked about by name.
        self.classify_help = _note(CLASSIFY_HELP)
        streams_outer.addWidget(self.classify_help)
        self.ignore_help = _note(REGIONS_HELP)
        streams_outer.addWidget(self.ignore_help)
        # Side by side, two across. "Make the vis and thermal in the settings
        # side by side instead of one under the other, so it's easier" - and he
        # is right about why: it is one camera with two heads, he sets them up
        # against each other, and comparing them used to mean scrolling.
        self._streams_layout = QGridLayout()
        self._streams_layout.setHorizontalSpacing(SPACE_ROOM)
        self._streams_layout.setVerticalSpacing(SPACE_ROOM)
        for column in range(STREAM_COLUMNS):
            self._streams_layout.setColumnStretch(column, 1)
        streams_outer.addLayout(self._streams_layout)
        self.add_stream_button = QPushButton("Add a stream")
        self.add_stream_button.clicked.connect(lambda: self.add_stream_row())
        streams_outer.addWidget(self.add_stream_button)
        layout.addWidget(streams_box)

        detection_box = QGroupBox("Movement detection")
        detection_outer = QVBoxLayout(detection_box)
        detection_outer.setSpacing(SPACE_SNUG)

        self._detection_enabled = QCheckBox("Watch for movement at all")
        self._detection_enabled.setToolTip(
            "The master switch. Turning it off stops movement detection and "
            "nothing else - recording keeps running, because it is a separate "
            "program that shares nothing with this one."
        )
        detection_outer.addWidget(self._detection_enabled)
        detection_outer.addWidget(
            _note(
                "The master switch. With it off nothing is watched, whatever "
                "the camera views say. Recording carries on either way."
            )
        )

        # Behind the master switch, for the same reason the per-view controls
        # are behind theirs: they are answers to a question nobody is asking
        # until it is on. Hidden is not off - whatever is set here is still what
        # gets saved.
        self._detection_extras = QWidget()
        extras = QVBoxLayout(self._detection_extras)
        extras.setContentsMargins(SPACE_ROOM, SPACE_TIGHT, 0, 0)
        extras.setSpacing(SPACE_SNUG)
        extras.addWidget(
            _note(
                "These apply to every view at once. Which views are watched, "
                "and how, is set on each card above."
            )
        )

        # First of the extras, and the only one of them he is likely to change.
        # It is also the only setting on this tab that changes what happens in
        # the room rather than what happens in the software.
        self._alarm_sound = QCheckBox("Make a sound when something moves")
        self._alarm_sound.setToolTip(
            "A short sound when movement is reported, as well as the red strip "
            "across the bottom of the pictures.\n\n"
            "It is on because you are not always looking at the screen. It "
            "never sounds more than once every twelve seconds, so a windy night "
            "is one sound and not forty.\n\n"
            "Turn it off if somebody sleeps in this room."
        )
        extras.addWidget(self._alarm_sound)

        self._detection_classify = QCheckBox("Allow VMD to try to say what it was")
        self._detection_classify.setToolTip(
            "The master switch for naming things. With this off, nothing is "
            "ever named, whatever the individual views are set to. With it on, "
            "each view decides for itself.\n\n"
            "It needs an extra download to work, and at 700 m a person is only "
            "about 13 dots across, so it is off to begin with. You are told "
            "about the movement either way."
        )
        extras.addWidget(self._detection_classify)

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
        extras.addLayout(travel_line)
        detection_outer.addWidget(self._detection_extras)
        self._detection_enabled.toggled.connect(self._detection_extras.setVisible)
        self._detection_extras.setVisible(self._detection_enabled.isChecked())
        layout.addWidget(detection_box)

        # --- storage ---------------------------------------------------------
        #
        # "I want a button that scans the PC storage situation and then
        # automatically adjusts the parameters like the budget, delete older
        # than and so on. Make it nicer and easier, like a slider for the
        # budget. If the user wants, he can edit."
        #
        # Three boxes he was expected to invent numbers for, one of which
        # silently deletes footage when it is lowered. The scan works two of them
        # out from the drive that is actually there, the slider makes the third
        # a thing he can feel rather than a number he has to know, and the boxes
        # stay: a suggestion that cannot be overruled is a decision wearing a
        # suggestion's clothes.
        storage_box = QGroupBox("Storage")
        storage_outer = QVBoxLayout(storage_box)
        storage_outer.setSpacing(SPACE_SNUG)

        storage_form = _form()
        self._root = QLineEdit()
        storage_form.addRow("Folder", self._root)

        scan_line = QHBoxLayout()
        scan_line.setSpacing(SPACE_SNUG)
        self.scan_button = QPushButton("Scan this PC")
        self.scan_button.setToolTip(
            "Looks at the drive the folder above is on - how big it is, how much "
            "is free, how much VMD is already using - and fills in a budget and "
            "an age rule to match.\n\n"
            "It changes the two boxes below and nothing else. Nothing is written "
            "until you press Save."
        )
        self.scan_button.clicked.connect(self.scan_this_pc)
        scan_line.addWidget(self.scan_button)
        scan_line.addStretch(1)
        storage_form.addRow("", scan_line)

        # Across both columns of the form, not in the field column. It is a
        # paragraph, not a value, and squeezed into the width of a text box it
        # asked for three lines of room to draw one - which reads as an
        # unexplained hole above the sentence.
        self.storage_scan_note = _note(SCAN_INVITATION)
        storage_form.addRow(self.storage_scan_note)

        self._budget = QLineEdit()
        # Room for a number, not room for a sentence. Measured off the font
        # rather than typed as a pixel count, so it is right at whatever size
        # the console's own text is drawn.
        self._budget.setMaximumWidth(
            self._budget.fontMetrics().horizontalAdvance("000000000")
        )
        self.budget_slider = QSlider(Qt.Orientation.Horizontal)
        self.budget_slider.setRange(1, BUDGET_SLIDER_MAX_GB)
        self.budget_slider.setStyleSheet(SLIDER_STYLE)
        self.budget_slider.setToolTip(
            "How much of the drive VMD is allowed to fill with footage. When it "
            "is full the oldest footage is deleted to keep recording going."
        )
        budget_line = QHBoxLayout()
        budget_line.setSpacing(SPACE_SNUG)
        budget_line.addWidget(self.budget_slider, 1)
        budget_line.addWidget(self._budget)
        storage_form.addRow("Budget (GB)", budget_line)

        # What the number actually means. A budget in gigabytes is not a
        # quantity anybody has an instinct for; how far back he can look is.
        self.budget_days_note = _note("")
        storage_form.addRow("", self.budget_days_note)

        # Wired last, and that is load-bearing: `setRange` above moves a slider
        # that was sitting at zero, and a handler connected before this line
        # would fire while half the widgets it writes to did not exist yet.
        #
        # `_syncing_budget` is set while one of the pair is being moved by the
        # other, so that they cannot chase each other round in a circle. The box
        # is the setting; the slider is a way of moving it, which is why a
        # number typed past the end of the slider is kept rather than clamped.
        self._syncing_budget = False
        self.budget_slider.valueChanged.connect(self._budget_slid)
        self._budget.textChanged.connect(self._budget_typed)

        self._days = QLineEdit()
        self._days.setPlaceholderText("empty means never delete by age")
        storage_form.addRow("Delete older than (days)", self._days)
        storage_outer.addLayout(storage_form)
        layout.addWidget(storage_box)

        radio_box = QGroupBox("Radio")
        radio_form = _form(radio_box)
        self._radio_host = QLineEdit()
        self._radio_user = QLineEdit()
        self._radio_password = QLineEdit()
        radio_form.addRow("Address", self._radio_host)
        radio_form.addRow("Username", self._radio_user)
        radio_form.addRow("Password", self._radio_password)

        # In the Radio box on purpose, and not with the camera tools below.
        # This is a thing the console does BECAUSE of the radio: with no radio
        # set up it does nothing at all, and an operator who has just typed a
        # radio address is the one person who needs to see it exists.
        self.link_auto_field = QCheckBox(
            "Turn the picture down by itself when the link gets busy"
        )
        self.link_auto_field.setToolTip(
            "Every serious problem this system has had has been the radio link "
            "being full. When it fills up, the picture stutters and the camera "
            "takes seconds to answer the arrow keys, because the steering has "
            "to queue behind the video.\n\n"
            "With this ticked, VMD watches how busy the link is and asks the "
            "camera for a smaller picture when it is struggling, then a better "
            "one again once it has been quiet for a while. It changes things "
            "rarely, and each change makes the picture jump for a moment.\n\n"
            "Untick it to leave the camera exactly as it is set. Nothing else "
            "changes: the picture, the recording and the movement alarms all "
            "carry on."
        )
        radio_form.addRow("", self.link_auto_field)

        self.link_help = _note(
            "It never goes below the lowest picture you allow. If the link "
            "cannot carry even that, it says so in the Logs tab rather than "
            "spoiling the picture further."
        )
        radio_form.addRow("", self.link_help)
        layout.addWidget(radio_box)

        # "The camera - is it relevant anymore?" It is, and more than most of
        # this page: it is the only way to find out whether the camera answers
        # at all on a machine with no terminal and no second computer. What was
        # wrong was the title, which was `The camera` sitting one screen below a
        # box called `Camera` - so it read as a second place to configure the
        # same thing rather than as the place to test it.
        self.tools_box = tools_box = QGroupBox("Check the camera")
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
        # Ctrl+S, because the button is at the bottom of a form that is about
        # 1700 px tall on his screen and he reaches it by scrolling past
        # everything he has just typed. There was not one shortcut anywhere in
        # this console before this line.
        #
        # Read in this tab's own `keyPressEvent` rather than bound as a Qt
        # shortcut, which is the rule the rest of this console already follows
        # and states: `vmd/desktop/window.py` reads Esc and F11 the same way,
        # because a shortcut is delivered ahead of the key handler and a
        # swallowed key release is a camera left slewing. It also means this
        # only fires while the Settings tab is the thing being typed into,
        # which is the whole of when it should.
        self.save_button.setToolTip("Write these settings to the file  (Ctrl+S)")
        ending = QHBoxLayout()
        ending.setContentsMargins(0, 0, 0, 0)
        ending.setSpacing(SPACE_GROUP)
        ending.addWidget(self._message, 1)
        ending.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(ending)
        layout.addStretch(1)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Ctrl+S saves, from anywhere on this tab.

        The button is at the bottom of a form about 1700 px tall on his screen,
        so reaching it means scrolling back past everything he has just typed -
        and before this there was not one keyboard shortcut anywhere in this
        console. It is refused while a save is already being applied, for the
        same reason the button is disabled then: a second restart queued behind
        the first.
        """
        if (
            event.key() == Qt.Key.Key_S
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            if self.save_button.isEnabled():
                self.save()
            event.accept()
            return
        super().keyPressEvent(event)

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
    def link_auto(self) -> bool:
        """Whether the picture is matched to the link, or left as it is set.

        Reads and writes `bitrate.mode`, which already had `auto` and `manual`
        in it and until now had nothing acting on either.
        """
        return self.link_auto_field.isChecked()

    @link_auto.setter
    def link_auto(self, value: bool) -> None:
        self.link_auto_field.setChecked(bool(value))

    @property
    def detection_enabled(self) -> bool:
        return self._detection_enabled.isChecked()

    @detection_enabled.setter
    def detection_enabled(self, value: bool) -> None:
        self._detection_enabled.setChecked(bool(value))

    @property
    def alarm_sound(self) -> bool:
        return self._alarm_sound.isChecked()

    @alarm_sound.setter
    def alarm_sound(self, value: bool) -> None:
        self._alarm_sound.setChecked(bool(value))

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

    # ---------------------------------------------------------------- storage

    def scan_this_pc(self) -> None:
        """Look at the drive, say what is on it, and fill in the two numbers.

        The two settings this fills in are the ones he had no way of arriving
        at: a budget in gigabytes and an age in days, on a form that never told
        him how big the drive was. Both stay editable, and neither is written
        anywhere until Save - this button changes two boxes and a sentence.
        """
        root = Path(self.storage_root.strip() or "recordings")
        if not root.is_absolute():
            root = self.settings_path.parent / root
        found = scan_drive(root, self._footage_rate(), usage=self.disk_usage)
        self.storage_scan_note.setText(found.words)
        if found.drive_gb is not None:
            # The slider now measures something real: how much of THIS drive the
            # footage may take. Before a scan it has an invented scale, because
            # nothing has asked the drive how big it is - and a handle sitting at
            # 5% of an invented scale says nothing at all.
            self.budget_slider.setMaximum(
                max(int(found.drive_gb), int(found.budget_gb or 0), 1)
            )
        if found.budget_gb is None:
            return  # nothing could be worked out, and the sentence says why
        self.budget_gb = f"{found.budget_gb:.0f}"
        if found.days is not None:
            self.retention_days = str(found.days)

    def _footage_rate(self) -> float:
        """How fast footage arrives, in bytes a second. An estimate, and a
        pessimistic one.

        The same arithmetic the storage panel uses when it has nothing measured
        to go on: the bitrate the camera has been asked to fit inside, times the
        number of views being recorded. It is deliberately the high end - a
        thermal head watching a still perimeter undershoots it by a lot - so the
        days it works out are the fewest he will get rather than the most.

        The views come from the form and the ceiling from the settings that were
        loaded, because the first is on this screen and the second is not.
        """
        views = sum(1 for _name, url, _used, _reader in self.streams() if url) or 1
        return max(self._loaded.bitrate.ceiling_kbps, 1) * 1000.0 / 8.0 * views

    def _budget_slid(self, value: int) -> None:
        if self._syncing_budget:
            return
        self._syncing_budget = True
        try:
            self._budget.setText(str(int(value)))
        finally:
            self._syncing_budget = False
        self._say_how_many_days()

    def _budget_typed(self, text: str) -> None:
        """Move the handle to match what was typed, without touching the text.

        A number past the end of the slider parks the handle at the end and is
        otherwise left completely alone. The box is the setting: rewriting what
        somebody typed is how a form loses a field, and this one is the field
        that deletes footage when it goes down.
        """
        if not self._syncing_budget:
            self._syncing_budget = True
            try:
                try:
                    wanted = int(round(float(text)))
                except ValueError:
                    wanted = self.budget_slider.value()  # half-typed, or empty
                self.budget_slider.setValue(
                    max(
                        self.budget_slider.minimum(),
                        min(self.budget_slider.maximum(), wanted),
                    )
                )
            finally:
                self._syncing_budget = False
        self._say_how_many_days()

    def _say_how_many_days(self) -> None:
        """How far back this budget lets him look, beside the budget."""
        try:
            budget_gb = float(self._budget.text())
        except ValueError:
            budget_gb = 0.0
        days = _days_of_footage(budget_gb, self._footage_rate())
        if days is None:
            self.budget_days_note.setText("")
            return
        self.budget_days_note.setText(
            f"≈ {_days_in_words(days)} of footage before the oldest starts "
            f"being deleted."
        )

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
        row.on_refold = self._refold
        self._rows.append(row)
        self._lay_the_cards_out()
        # A view added or removed changes how fast the disk fills, which changes
        # how many days the budget buys.
        self._say_how_many_days()
        return row

    def remove_stream_row(self, row: StreamRowWidget) -> None:
        if row not in self._rows:
            return
        self._rows.remove(row)
        self._streams_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._lay_the_cards_out()
        self._say_how_many_days()

    def _lay_the_cards_out(self) -> None:
        """Put the cards back in the grid, in order, two across.

        Rebuilt from `self._rows` on every change rather than patched, because a
        grid cell is a position and the rows move: removing the first of three
        leaves a hole in the top left and the third card where the third card
        was. Taking the items out does not unparent the widgets - they stay
        children of the box and are simply placed again.

        Top-aligned, so that one card with its detection settings unfolded does
        not stretch the empty card beside it to the same height.
        """
        self._show_stream_help()
        while self._streams_layout.count():
            self._streams_layout.takeAt(0)
        for index, row in enumerate(self._rows):
            self._streams_layout.addWidget(
                row,
                index // STREAM_COLUMNS,
                index % STREAM_COLUMNS,
                Qt.AlignmentFlag.AlignTop,
            )

    def _refold(self) -> None:
        """A card has folded or unfolded, so the form above it is out of date.

        Qt invalidates layouts by walking parent WIDGETS, and the grid that puts
        the cards side by side is a layout inside the Streams box's layout - not
        a widget, so nothing on that walk ever reaches it. It goes on answering
        with the height it worked out for the row before the fold opened, and
        everything above it is sized from that stale answer. On screen that was
        "How touchy:" drawn through the note above it and the whole card
        squeezed to a third of the height its own words need.

        Telling it is not enough - `invalidate` schedules the recalculation and
        a layout answers `heightForWidth` out of its cache without doing it. So
        the cards go into the grid again from scratch, which is what the form
        already does when a view is added or removed and the only operation that
        leaves nothing at all remembered from the shape the row used to be. It
        costs a re-layout of two widgets on a button press.
        """
        self._show_stream_help()
        self._lay_the_cards_out()

    def _show_stream_help(self) -> None:
        """Only explain the controls that are actually on the screen.

        Two of the three paragraphs above the cards are about settings that are
        folded away until a view is being watched, which on a console nobody has
        set up yet is always. Explaining a control he cannot see is the same
        cost as explaining one twice - it is text between him and the box he
        came here to type in.
        """
        watched = any(row.detect_field.isChecked() for row in self._rows)
        self.classify_help.setVisible(watched)
        self.ignore_help.setVisible(watched)

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
        self.link_auto = settings.bitrate.mode == "auto"
        self.detection_enabled = settings.detection.enabled
        self.alarm_sound = settings.detection.alarm_sound
        self.detection_classify = settings.detection.classify
        self.min_travel_px = (
            "" if settings.detection.min_travel_px is None else str(settings.detection.min_travel_px)
        )
        # The whole stream, not four of its fields: the detection choices belong
        # to the row that shows them, and a row that was handed only a name and
        # an address would write the defaults back over them at the next save.
        self.set_streams(list(settings.camera.streams))
        self._set_message(" ".join(part for part in (problem, _adopted(settings)) if part))

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

        warning = self._budget_warning(settings)
        if warning:
            self._set_message(warning)
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

    def _budget_warning(self, settings: Settings) -> str:
        """Say what lowering the budget is about to delete, once, before it does.

        This is the only irreversible destructive action in the whole interface
        and it looks like an ordinary text field. Typing 10 where 100 was meant
        has retention delete about 90 GB of footage on its next pass -
        permanently, with no question asked and no line anywhere saying it is
        about to happen.

        Two presses of Save, not a dialog. A modal over a console can be
        dismissed by a stray keypress from somebody steering the camera, and it
        arrives on top of the one screen this operator has; two presses cannot
        be dismissed by accident and cost him one extra click on the day he
        really does mean it - which he will, because this is a real thing he
        needs to be able to do.

        Only the case that destroys something asks. Raising the budget, leaving
        it alone, and lowering it to something the folder is still inside all
        save on the first press, as every other setting does.
        """
        storage = settings.storage
        if not storage.budget_enabled:
            return ""
        if storage.budget_gb >= self._loaded.storage.budget_gb:
            return ""

        root = Path(storage.root)
        if not root.is_absolute():
            root = self.settings_path.parent / root
        used = recorded_bytes(root)
        # Nothing can be said about what would be deleted if the folder cannot
        # be looked at - and a folder that cannot be read is already a sentence
        # of its own from `storage_problem`, one line above this.
        if used is None or used <= storage.budget_bytes:
            return ""

        # Asked once per number. Correcting the number he mistyped re-arms it,
        # which is right: the second figure is a different amount of footage.
        asked = (str(root), storage.budget_gb)
        if self._warned_about == asked:
            return ""
        self._warned_about = asked
        return (
            f"This will delete about {bytes_in_words(used - storage.budget_bytes)} "
            f"of the oldest footage, and it cannot be undone. "
            f"Press Save again to go ahead."
        )

    def report_progress(self, text: str) -> None:
        """What is being done to the running system, while it is being done.

        The file is written the moment Save is pressed; putting it into effect
        means restarting up to three child processes, which happens on a worker
        and takes seconds. This says which one is being restarted, and holds the
        button while it is - a button that can be pressed again mid-restart is
        a second restart queued behind the first.

        Drawn quiet, not amber: this is the console doing what it was told, and
        amber on this line is reserved for something that did not happen.
        """
        self.save_button.setEnabled(False)
        self._set_message(text, quiet=True)

    def report_after_save(self, text: str) -> None:
        """Replace what was being done with what actually took effect.

        The file was written; that is what "Saved." means and it is not a lie.
        But a child that would not restart is still running the settings the
        operator just replaced, and this line is the only place on this machine
        where that can be said to them.
        """
        self.save_button.setEnabled(True)
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
            alarm_sound=self.alarm_sound,
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
        # Only the switch. The floor, the ceiling and the by-hand rate are not
        # on this form: they are numbers in kilobits that mean nothing to the
        # operator, and the two that matter already have sensible values. What
        # he needs on the page is the one thing he might want to stop.
        payload["bitrate"] = dict(payload.get("bitrate", {}))
        payload["bitrate"]["mode"] = "auto" if self.link_auto else "manual"
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
        for name, url, _used, _reader in self.streams():
            if not url:
                # There is no tick to talk about any more: a card on the list is
                # a view in use, so the two ways out of this are the address and
                # the Remove button.
                return (
                    f'"{name or "A view"}" has no address. Type one in, or '
                    f"remove it."
                )
            if url and not name:
                return "A stream has an address but no name."
            if name and name in seen:
                return f'Two streams are both called "{name}".'
            if name:
                seen.add(name)
        return ""

    def _set_message(self, text: str, quiet: bool = False) -> None:
        """One line under the Save button, in the ink its news deserves.

        Amber is for something the operator has to do something about. `quiet`
        is for the console describing itself - "Saved.", and the steps of
        putting a save into effect - which is news but not a problem.
        """
        self.message = text
        self._message.setText(text)
        colour = PALETTE["muted"] if quiet or text in ("", "Saved.") else PALETTE["warn"]
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
        f"link floor    : {settings.bitrate.floor_kbps} kb/s",
        # Whether anything is moving the bitrate at all. A report that shows a
        # ceiling and a floor without saying whether they are being acted on is
        # a report that will be read as "the picture is being managed" whichever
        # way the switch is set.
        f"link follows  : "
        f"{'automatically' if settings.bitrate.mode == 'auto' else 'left as set by hand'}",
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


def _adopted(settings: Settings) -> str:
    """What to say about a view the file has switched off, if there is one.

    This is the decision about `enabled: false` in a file written while there
    was still a tick box for it. Two things could happen to such a view and only
    one of them is defensible.

    Leaving it off would leave a setting with no control anywhere in this
    console: a camera view he can see on the form, cannot switch back on, and is
    given no reason for. He has no terminal and no second machine, so "edit the
    JSON" is not a way back - it is the end of the road.

    So it is adopted, like every other line on the list. What is not acceptable
    is doing that silently, because somebody may have meant it: hence this
    sentence, which names the view, says what happens at the next Save, and
    points at the control that still expresses "I do not want this view", which
    is Remove.
    """
    off = [
        stream.name or "a view with no name"
        for stream in settings.camera.streams
        if not stream.enabled
    ]
    if not off:
        return ""
    named = " and ".join(f'"{name}"' for name in off)
    was = "was" if len(off) == 1 else "were"
    it = "it" if len(off) == 1 else "they"
    its = "its" if len(off) == 1 else "their"
    return (
        f"{named} {was} switched off in the settings file. There is no longer a "
        f"switch for that - every view on this list is a view in use - so {it} "
        f"will be used again from the next Save. Remove {its} card if that is "
        f"not what you want."
    )


# What each setting is called on the screen, keyed by where pydantic says the
# trouble is. An index inside a list is written `*`, since one bad camera view
# is described by the view, not by its position on the page.
#
# This exists because a refused save was naming the field by its Python
# attribute path - `storage.retention_days` - to a man who has never seen the
# source and cannot see the file. On a form he has to scroll, that leaves the
# one sentence telling him what to correct unable to tell him where it is:
# every box here has a label a finger's width from it, and the label is the
# only name for it he has ever been shown.
#
# Anything not named here keeps its path, which is worse than a label and much
# better than nothing. The rule for adding a field to the form is that its
# label is added here at the same time.
FIELD_LABELS = {
    "camera.host": "Address, under Camera",
    "camera.username": "Username, under Camera",
    "camera.password": "Password, under Camera",
    "camera.streams.*.name": "the name of a camera view",
    "camera.streams.*.url": "the address of a camera view",
    "detection.min_travel_px": "Must travel at least (dots)",
    "storage.root": "Folder, under Storage",
    "storage.budget_gb": "Budget (GB)",
    "storage.retention_days": "Delete older than (days)",
    "radio.host": "Address, under Radio",
    "radio.username": "Username, under Radio",
    "radio.password": "Password, under Radio",
}

# What a number that would not parse should be called, by the name pydantic
# gives the failure. The library's own sentence - "Input should be a valid
# integer, unable to parse string as an integer" - says the same thing twice
# and says neither half in words anybody uses.
WANTED_NUMBER = {
    "int_parsing": "a whole number",
    "int_type": "a whole number",
    "float_parsing": "a number",
    "float_type": "a number",
}


def _first_problem(exc: Exception) -> str:
    """One readable sentence out of a validation error.

    Readable means two things and they are separate: the field is named as the
    screen names it, and the complaint is said in words rather than in the
    validator's. Where either is unknown the raw form is kept - a sentence that
    is technical is still a sentence, and one that has been dropped is not.
    """
    if not isinstance(exc, ValidationError):
        return str(exc)
    first = exc.errors()[0]
    path = ".".join(
        "*" if isinstance(part, int) else str(part) for part in first["loc"]
    )
    where = FIELD_LABELS.get(path) or path or "settings"
    wanted = WANTED_NUMBER.get(str(first.get("type", "")))
    typed = first.get("input")
    if wanted is not None and isinstance(typed, str):
        return f'{where}: "{typed}" is not {wanted}.'
    # "Value error, " is pydantic announcing which of its own machinery raised;
    # what follows it is the sentence this codebase wrote for the operator.
    said = str(first["msg"]).removeprefix("Value error, ")
    return f"{where}: {said}"
