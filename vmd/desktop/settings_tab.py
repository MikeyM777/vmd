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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
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

# Naming what moved is gone from this tab, and with it the paragraph that used
# to explain why the thermal head was treated differently. His instruction: "I
# need movement notifications, but not accurate identification." See
# `vmd/detect/config.py`, where it is now switched off at the source rather than
# merely unticked - a control that is off by default is a control somebody turns
# on one afternoon, and this one bought a guess he could not check.
#
# `Heat camera` went with it. Traced through the whole codebase, `stream.thermal`
# had exactly one consumer - the line that decided whether naming ran - so with
# naming gone the tick was a question with no consequence at all. Both fields
# stay in the model: a settings file in the field has them, and a field removed
# from the model is a file that stops loading on a laptop with no terminal.

# The three sentences he asked for, in the only place he will read them: on the
# form, under the control, in the ink notes are written in.
#
# "'Watch for movement' - what is that?", "'Skyline and ignore...' - what is
# that?" - both asked by the person these labels were written for. A tooltip is read by whoever hovers over that
# one control, which on a console driven from two metres back is nobody. The
# review of this tab says it plainly: a control whose name only makes sense on
# hover is a control with the wrong name, and the tooltips here were already
# doing more work than tooltips should.
DETECT_HELP = (
    "With this on, anything that moves in this view is written into the movement "
    "list and lights the red strip across the bottom of the pictures. Recording "
    "carries on either way - this only decides whether you are told."
)

REGIONS_HELP = (
    "The sky, a road you do not care about, a tree that moves in the wind - "
    "anything you mark in here is not reported. Everything outside it is still "
    "watched."
)

SENSITIVITY_CHOICES: list[tuple[str, str]] = [
    ("Low - only big, obvious movement", "low"),
    ("Normal", "normal"),
    ("High - notices small or distant movement", "high"),
]

# `WHY_HORIZON`, `WHICH_NUMBER_IS_WHICH` and `WHY_REGIONS` stood here, and all
# three were paragraphs explaining a number in dots of the camera's own frame:
# how far down the sky line was, and how far across, down, wide and tall a patch
# to ignore was. Not one of those is a quantity anybody can see or check against
# a picture he is not looking at, which is why every one of them needed a
# paragraph. They are drawn now - see `vmd/desktop/mask.py` - and a picture with
# a mouse on it needs no paragraph at all.


class StreamRowWidget(QFrame):
    """One camera view, as a card: a name, an address, and - folded away until
    it is asked for - whether it is watched and how. Nothing about it is fixed:
    a camera calls its streams whatever it likes and the form has to keep up.

    Every one of those choices lives on this widget rather than in a list held
    beside it, because that is what makes reordering rows safe. A detection
    setting matched to a stream by position is a setting waiting to land on the
    wrong head.

    Four things it used to ask and no longer does.

    **Whether the view is used at all.** It was a tick box called `Use this
    view`, and the operator's verdict was "useless, of course use that view, if
    it's added". The reward for remembering the tick was the state you were
    already in, and the price of missing it was a camera silently not shown,
    not recorded and not watched. `enabled` is still in the settings file and
    everything downstream still reads it; this form always writes True.

    **Which client reads the stream.** `auto` or `ffmpeg`, and the honest
    explanation is "try the other one if the picture will not come up", which is
    not a question to put to somebody setting a camera up for the first time.
    The setting keeps working and a file that says `ffmpeg` still says `ffmpeg`
    after a save; it is just not on the screen.

    **Whether this is the heat camera.** Traced right through: `stream.thermal`
    had exactly one consumer in the whole codebase, and that was the line
    deciding whether naming ran. Naming is gone, so the tick was a question with
    no consequence anywhere.

    **Whether to try to name what moved.** His instruction, in his words: "I
    need movement notifications, but not accurate identification."

    The card can no longer be removed either, and that is deliberate rather than
    an omission: see `SettingsTab._build_streams_box`.

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

        # `Remove` was here, beside the name. It is gone, and the reason is
        # asymmetry: with `Add a stream` gone too, one stray click on it costs a
        # camera permanently and the only way back is hand-editing JSON on a
        # machine with no terminal. The two views are a fixed property of a
        # two-sensor camera on a gimbal, not a list anybody curates.
        top.addWidget(self.name_field, 1)
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
        # Named after the view it is about, and re-named as the name is typed:
        # see `_name_the_watch_switch`.
        self.detect_field = QCheckBox()
        self.detect_field.setChecked(stream.detect)
        self.detect_field.setToolTip(
            "Watch this view and raise an alert when something moves in it.\n\n"
            "Off until you turn it on. A detector pointed at a treeline before "
            "anyone has told it about the trees alarms all day, and an alarm "
            "nobody believes is worse than none."
        )
        self._name_the_watch_switch()
        self.name_field.textChanged.connect(
            lambda _typed: self._name_the_watch_switch()
        )
        # Which of the camera's lenses this picture's zoom bar drives.
        #
        # Hidden until the camera has said it has more than one, which is what
        # `set_lenses` decides: on a single-sensor camera this is a question
        # with one answer, and a control offering one answer is furniture on a
        # tab whose complaint was that there is too much on it.
        #
        # It exists because the automatic answer is a guess about a vendor's
        # naming, and a wrong guess is silent - the camera accepts the command
        # and carries it out on the other picture. He reported exactly that:
        # "only the vis is zooming".
        self.lens_row = QWidget()
        lens_line = QHBoxLayout(self.lens_row)
        lens_line.setContentsMargins(0, 0, 0, 0)
        lens_line.setSpacing(SPACE_SNUG)
        lens_label = QLabel("Zoom drives")
        self.lens_field = QComboBox()
        self.lens_field.setToolTip(
            "Which of the camera's lenses this picture's zoom slider moves.\n\n"
            "Leave it on \"Work it out for me\" unless the slider moves the "
            "wrong picture, or does nothing. Then pick another one and try it - "
            "you can see which picture answers."
        )
        lens_line.addWidget(lens_label)
        lens_line.addWidget(self.lens_field, 1)
        outer.addWidget(self.lens_row)
        self.set_lenses([], stream.ptz_profile)

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
        # "Too much going on." Folded, never deleted: he has said he wants to
        # test movement detection in the next days, and a setting that has been
        # removed is not a setting that can be tested.
        self.watched = QWidget()
        watch = QVBoxLayout(self.watched)
        watch.setContentsMargins(SPACE_ROOM, SPACE_TIGHT, 0, 0)
        watch.setSpacing(SPACE_TIGHT)

        # The one thing on this card he actually does. It used to be called
        # "Sky line and ignored patches" - two nouns lifted out of the source and
        # joined by an "and" - then "Ignore parts of the picture", which named
        # the action but not the thing. It is a list of parts of the picture, so
        # that is what it says.
        self.mask_button = QPushButton("Parts to ignore")
        self.mask_button.setToolTip(
            "Shows one picture from this view and lets you draw round anything "
            "you do not want reported: the sky, a road, a tree that sways.\n\n"
            "Draw it, do not type it. Nothing in here is a number you could "
            "have known."
        )
        # Set by SettingsTab, which is the only thing that knows how to reach a
        # camera. A row on its own can still be built and tested with nothing
        # installed.
        self.on_pick = lambda row: None
        self.mask_button.clicked.connect(lambda: self.on_pick(self))
        watch.addWidget(self.mask_button)

        # --- and, behind a door, the one knob that tunes a treeline ----------
        #
        # He asked for **How touchy:** to be removed or hidden. Hidden: he has
        # said he wants to test movement detection over the coming days, and
        # sensitivity is the only control that tunes a detector pointed at a
        # treeline 700 m away. Removing it would leave him with a detector that
        # either alarms all night or says nothing, and no way to move between
        # those two states.
        #
        # Behind "Advanced" and defaulting to Normal, so that a console nobody
        # has set up yet asks him one question per view and not two.
        self.advanced_button = _fold_button(
            "Advanced",
            "One setting, for a view that alarms too much or too little. "
            "Leave it alone until it does.",
        )
        watch.addWidget(self.advanced_button)

        self.advanced = QWidget()
        advanced = QVBoxLayout(self.advanced)
        advanced.setContentsMargins(SPACE_ROOM, SPACE_TIGHT, 0, 0)
        advanced.setSpacing(SPACE_TIGHT)

        self.sensitivity_field = QComboBox()
        for label, value in SENSITIVITY_CHOICES:
            self.sensitivity_field.addItem(label, value)
        self.sensitivity_field.setToolTip(
            "How much movement it takes before you are told.\n\n"
            "High notices more, including more wind, rain and shadows. Low "
            "notices only large, clear movement. Start at Normal."
        )
        self.set_sensitivity(stream.sensitivity)
        # A label above its box rather than beside it. In half a column there is
        # no room for both, and the thing that has to be readable is the choice
        # itself: "High - notices small or distant movement" is 250 px of words
        # and it is what the operator is picking between.
        self.sensitivity_label = QLabel("How touchy:")
        self.sensitivity_label.setToolTip(self.sensitivity_field.toolTip())
        advanced.addWidget(self.sensitivity_label)
        advanced.addWidget(self.sensitivity_field)
        self.advanced.setVisible(False)
        self.advanced_button.toggled.connect(
            lambda shown: self._unfold(self.advanced, shown)
        )
        watch.addWidget(self.advanced)

        outer.addWidget(self.watched)
        self.detect_field.toggled.connect(
            lambda shown: self._unfold(self.watched, shown)
        )
        self.watched.setVisible(self.detect_field.isChecked())

        # The areas he has drawn, held as points and never shown as numbers.
        #
        # `120 x 80 dots, at 30 across and 40 down` was on this card, four
        # times over, in a list beside four spin boxes - and it is exactly what
        # he asked to be rid of. A dot of a camera frame is not a quantity
        # anybody can see, estimate or check, so the only honest control for one
        # is the picture itself.
        #
        # `ignore_regions` - the older rectangles - are NOT read into this and
        # NOT written from it. They are carried across a save untouched inside
        # `_base`, like every other field this form has stopped showing. The
        # detector still honours them; converting them here would be this form
        # rewriting a setting the operator never touched.
        self._shapes: list[list[tuple[int, int]]] = [
            shape.as_tuples() for shape in stream.ignore_shapes
        ]
        self._say_how_many_areas()

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

        `setVisible` on its own is what drew text over text when the fold under
        a card was opened: "How touchy:" landed on the last line of the note
        above it, and two more sentences were drawn through each other.

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

    # -------------------------------------------------------------- the name

    def _name_the_watch_switch(self) -> None:
        """Say which view this switch is about, in the name typed above it.

        There were three controls on this tab whose names were nearly the same
        sentence - one on each camera card and the master switch below them -
        and the two on the cards were word for word identical: **Watch for
        movement**, six inches apart, with nothing in either of them saying
        which head of the gimbal it belonged to. Ticking one of two identical
        boxes and then scrolling to a third called **Watch for movement at all**
        is not a thing anybody should have to reason about at three in the
        morning.

        So the card's switch says the view's own name, which is the one word on
        the card that already tells the two of them apart, and it follows the
        name field as it is typed: a card whose name has just been corrected
        must not go on offering to watch the old one. Before a name has been
        typed there is nothing to name it by, and "this view" is the truth.
        """
        name = self.name_field.text().strip()
        self.detect_field.setText(
            f"Watch {name} for movement" if name else "Watch this view for movement"
        )

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

    def sensitivity(self) -> str:
        return self.sensitivity_field.currentData()

    def set_sensitivity(self, value: str) -> None:
        for index in range(self.sensitivity_field.count()):
            if self.sensitivity_field.itemData(index) == value:
                self.sensitivity_field.setCurrentIndex(index)
                return
        self.sensitivity_field.setCurrentIndex(1)  # normal

    def shapes(self) -> list[list[tuple[int, int]]]:
        """The areas drawn on this view, as points. Never words, never numbers."""
        return [list(shape) for shape in self._shapes]

    def set_shapes(self, shapes) -> None:
        self._shapes = [
            [(int(x), int(y)) for x, y in shape] for shape in shapes
        ]
        self._say_how_many_areas()

    def _say_how_many_areas(self) -> None:
        """Count them on the button, and nothing else about them.

        The count is the one fact about a set of drawn areas that is worth a
        word: it says whether pressing this opens an empty picture or one that
        has been marked up, which is the only question anybody has before
        opening it. What is NOT here is where they are or how big - `120 x 80
        dots, at 30 across and 40 down` is what he asked to be rid of, and it
        was never a sentence anybody could check against a picture they could
        not see.
        """
        count = len(self._shapes)
        if not count:
            self.mask_button.setText("Parts to ignore")
        elif count == 1:
            self.mask_button.setText("Parts to ignore  (1 marked)")
        else:
            self.mask_button.setText(f"Parts to ignore  ({count} marked)")

    def set_lenses(self, profiles: list[dict], current: str = "") -> None:
        """Offer the camera's lenses, keeping whatever was already chosen.

        A token saved against a different camera is kept as an entry of its own
        rather than dropped, because silently resetting a setting the operator
        made is how he ends up not trusting the form. `Lenses` refuses to send
        an unknown token anyway, and says so in the log.
        """
        chosen = current or self.chosen_lens()
        self.lens_field.blockSignals(True)
        try:
            self.lens_field.clear()
            self.lens_field.addItem("Work it out for me", "")
            for profile in profiles:
                token = str(profile.get("token", ""))
                if not token:
                    continue
                name = str(profile.get("name") or "").strip()
                # Said plainly when the camera has told us. A lens with no PTZ
                # cannot zoom whatever is pointed at it, and offering it without
                # saying so is offering a setting that cannot work.
                cannot = "" if profile.get("can_zoom", True) else "  (cannot zoom)"
                label = f"{name} - {token}" if name else token
                self.lens_field.addItem(f"{label}{cannot}", token)
            if chosen and self.lens_field.findData(chosen) < 0:
                self.lens_field.addItem(f"{chosen}  (not on this camera)", chosen)
            index = self.lens_field.findData(chosen)
            self.lens_field.setCurrentIndex(max(index, 0))
        finally:
            self.lens_field.blockSignals(False)
        # One lens is not a choice, and neither is none.
        self.lens_row.setVisible(len(profiles) > 1 or bool(chosen))

    def chosen_lens(self) -> str:
        data = self.lens_field.currentData()
        return "" if data is None else str(data)

    def stream_values(self) -> dict:
        """Everything this row knows, as StreamSettings would take it.

        Built on top of what the stream arrived with, so a field this form has
        never heard of survives a save rather than being reset to its default.

        Four fields the form used to write are now only carried: `thermal`,
        `classify`, `horizon_y` and `ignore_regions`. They are not named below,
        which is exactly how they survive - `payload` starts from what was
        loaded, so whatever the file said about them is still in this dictionary
        and goes back out unchanged. Writing a default over a setting the form
        stopped showing would be the same failure as deleting a stream, one
        field along.
        """
        name, url, enabled, reader = self.values()
        payload = dict(self._base)
        payload.update(
            name=name,
            url=url,
            enabled=enabled,
            reader=reader,
            detect=self.detect_field.isChecked(),
            ptz_profile=self.chosen_lens(),
            sensitivity=self.sensitivity(),
            ignore_shapes=[
                {"points": [list(point) for point in shape]} for shape in self.shapes()
            ],
        )
        return payload


def lens_lines(answer: dict) -> list[str]:
    """What the camera said about its lenses, in words. Pure, and tested as such.

    Written for somebody who has never heard of a media profile. What he needs
    from it is one thing: whether each picture's zoom slider is pointed at that
    picture's lens - and, when it is not, that he can change it on the card.
    """
    if not answer.get("ok"):
        reason = str(answer.get("error") or "the camera did not say").strip()
        return [f"Could not ask the camera about its lenses: {reason}"]

    profiles = list(answer.get("profiles") or [])
    using = dict(answer.get("using") or {})
    lines = [f"The camera has {len(profiles)} picture(s) it can send:"]
    for profile in profiles:
        token = str(profile.get("token", ""))
        name = str(profile.get("name") or "").strip()
        called = f"{name} ({token})" if name else token
        driving = [view for view, chosen in using.items() if chosen == token]
        who = f" - the zoom slider under {', '.join(driving)}" if driving else ""
        cannot = "" if profile.get("can_zoom", True) else "  ** this one cannot zoom **"
        lines.append(f"    {called}{who}{cannot}")

    missing = [view for view, chosen in using.items() if not chosen]
    for view in missing:
        lines.append(f"    {view} has no lens behind it, so its zoom slider cannot work.")

    if answer.get("shared") and len(using) > 1:
        lines.append("")
        lines.append(
            "Both pictures are on the same lens, so either slider moves both. "
            "That is what this camera reported, not a fault in VMD."
        )
    lines.append("")
    lines.append(
        "If a slider moves the wrong picture, pick a different lens under "
        "\"Zoom drives\" on that camera's card above, then press Save and try it."
    )
    return lines


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
    "Press the button above and it will look at the drive this folder is on, "
    "then fill in a size and an age rule that fit it. Nothing is written until "
    "you press Save."
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


def drive_under(root) -> Path:
    """The nearest folder above `root` that exists, which is on the same drive.

    The recordings folder does not have to be there yet - first run is exactly
    the state the storage controls are useful in - and asking the operating
    system about a folder that is not there answers with an error rather than
    with the drive it would be on.
    """
    probe = Path(root)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def drive_name(root) -> str:
    """What Windows calls the drive this folder is on: `C:`, or empty.

    On the console this is always a drive letter, and it is the whole point of
    printing it: it is the one word that lets him put VMD's figure and This PC's
    figure side by side. `Path.anchor` is `C:\\`; the trailing separator is
    dropped because nothing on that screen writes one.
    """
    anchor = Path(drive_under(root)).anchor
    return anchor.rstrip("\\/") if anchor else ""


# Why the number VMD prints is not the number on the laptop's own label.
#
# His words: "the laptop have 950gb". `bytes_in_words` divides by 1024 three
# times and calls the answer GB, so a drive sold as 950 GB - which is
# 950,000,000,000 bytes - prints as 884 GB. Both figures are correct and they
# count a gigabyte differently.
#
# The decision was to keep dividing by 1024, for one reason: Windows does the
# same and labels it GB too, so This PC on his own machine says 884 as well.
# Printing GiB would be exact and would match nothing he can put beside it;
# printing decimal GB would match the sticker and disagree with the only other
# place on the console he could check. So VMD prints what Windows prints, names
# the drive so the two can be compared, and says once - here, where the figure
# first appears - why the sticker is a bigger number. A number he thinks is
# wrong is a number he stops believing, and this is the number the whole
# storage box is about.
WHY_THE_STICKER_DISAGREES = (
    "That is the same figure Windows shows for this drive under This PC. It is "
    "smaller than the size printed on the laptop - a drive sold as \"950 GB\" "
    "shows as about 884 GB in Windows - because the two count a gigabyte "
    "differently. Nothing is missing."
)


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

    probe = drive_under(root)
    named = drive_name(root)
    called = f"Drive {named}" if named else "This computer's drive"
    try:
        space = usage(str(probe))
    except OSError as exc:
        return DriveScan(
            f"The drive this folder is on could not be read: {_why(exc)}. "
            f"Nothing was changed. Check the folder above is on a drive this "
            f"computer can reach."
        )

    # Three states and not two. `recorded_bytes` answers None when the folder
    # cannot be walked, and its own docstring says that must not be read as
    # zero - "nothing recorded" and "nobody could look" are different sentences
    # and only one of them is good news. The one that is ordinary is the folder
    # not being there at all, which is first run: the recorder makes it.
    ours = recorded(root)
    if ours is None and Path(root).exists():
        footage = "could not be counted - that folder could not be read"
        ours = 0
    elif not ours:
        footage = "nothing yet"
        ours = 0
    else:
        footage = bytes_in_words(ours)

    room = space.free + ours
    reserve = max(space.total * DRIVE_RESERVE_FRACTION, DRIVE_RESERVE_FLOOR_BYTES)
    budget_bytes = room - reserve
    drive_gb = space.total / 1024**3
    # A line per finding rather than one solid paragraph. This is a report, and
    # a report read once by somebody who is not sure what he is looking for is
    # read line by line or not at all. "0 KB" is not an amount of footage; the
    # first run has none, and saying so is shorter and truer.
    found = (
        f"{called} holds {bytes_in_words(space.total)} and "
        f"{bytes_in_words(space.free)} of it is free. VMD's own footage on it "
        f"comes to {footage}."
    )
    if budget_bytes < SMALLEST_WORTHWHILE_BUDGET_BYTES:
        return DriveScan(
            found + "\n" + WHY_THE_STICKER_DISAGREES
            + "\nThere is not enough room left on it to suggest anything. "
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
    # "Suggested budget" named a box that is no longer called that. Every
    # sentence on this tab names its controls the way the screen names them, or
    # it is a sentence about a form the operator cannot see.
    lines = [
        found,
        WHY_THE_STICKER_DISAGREES,
        f"Suggested size: {budget_gb:.0f} GB{holds}. That is everything free "
        f"apart from a slice of the drive left alone, so it can never be filled "
        f"right up.",
    ]
    if days is not None:
        lines.append(
            f"Suggested delete older than: {_days_in_words(days)}, the same as "
            f"that much space holds, so footage goes for one reason and not two."
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


def _picture_rate_in_words(kbps: int) -> str:
    """A picture rate as one figure with a unit, and never a trailing ".0".

    One unit throughout, because two - "1 Mb/s" here and "600 kb/s" there - is
    two scales to hold in your head to compare two sentences on one screen.
    """
    rate = f"{kbps / 1000.0:.1f}".rstrip("0").rstrip(".")
    return f"{rate} Mb/s"


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


# What a fold that is shut looks like, and what an open one looks like.
#
# A caret and not a colour, and not the button's pressed-in look either: the
# application stylesheet has no opinion about a checked QPushButton, so a fold
# he has opened and one he has not are drawn as the same rectangle. On a tab
# that now hides three things behind buttons, "is this open?" has to be
# answerable from two metres back and without seeing the panel below it - which
# on a form he has to scroll is often the case.
SHUT = "▸"   # a right-pointing triangle: there is more this way
OPEN = "▾"   # a down-pointing triangle: it is below you


def _fold_button(text: str, tip: str = "") -> QPushButton:
    """A button that opens something, and says which way it is pointing."""
    button = QPushButton(f"{SHUT}  {text}")
    button.setCheckable(True)
    if tip:
        button.setToolTip(tip)
    button.toggled.connect(
        lambda open_now: button.setText(f"{OPEN if open_now else SHUT}  {text}")
    )
    return button


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
        # What the last look at the drive found: its size in GB and what Windows
        # calls it. None until something has looked, and None again whenever the
        # drive cannot be read - which is a state the form has to be able to be
        # in, because the folder can name a drive letter with nothing behind it.
        self._drive_gb: float | None = None
        self._drive_name = ""
        # How much footage is on the drive now, in bytes, or None when the
        # folder cannot be read. Measured when the tab is loaded and when the
        # folder changes, and held rather than asked for again: it is a
        # directory walk, and the sentence it feeds is redrawn on every keystroke
        # in the age box.
        self._footage_bytes: int | None = None
        # One at a time, and never on the UI thread: finding the right path
        # probes two dozen addresses and takes up to a minute. A console that
        # stops repainting for a minute is a console the operator restarts.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._running: list[_ToolSignals] = []
        # Whether the camera has been asked, once, what lenses it has. See
        # `showEvent`: the answer is what makes **Zoom drives** appear on the
        # cards at all.
        self._asked_about_lenses = False

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
        # On the form rather than only in a tooltip, and it says what the list
        # IS now rather than how to change it - because it cannot be changed
        # from here any more. See `_build_streams_box` in this docstring's
        # place below: the list is locked.
        self.streams_help = _note(
            "One card for each of the camera's views. Every view on this list is "
            "used: it is shown in the Live tab, it is recorded, and it is "
            "watched if you ask for that below."
        )
        streams_outer.addWidget(self.streams_help)
        # What "Watch for movement" means, said once for both cards rather than
        # printed on each of them. With the views side by side, per-card help is
        # the same paragraph twice, six inches apart, on the tab he called busy.
        self.detect_help = _note(DETECT_HELP)
        streams_outer.addWidget(self.detect_help)
        # And what the one button under the tick is for, on the same terms:
        # once, above both cards, and only while there is a card it applies to.
        # `_show_stream_help` decides that - with nothing watched this paragraph
        # explains a button that is not on the screen, which on a console nobody
        # has set up yet is preamble before the first box.
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
        # The one fix for crossed zoom sliders that needs nothing explained.
        #
        # He pulled the last fix for this and reported back: still crossed. "The
        # thermal zoom slider moves the vis and the vis zoom slider moves the
        # thermal." There has been a per-view override on each card all along -
        # **Zoom drives** - and it was useless to him twice over: it is hidden
        # until the camera has said it has more than one lens, and the only
        # thing that made the camera say so was a button buried inside **Check
        # the camera**, which he has no reason to have pressed.
        #
        # What he knows is the whole of what is needed: he can SEE that each
        # slider moves the other picture. So the control is that sentence and a
        # button, and it asks him to understand nothing about media profiles,
        # tokens or ONVIF - one press exchanges the two views' lenses.
        #
        # Quiet, because on an installation where the sliders are right this is
        # a line about a fault that is not happening.
        self.swap_row = QWidget()
        swap_line = QHBoxLayout(self.swap_row)
        swap_line.setContentsMargins(0, 0, 0, 0)
        swap_line.setSpacing(SPACE_SNUG)
        self.swap_note = _note("The zoom sliders move the wrong pictures:")
        # The one note on this tab that does not wrap. Every other one is a
        # paragraph and wants the width of the column; this one is a caption on
        # a button, and a `WrappedNote` beside a button in a row is handed its
        # narrowest useful width - which broke six words over three lines with
        # 700 px of empty column beside them.
        self.swap_note.setWordWrap(False)
        self.swap_button = QPushButton("Swap them")
        self.swap_button.setToolTip(
            "For when the zoom slider under one picture moves the other "
            "picture.\n\n"
            "It asks the camera which lens is behind each view, exchanges the "
            "two, and puts the answer on the cards above. Press Save afterwards "
            "and try the sliders again.\n\n"
            "Nothing else about the camera is changed."
        )
        self.swap_button.clicked.connect(self.swap_the_zoom_sliders)
        swap_line.addWidget(self.swap_note)
        swap_line.addWidget(self.swap_button)
        swap_line.addStretch(1)
        streams_outer.addWidget(self.swap_row)

        # **Add a stream** was here, and **Remove** was on every card. Both are
        # gone, and the second is the one that matters.
        #
        # He asked for Add to go: this camera is one gimbal with two heads, the
        # views are a fixed property of the hardware, and a button offering a
        # third is a button offering a mistake. Remove had to go with it, and
        # not for symmetry - for asymmetry. With Add gone, one stray click on
        # Remove costs him a camera view permanently, and the only way back is
        # hand-editing JSON on a machine with no terminal and no second
        # computer. There is no undo on this form and there cannot be one.
        #
        # The list is locked, not fixed at two: `set_streams` is untouched, so a
        # settings file with three views still draws three cards and still saves
        # three. What has gone is this form's ability to invent or destroy one.
        layout.addWidget(streams_box)

        detection_box = QGroupBox("Movement detection")
        detection_outer = QVBoxLayout(detection_box)
        detection_outer.setSpacing(SPACE_SNUG)

        # Not "Watch for movement at all", which was the card switch's sentence
        # with two more words on the end of it - so the master switch and the
        # thing it is master of read as the same control seen twice.
        self._detection_enabled = QCheckBox("Watch for movement on any view")
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

        # **Allow VMD to try to say what it was** was here, and it is gone,
        # along with the chooser on each camera card that answered to it.
        #
        # His instruction: "I need movement notifications, but not accurate
        # identification." Not off by default - off. A control that is merely
        # unticked is a control somebody ticks one afternoon to see what it
        # does, and what it does at 700 m is put a confident wrong noun on an
        # event he was being told about anyway. `classify_enabled` in
        # `vmd/detect/config.py` now returns False whatever the file says, so
        # this is not a form that hides a running feature: nothing runs it.
        #
        # `detection.classify` and `StreamSettings.classify` stay in the model,
        # so every settings file in the field still loads, and are carried
        # across a save untouched like every other field this form has stopped
        # showing.

        # **Must travel at least (dots)** was here, and it is gone.
        #
        # Not the setting - `detection.min_travel_px` is still read by the
        # detector, still carried across a save, and a file that has a number in
        # it keeps that number. What is gone is the box, and it was never really
        # a question being put to the operator:
        #
        # * its own tooltip said "Leave it empty";
        # * its placeholder pointed at "the touchiness setting", which is called
        #   **How touchy:** and lives inside a camera card, folded away until
        #   that view is being watched - so the field explained itself by
        #   naming a control that is not on the screen;
        # * and it asked for a count of dots in the camera's own frame, which
        #   is not a quantity anybody can see, estimate or check. Every other
        #   number in dots on this tab is drawn on a picture instead.
        #
        # The review's fallback - move it beside **How touchy:** on the card -
        # is worse than either keeping it or deleting it, and that is the second
        # reason this went rather than moved: **How touchy:** is a setting of one
        # view and this is a setting of all of them, so a copy of it on each card
        # would be one number wearing two labels, where changing it under
        # "thermal" silently changed it under "visible" too.
        #
        # If it ever has to come back it comes back as a picture, like the sky
        # line did.
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
        # Not "Scan this PC", which reads as a virus scan or as a hunt for
        # cameras. It reads one drive and suggests two numbers, and the button
        # can afford to say so: it is the only one in this box.
        self.scan_button = QPushButton("Look at this drive and suggest a size")
        self.scan_button.setToolTip(
            "Looks at the drive the folder above is on - how big it is, how much "
            "is free, how much VMD is already using - and fills in a size and "
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
        # A scale that means something, from the first time the tab is opened.
        #
        # It used to run to an invented 2000 GB until somebody pressed the scan
        # button. On a drive that holds 884 GB that puts the far end of the
        # slider at a size the drive can never reach, so the handle says nothing
        # about how full anything is - and it let him set a size bigger than the
        # drive, which is the fault he reported: "the laptop have 950gb and the
        # VMD doesnt stop me in the budget". `_fit_the_slider_to_the_drive`
        # measures the drive at load and whenever the folder is changed.
        self.budget_slider.setRange(1, BUDGET_SLIDER_MAX_GB)
        self.budget_slider.setStyleSheet(SLIDER_STYLE)
        self.budget_slider.setToolTip(
            "How much of the drive VMD is allowed to fill with footage. When it "
            "is full the oldest footage is deleted to keep recording going.\n\n"
            "It stops at the size of the drive, because a size bigger than the "
            "drive is one nothing can ever reach."
        )
        budget_line = QHBoxLayout()
        budget_line.setSpacing(SPACE_SNUG)
        budget_line.addWidget(self.budget_slider, 1)
        budget_line.addWidget(self._budget)
        # Not "Budget (GB)". Budget is a money word, and what is being asked for
        # here is not money: it is how much of the drive VMD may fill.
        storage_form.addRow("How much space VMD may use (GB)", budget_line)

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

        # What the age rule means given what is actually on the drive.
        #
        # He is right that the box was not tied to anything: it let him set 90
        # days while holding six, and a rule that will not fire for another
        # eighty-four days reads exactly like a rule that is working. It is
        # staying - asked whether there is a legal requirement, he said yes - so
        # the fix is to make it honest rather than to take it away.
        self.retention_note = _note("")
        storage_form.addRow("", self.retention_note)
        self._days.textChanged.connect(lambda _typed: self._say_what_the_age_rule_does())
        # The folder decides both the drive the slider is scaled to and how much
        # footage there is to talk about, and it is typed rather than chosen.
        # On `editingFinished` and not on every keystroke: this walks a directory
        # tree, and `C`, `C:`, `C:\\`, `C:\\f`... is a walk per letter.
        self._root.editingFinished.connect(self._the_folder_changed)

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

        # It used to say "It never goes below the lowest picture you allow",
        # which points at a setting that is not on this screen and is not on any
        # screen: `bitrate.floor_kbps`, a number in the file. A sentence about a
        # limit "you allow" that he has never been shown and cannot change reads
        # as a control he has missed. So it says the figure instead, out of the
        # settings that were loaded, and `load` sets it again every time.
        self.link_help = _note("")
        self.say_the_lowest_picture(self._loaded)
        radio_form.addRow("", self.link_help)
        layout.addWidget(radio_box)

        # "The camera - is it relevant anymore?" - and then, plainly: get rid of
        # it. Refused, and the reasons were put to him and accepted.
        #
        # It is the only diagnostic on a machine with no terminal and no second
        # computer: **Test the camera** and **Find the camera's address** are the
        # difference between "the picture is black" and knowing why, and *Which
        # lens is behind which picture?* is the only cure for the fault he
        # reported himself - "only the vis is zooming".
        #
        # What is true in his complaint is that it was the biggest thing on the
        # page, always open, five buttons and a black rectangle, sitting under a
        # form he came here to type four numbers into. So it is shut by default
        # and it is the last thing on the page: he will not see it again unless
        # he goes looking for it, and it is there on the day he needs it.
        self.tools_button = _fold_button(
            "Check the camera",
            "The tools for finding out whether the camera is answering, and "
            "what it says when it does.\n\n"
            "Nothing in here changes a setting. You do not need any of it "
            "unless something is wrong.",
        )
        layout.addWidget(self.tools_button)

        # A frame rather than a group box, and with no title: the button above
        # it is the title, and a panel headed "Check the camera" under a button
        # saying "Check the camera" is one name printed twice.
        self.tools_box = tools_box = QFrame()
        tools_box.setFrameShape(QFrame.Shape.StyledPanel)
        tools_outer = QVBoxLayout(tools_box)
        tools_outer.setSpacing(SPACE_SNUG)
        # Two across, not five along. Five of these on one line is about 1500 px
        # of buttons in a column that stops at 980, and Qt clips a button at
        # BOTH ends rather than eliding one - which is how the longest of them
        # came out reading "urn the picture down to what the link can carr".
        tools_buttons = QGridLayout()
        tools_buttons.setHorizontalSpacing(SPACE_SNUG)
        tools_buttons.setVerticalSpacing(SPACE_SNUG)
        self.test_button = QPushButton("Test the camera")
        self.test_button.clicked.connect(self.test_camera)
        # "path" here was the RTSP path - the `/ch2` on the end of the address.
        # To the man reading this a path is a track, and the button sits two
        # inches under a box labelled Address that is the thing it fills in.
        self.find_button = QPushButton("Find the camera's address")
        self.find_button.clicked.connect(self.find_paths)
        # "Fit the camera to the link" reads as an instruction about mounting
        # one. It asks the camera for a smaller picture, which is what the tick
        # box two boxes above already says in words.
        self.fit_button = QPushButton("Turn the picture down to what the link can carry")
        self.fit_button.clicked.connect(self.fit_to_link)
        # Which lens is behind which picture. It fills the chooser on each
        # camera card as well as printing what it found, which is the only way
        # the operator can fix a zoom bar that moves the wrong picture.
        self.lens_button = QPushButton("Which lens is behind which picture?")
        self.lens_button.clicked.connect(self.ask_about_lenses)
        self.report_button = QPushButton("Save a report")
        self.report_button.clicked.connect(lambda: self.save_report())
        for index, button in enumerate(
            (
                self.test_button,
                self.find_button,
                self.lens_button,
                self.fit_button,
                self.report_button,
            )
        ):
            tools_buttons.addWidget(button, index // 2, index % 2)
        for column in range(2):
            tools_buttons.setColumnStretch(column, 1)
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
        tools_box.setVisible(False)
        self.tools_button.toggled.connect(tools_box.setVisible)

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

    def say_the_lowest_picture(self, settings: Settings) -> None:
        """Put the floor the camera is never asked to go below into words.

        The number itself, because the thing it used to name - "the lowest
        picture you allow" - is not a control anywhere in this console. A
        sentence that refers to a setting he cannot find is worse than one that
        refers to nothing: he goes looking.
        """
        self.link_help.setText(
            f"It never asks the camera for less than "
            f"{_picture_rate_in_words(settings.bitrate.floor_kbps)}. If the link "
            f"cannot carry even that, it says so in the Logs tab rather than "
            f"spoiling the picture further."
        )

    def credential_fields(self) -> list[QLineEdit]:
        """Every field holding a password. They are all plain text on purpose."""
        return [self._password, self._radio_password]

    # ---------------------------------------------------------------- storage

    def scan_this_pc(self) -> None:
        """Look at the drive, say what is on it, and fill in the two numbers.

        The two settings this fills in are the ones he had no way of arriving
        at: a size in gigabytes and an age in days, on a form that never told
        him how big the drive was. Both stay editable, and neither is written
        anywhere until Save - this button changes two boxes and a sentence.
        """
        found = scan_drive(
            self._recordings_folder(), self._footage_rate(), usage=self.disk_usage
        )
        self.storage_scan_note.setText(found.words)
        self._fit_the_slider_to_the_drive()
        if found.budget_gb is None:
            return  # nothing could be worked out, and the sentence says why
        self.budget_gb = f"{found.budget_gb:.0f}"
        if found.days is not None:
            self.retention_days = str(found.days)
        self._say_what_the_age_rule_does()

    def _recordings_folder(self) -> Path:
        """The folder on screen, anchored the way `load_settings` anchors it."""
        root = Path(self.storage_root.strip() or "recordings")
        if not root.is_absolute():
            root = self.settings_path.parent / root
        return root

    def _the_folder_changed(self) -> None:
        """A new folder is a new drive and a different pile of footage."""
        self._footage_bytes = None
        self._fit_the_slider_to_the_drive()
        self._say_what_the_age_rule_does()

    def _fit_the_slider_to_the_drive(self) -> None:
        """Put the far end of the slider where the drive actually ends.

        A slider that runs past the end of the drive is not merely a wrong
        scale. It is the fault he reported - "the laptop have 950gb and the VMD
        doesnt stop me in the budget" - because the far end of it is a size that
        can never be reached, and a size that can never be reached means the
        oldest footage is never deleted to make room. Retention does its job
        perfectly and the drive fills up anyway.

        The typed box is left completely alone. It is the setting, the slider is
        only a way of moving it, and a form that rewrites a number the operator
        typed is a form he stops believing. A number past the drive is caught at
        Save instead, in a sentence - see `_bigger_than_the_drive`.
        """
        folder = self._recordings_folder()
        self._drive_name = drive_name(folder)
        try:
            self._drive_gb = self.disk_usage(str(drive_under(folder))).total / 1024**3
        except OSError:
            # A drive letter with nothing behind it. Nothing is known about how
            # big it is, so the slider goes back to a length rather than a
            # measurement, and Save refuses nothing on the strength of a guess.
            self._drive_gb = None
        top = int(self._drive_gb) if self._drive_gb else BUDGET_SLIDER_MAX_GB
        # `setMaximum` drags the handle back when the current value is past the
        # new end, and the handle moving rewrites the box. Held shut for exactly
        # that: the typed size is the setting and this is a change of scale.
        self._syncing_budget = True
        try:
            self.budget_slider.setMaximum(max(top, 1))
        finally:
            self._syncing_budget = False
        self._budget_typed(self._budget.text())

    def _how_much_footage(self) -> int | None:
        """Bytes of footage on the drive now, measured once and remembered.

        None when the folder cannot be walked, which includes the ordinary
        first-run state of it not being there yet. None is not zero: "nothing
        recorded" and "nobody could look" are different sentences, and only one
        of them is something to tell him.
        """
        if self._footage_bytes is None:
            self._footage_bytes = recorded_bytes(self._recordings_folder())
        return self._footage_bytes

    def _say_what_the_age_rule_does(self) -> None:
        """Say what "delete older than" means given the footage there really is.

        His complaint about this box was that it is not tied to reality: he can
        set 90 days while holding six days of footage, and nothing anywhere says
        that the rule will not fire for another eighty-four. A number that looks
        like it is doing something and is not is worse than no number, because
        it is the number he will point at when asked how long footage is kept.

        The box stays - he was asked whether there is a legal requirement and
        said yes - so what changes is that the line under it says which of the
        two rules is actually deleting his footage.
        """
        typed = self.retention_days.strip()
        held, days = self._how_far_back_it_goes()
        if not typed:
            self.retention_note.setText(
                held + "Nothing is deleted because of its age. Footage goes only "
                "when the size above is full, oldest first."
            )
            return
        try:
            rule = int(float(typed))
        except ValueError:
            # Half-typed, or not a number at all. The save refuses it by the
            # name on the form; a sentence under it guessing at what he meant
            # would be a second opinion nobody asked for.
            self.retention_note.setText("")
            return
        if days is not None and rule > days:
            self.retention_note.setText(
                f"{held}Nothing is deleted by age until it is "
                f"{_days_in_words(rule)} old, so today this rule deletes nothing "
                f"- the size above is what decides."
            )
            return
        self.retention_note.setText(
            f"{held}Anything older than {_days_in_words(rule)} is deleted, "
            f"whether there is room for it or not."
        )

    def _how_far_back_it_goes(self) -> tuple[str, float | None]:
        """How much footage there is, in words and as a number of days.

        Two returns because the sentence and the comparison want different
        things: he is told "about 6 days", and the rule is compared against the
        unrounded figure so that 6 days of footage and a 6-day rule do not read
        as a rule that never fires.

        `None` when the folder could not be walked - which includes the folder
        not being there yet, on the first run - and then nothing is said about
        it at all. An invented amount of footage under a rule about deleting
        footage is the worst sentence this box could carry.
        """
        have = self._how_much_footage()
        rate = self._footage_rate()
        if have is None or rate <= 0:
            return "", None
        days = have / rate / SECONDS_IN_A_DAY
        if days < 1:
            # `_days_of_footage` never answers zero, and it is right not to for
            # a size: a size that holds part of a day holds something. This is
            # not a size, it is a measurement, and "about 1 day" for four
            # minutes of footage is the box lying in the direction that makes
            # the age rule look reasonable.
            return "You have less than a day of footage. ", days
        return f"You have about {_days_in_words(int(days))} of footage. ", days

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
        that deletes footage when it goes down. A number past the end of the
        drive is caught at Save, in a sentence, rather than corrected here.
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
        # How long the footage he has goes back does not change, but which of
        # the two rules is deleting it does.
        self._say_what_the_age_rule_does()

    def _say_how_many_days(self) -> None:
        """How far back this size lets him look, beside the size.

        Or, when the size is past the end of the drive, that it is - said here
        rather than only at Save, because a line under the box promising "about
        19 days of footage" for a size the drive can never hold is the form
        agreeing with the mistake. Save refuses it in full; this is the shorter
        version, at the moment he types it.
        """
        try:
            budget_gb = float(self._budget.text())
        except ValueError:
            budget_gb = 0.0
        if self._drive_gb is not None and budget_gb > self._drive_gb:
            called = f"Drive {self._drive_name}" if self._drive_name else "this drive"
            self.budget_days_note.setText(
                f"That is more than {called} holds ({self._drive_gb:.0f} GB), so "
                f"nothing would ever be deleted to make room and the drive would "
                f"fill up. Save will not accept it."
            )
            return
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
        """Draw a card for one view. Called by `set_streams` and by nothing else.

        There is no button that reaches this any more - see the note where
        **Add a stream** used to be - but the method stays public and unchanged,
        because it is how the settings file's own list becomes cards and how
        every test builds one.
        """
        row = StreamRowWidget(name, url, enabled, reader, stream=stream)
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
        """Take a card off the form. Reached only from `set_streams`.

        Which is the whole of why it is still here: replacing the list means
        clearing it first. Nothing the operator can press arrives here.
        """
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
        # And then upwards, by hand, because Qt will not do it for us.
        #
        # `_lay_the_cards_out` fixes the grid. It does not fix what the layouts
        # ABOVE the grid remember, and they are the ones the scroll area asks
        # how tall this page is. Every one of them caches the height it worked
        # out for a width, a fold opening inside a card reaches them only along
        # a chain of parent WIDGETS, and the grid is a layout inside a layout -
        # so it is not on that chain and neither is anything above it.
        #
        # The visible failure is not subtle once it is measured: the page keeps
        # the height it had, the Streams box is handed less than its own
        # minimum, and every control on the card is drawn a few pixels short of
        # its own label - **Parts to ignore** in 24 px of the 26 it needs, "How
        # touchy:" in 8 of 16.
        #
        # `invalidate` on its own is not enough. It schedules the recalculation
        # and a layout goes on answering out of its cache until something
        # activates it, which is the same lesson the grid taught above. So each
        # layout from the card up to the page is invalidated and then activated.
        for row in self._rows:
            widget = row
            while widget is not None:
                layout = widget.layout()
                if layout is not None:
                    layout.invalidate()
                    layout.activate()
                if widget is self._page:
                    break
                widget = widget.parentWidget()

    def _show_stream_help(self) -> None:
        """Only explain the controls that are actually on the screen.

        The paragraph about parts to ignore is about a button that is folded
        away until a view is being watched, which on a console nobody has set
        up yet is always. Explaining a control he cannot see is the same cost as
        explaining one twice - it is text between him and the box he came here
        to type in.
        """
        watched = any(row.detect_field.isChecked() for row in self._rows)
        self.ignore_help.setVisible(watched)
        # "Swap them" means nothing with one view and nothing with three: there
        # is no pair to exchange. The camera this console is built around has
        # exactly two heads, and anything else is answered by **Zoom drives** on
        # the cards, which says which lens each picture is on by name.
        self.swap_row.setVisible(len(self._rows) == 2)

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
        # How far a thing must travel before it counts is not on this form any
        # more; `self._loaded` above is what carries it across a save.
        self.say_the_lowest_picture(settings)
        # Both of these touch the filesystem, and both are the reason the two
        # storage numbers can be said anything about at all: how big the drive
        # is, and how much footage is on it.
        self._footage_bytes = None
        self._fit_the_slider_to_the_drive()
        self._say_what_the_age_rule_does()
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

        too_big = self._bigger_than_the_drive(settings)
        if too_big:
            self._set_message(too_big)
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

    def _bigger_than_the_drive(self, settings: Settings) -> str:
        """Refuse a size the drive can never reach, and say why in one sentence.

        "The laptop have 950gb and the VMD doesnt stop me in the budget." The
        deleting itself was never wrong - retention removes the oldest segments
        to stay inside the size - but a size larger than the drive is a
        threshold nothing can cross, so nothing is ever deleted and the drive
        fills up instead. Recording then stops, on the console whose entire job
        is not stopping.

        Refused rather than quietly clamped. Rewriting a number he typed is how
        a form loses a field, and this is the field that decides how much
        footage he keeps: he has to see that the number he chose is not the
        number that will be in force. It says the drive by name and gives the
        figure, so the sentence can be checked against This PC.

        Silent when nothing is known about the drive. A drive letter with
        nothing behind it is already refused a line above this, by
        `storage_problem`, in words about the real fault.
        """
        if not settings.storage.budget_enabled:
            return ""
        if self._drive_gb is None:
            return ""
        if settings.storage.budget_gb <= self._drive_gb:
            return ""
        called = f"Drive {self._drive_name}" if self._drive_name else "This drive"
        return (
            f"{called} holds {self._drive_gb:.0f} GB, so {settings.storage.budget_gb:g} "
            f"GB is a size it can never reach. Old footage is only deleted once "
            f"that size is used up, so a size bigger than the drive means nothing "
            f"is ever deleted and the drive fills up instead. Type "
            f"{self._drive_gb:.0f} or less."
        )

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
        # Two of the four. `min_travel_px` and `classify` are not on this form
        # and are not written here either, which is exactly how they survive:
        # `payload` starts from the settings that were loaded, so whatever the
        # file said about them is still in this dictionary and goes back out
        # unchanged. Nothing reads `classify` any more in any case; see
        # `vmd/detect/config.py`.
        payload["detection"].update(
            enabled=self.detection_enabled,
            alarm_sound=self.alarm_sound,
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
                # A card on the list is a view in use, and there is no longer a
                # Remove button to point him at - so there is exactly one way
                # out of this, and it is the one named.
                return (
                    f'"{name or "A view"}" has no address. Type the address of '
                    f"that view into the box under its name."
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
            "Trying the addresses cameras usually answer on. This takes up to "
            "a minute.",
            lambda tools, s: tools.find_paths(s),
        )

    def swap_the_zoom_sliders(self) -> None:
        """Exchange the two views' lenses, because he can see they are crossed.

        "The thermal zoom slider moves the vis and the vis zoom slider moves the
        thermal." That sentence is the entire input this control needs, and it
        is the only input he has: which media profile belongs to which picture is
        worked out from a vendor's naming, the guess is silent when it is wrong,
        and nothing in this program can see which picture answered.

        So it asks the camera for the mapping it is using now - including the
        automatic answer, which is the one that is wrong - swaps the two, and
        writes both into `ptz_profile` so the choice survives a save and a
        restart. Off the window thread, because it crosses the radio link.
        """
        if len(self._rows) != 2:
            return
        settings = self.settings_from_form()
        if settings is None:
            return

        tools = self._camera_tools(settings)
        signals = _ToolSignals()
        signals.done.connect(lambda lines: self._swap_arrived(signals, lines))
        self._running.append(signals)
        self.swap_button.setEnabled(False)
        self._set_message(
            "Asking the camera which lens is behind each picture.", quiet=True
        )
        self._pool.start(_ToolJob(lambda: [tools.lenses(settings)], signals))

    def _swap_arrived(self, signals: _ToolSignals, lines: list) -> None:
        """Put the exchange on the cards, or say why there was nothing to do.

        Every way this can fail ends in a sentence on the message line. A button
        that reports a crossed pair of sliders and then does nothing visible is
        the fault he already reported, wearing a fix's clothes.
        """
        self.swap_button.setEnabled(True)
        if signals in self._running:
            self._running.remove(signals)

        answer = next((line for line in lines if isinstance(line, dict)), None)
        if answer is None:
            self._set_message(
                "The camera could not be asked which lens is behind each "
                "picture. Nothing was changed."
            )
            return
        if not answer.get("ok"):
            reason = str(answer.get("error") or "the camera did not say").strip()
            self._set_message(
                f"The camera could not be asked which lens is behind each "
                f"picture: {reason}. Nothing was changed."
            )
            return

        profiles = list(answer.get("profiles") or [])
        using = dict(answer.get("using") or {})
        first, second = self._rows
        one, two = first.values()[0], second.values()[0]
        here, there = using.get(one, ""), using.get(two, "")

        if not here or not there:
            missing = one if not here else two
            self._set_message(
                f"The camera did not say which lens is behind \"{missing}\", so "
                f"there is nothing to swap. Press \"Check the camera\" at the "
                f"bottom of this page and then \"Which lens is behind which "
                f"picture?\" to see what it did say."
            )
            return
        if here == there:
            # Not a fault, and saying so is the whole of the answer: a swap
            # cannot fix a camera that is sending one lens down both pictures.
            self._set_message(
                "Both pictures are on the same lens on this camera, so swapping "
                "them would change nothing - either slider moves both. That is "
                "what the camera reported, not a fault in VMD."
            )
            return

        first.set_lenses(profiles, there)
        second.set_lenses(profiles, here)
        self._lay_the_cards_out()
        self._set_message(
            f"Swapped: the zoom slider under \"{one}\" now moves the lens "
            f"\"{two}\" was on, and the other way round. Press Save, then try "
            f"the sliders again. Press this again to put them back.",
            quiet=True,
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Ask the camera about its lenses the first time this tab is opened.

        **Zoom drives** on each card is hidden until the camera has said it has
        more than one lens, and until now the only thing that made the camera
        say so was a button inside **Check the camera** - which is now shut by
        default and which he has no reason to press. A control that only appears
        after a tool he has never heard of is a control that does not exist.

        On being shown rather than on `load`, and once: the tab is built when the
        console starts and the console starts on the Live tab, so asking at load
        would cross the radio link for a page nobody is looking at. Quietly, off
        the window thread, and any failure is dropped - this is a chooser being
        offered, not an answer anybody asked for.
        """
        super().showEvent(event)
        if self._asked_about_lenses:
            return
        self._asked_about_lenses = True
        self.ask_about_lenses(quietly=True)

    def ask_about_lenses(self, quietly: bool = False) -> None:
        """Ask the camera what lenses it has, and offer them on every card.

        The reason this exists is a fault the operator hit: "only the vis is
        zooming". Which media profile belongs to which picture is worked out
        from the profile names and the video sources, which is a guess about a
        vendor's naming - and when it guesses wrong nothing reports it, because
        the camera accepts the command and carries it out on the other lens.

        So the guess is shown, and can be overruled. He can see which picture
        answers; nothing in this program can.

        `quietly` is the tab asking on its own behalf when it is first opened.
        Then nothing is printed and nothing is complained about: the operator did
        not ask this question and must not be shown its answer, let alone its
        failure. The button asking is loud, because somebody pressed it.
        """
        if quietly:
            # The settings that were loaded, not the form. Nothing has been
            # typed yet when this fires, and `settings_from_form` writes to the
            # message line when it refuses - which would replace whatever `load`
            # had to say with a complaint about a question the operator did not
            # ask.
            settings = self._loaded
            if not settings.camera.host.strip():
                return  # no address is not news
        else:
            settings = self.settings_from_form()
            if settings is None:
                self._output.setPlainText(f"Fix this first: {self.message}")
                return

        tools = self._camera_tools(settings)
        signals = _ToolSignals()
        signals.done.connect(
            lambda lines: self._lenses_found(
                self.lens_button, signals, lines, quietly=quietly
            )
        )
        self._running.append(signals)
        if quietly:
            self._pool.start(_ToolJob(lambda: [tools.lenses(settings)], signals))
            return
        signals.progress.connect(self._append_line)
        self.lens_button.setEnabled(False)
        self._output.setPlainText("Asking the camera about its lenses")
        self._pool.start(_ToolJob(lambda: [tools.lenses(settings)], signals))

    def _lenses_found(
        self,
        button: QPushButton,
        signals: _ToolSignals,
        lines: list,
        quietly: bool = False,
    ) -> None:
        """Print what the camera said, and put it into the choosers."""
        if not quietly:
            button.setEnabled(True)
        if signals in self._running:
            self._running.remove(signals)

        answer = next((line for line in lines if isinstance(line, dict)), None)
        if answer is None:
            if not quietly:
                for line in lines:
                    self._output.appendPlainText(str(line))
            return
        if not quietly:
            for line in lens_lines(answer):
                self._output.appendPlainText(line)
        elif not answer.get("ok"):
            # Nobody asked, so nobody is told. The chooser stays hidden, which
            # is the state it was already in.
            logger.info("the camera did not say what lenses it has: %s", answer.get("error"))
            return

        profiles = list(answer.get("profiles") or [])
        for row in self._rows:
            row.set_lenses(profiles)
        self._lay_the_cards_out()

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
        """Show one frame from this view, to draw the parts to ignore on.

        Everything under this button used to be numbers: a sky line in dots
        from the top of the frame, and a list of rectangles printed as `120 x 80
        dots, at 30 across and 40 down`. Not one of those is a quantity anybody
        can see, estimate or check, and a sky line set too low deletes real
        movement without ever saying it did. He asked for all of it to go, and
        he was right - the picture is the control.

        The frame is fetched before the dialog is built, because the dialog
        takes a picture rather than a way of getting one. When it cannot be had
        the dialog is opened anyway, carrying the reason: the areas already
        drawn are still his to delete, and a camera that is unreachable this
        afternoon must not also take away the ability to undo a mistake.
        """
        # Imported here for the same reason the camera tools are: opening the
        # console must not pay for the camera stack, and this module has to stay
        # importable on a machine with nothing installed.
        from vmd.desktop.mask import MaskDialog

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
        frame, problem = b"", ""
        try:
            frame = tools.grab_frame(settings, name)
        except Exception as exc:  # noqa: BLE001 - any failure is one sentence
            logger.info("no picture from %s: %s", name, exc)
            problem = str(exc)

        dialog = MaskDialog(frame, row.shapes(), problem=problem, parent=self)
        if dialog.exec():
            row.set_shapes(dialog.shapes())
            self._refold()
        return dialog

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

    def lenses(self, settings: Settings) -> dict:
        """Which lenses the camera has, and which picture each one drives now.

        A dict rather than the lines every other tool returns, because the form
        does something with this as well as printing it: it fills in the chooser
        on each camera card. `_lens_lines` turns it into the words.
        """
        return self._ptz.zoom_profiles()

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
    sentence, which names the view and says what happens at the next Save.

    It used to end by pointing at **Remove**, and that button is gone - the list
    is locked, because with **Add a stream** gone a stray click on Remove costs
    a camera view permanently. A sentence naming a button that is not on the
    screen sends him looking for it, which is worse than saying nothing. So it
    says what is true instead: the view is in use, and that is now the only
    state a view on this list can be in.
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
    return (
        f"{named} {was} switched off in the settings file. There is no longer a "
        f"switch for that - every view on this list is a view in use - so {it} "
        f"will be used again from the next Save."
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
    # `detection.min_travel_px` was here. The rule is that a field on the form
    # has its label in this table; the other half of the rule is that a field
    # taken OFF the form loses it, because the sentence would then point at a
    # box that is not there to be corrected. Nothing on the screen can put a bad
    # value into it now - it comes off the file and goes back unchanged - and a
    # file that has a bad one in it is refused by `load_settings`, in its own
    # words, before this form is filled in at all.
    "storage.root": "Folder, under Storage",
    "storage.budget_gb": "How much space VMD may use (GB)",
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
