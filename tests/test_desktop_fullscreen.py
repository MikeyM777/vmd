"""Every tab, at the sizes this console is really run at, with nothing drawn
through anything else.

The report from the field was "the program isn't fitted right, all the text got
mushed up when fullscreen", and fullscreen is not an unusual way to run this: it
is a dedicated always-on console on a laptop nobody types on.

Fullscreen is the case because fullscreen is the one that IGNORES what the
layout asked for. `resize()` is clamped by a window's minimum size, so a tab
whose contents need more room than the screen has simply refuses to shrink and
the window grows past the edge of the display. Fullscreen has no such courtesy:
the window is given the screen's geometry, and everything inside is then handed
less height than its own minimum. Qt does not scroll, wrap or elide in that
situation - it hands each widget a share of what there is, which for the Settings
tab at 1366x768 was a stream address field five pixels tall and the "Not
saved..." sentence drawn straight through the report box above the Save button.

So the measurement here is deliberately not "did the widget get its sizeHint".
It is the operator's own test: is any sentence drawn on top of another, and is
any sentence given less room than the text in it needs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from vmd.desktop.disk import DiskReading, DiskWatcher
from vmd.desktop.live import (
    GIVEN_UP_WORDS,
    GIVING_UP_AFTER,
    RESTART_BACKOFF_MAX,
    UNANSWERED_NOTE,
)
from vmd.desktop.video import FakeVideoPane
from vmd.desktop.window import ConsoleWindow
from vmd.settings import Settings, StreamSettings, save_settings
from vmd.streaming.go2rtc import NOT_INSTALLED

# The screens this console is actually run on, as the number of logical pixels
# a fullscreen window gets on each.
#
# 1280x720 is not a screen anyone sells: it is what a 1920x1080 laptop panel
# reports to Qt at Windows' 150% display scaling, which is the factory setting on
# every 14-inch 1080p laptop. 1536x864 is the same panel at 125%. Qt 6 turns the
# scale factor into a device pixel ratio and hands the application a SMALLER
# logical screen - so display scaling does not make the text bigger relative to
# the window, it makes the window smaller relative to the text, and it is the
# tightest case this console has to survive.
SCREENS = [
    (1280, 720),   # 1920x1080 at 150% scaling
    (1366, 768),   # a small laptop panel
    (1536, 864),   # 1920x1080 at 125% scaling
    (1920, 1080),
    (2560, 1440),
    (3840, 2160),
]

# The widget classes that put words in front of the operator. Anything else -
# frames, splitters, the steering overlay that deliberately covers the video
# wall - is not a sentence and may overlap whatever it likes.
SPEAKS = (
    QLabel,
    QAbstractButton,
    QLineEdit,
    QComboBox,
    QAbstractSpinBox,
    QAbstractItemView,
    QPlainTextEdit,
)


class FakeServices:
    """Everything failing at once, because that is when the text is longest."""

    def __init__(self, disk: DiskWatcher | None = None) -> None:
        self.disk = disk

    def apply(self, settings) -> None: ...

    def start(self) -> None: ...

    def tick(self) -> list[str]:
        return []

    def stop(self) -> None: ...

    def local_url(self, name: str) -> str:
        return f"rtsp://127.0.0.1:8554/{name}"

    def state(self) -> dict:
        return {
            "recording": False,
            "streaming": NOT_INSTALLED,
            "restarts": {},
            "detection": {
                "enabled": True,
                "running": False,
                "restarts": 9,
                "reason": "detection keeps stopping and being restarted",
            },
            # The longest thing the band can ever carry, and the newest: a fifth
            # chip appears only when the radio link is carrying a stream more
            # than once, and it arrives as a whole sentence naming every stream
            # it is true of. A band measured without it is a band measured in
            # the state it is in on a healthy machine, which is not the state
            # this file exists to measure.
            "on_camera": ["thermal", "visible"],
        }


class FakePtz:
    def apply(self, settings) -> None: ...

    def status(self) -> dict:
        return {"available": False, "reason": "no camera address set"}

    def move(self, pan, tilt, zoom) -> dict:
        return {"ok": True}

    def stop(self) -> dict:
        return {"ok": True}

    def home(self) -> dict:
        return {"ok": True}


class FakeRadio:
    """A link at the edge, which is what makes the link panel say the most."""

    MARGINAL = {
        "connected": True,
        "age_seconds": 1.0,
        "signal_dbm": -84.0,
        "noise_dbm": -95.0,
        "rx_mbps": 17.6,
        "rx_capacity_mbps": 18.0,
        "tx_mbps": 0.9,
        "tx_capacity_mbps": 18.0,
        "ccq": 612.0,
        "distance_m": 15400.0,
        "device": "Ubiquiti PowerBeam 5AC",
        "uptime_s": 3600.0,
    }

    def apply(self, settings) -> None: ...

    def status(self) -> dict:
        return dict(self.MARGINAL)


def alarming_disk(root: Path) -> DiskWatcher:
    """A drive that will run out before the budget does: the longest storage
    reading there is, and the one that means recording is about to stop."""
    settings = Settings()
    settings.storage.root = root
    reading = DiskReading(
        at=0.0,
        free_bytes=5 * 1024**3,
        used_bytes=20 * 1024**3,
        bytes_per_second=900_000.0,
        rate_is_estimate=False,
        newest_write=0.0,
        writing=True,
        write_problem=None,
        problem=None,
    )
    watcher = DiskWatcher(settings, executor=lambda work: work(), read=lambda s, n: reading)
    watcher.poll()
    return watcher


def console(qtbot, tmp_path: Path) -> ConsoleWindow:
    """The real window, wired to services that are all in their worst state."""
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.storage.root = tmp_path / "recordings"
    settings.camera.streams = [
        StreamSettings(name="thermal", url="rtsp://camera/thermal", enabled=True),
        StreamSettings(name="visible", url="rtsp://camera/visible", enabled=True),
    ]
    save_settings(settings, path)
    window = ConsoleWindow(
        settings_path=path,
        services=FakeServices(disk=alarming_disk(tmp_path / "recordings")),
        ptz=FakePtz(),
        radio=FakeRadio(),
        index_path=tmp_path / "segments.db",
        make_pane=lambda name: FakeVideoPane(),
        events_path=None,
    )
    qtbot.addWidget(window)
    return window


def in_the_worst_state(window: ConsoleWindow) -> None:
    """Put every tab into the state that carries the longest sentences."""
    live = window.live
    live._alarm_label.setText("Movement on thermal at 03:41:08")
    live._alarm.setVisible(True)

    # The stream line, which is the longest sentence on the Live tab and the
    # reason this file measures it at all.
    #
    # It was not being measured. `pretend_failed()` was called once, before the
    # loop; the first refresh restarted the pane, which puts it back to
    # "connecting", and the seven refreshes after it read "connecting" and
    # overwrote the label with it. The result was 'thermal  -  connecting' - 22
    # characters - against the 66 of GIVEN_UP_WORDS, so nineteen layout
    # assertions in this file were sizing a string a third of the length of the
    # one that matters.
    #
    # Two things were missing. The pane has to be put back into "failed" before
    # every refresh, because the console restarting it is what takes it out of
    # that state; and the clock has to move, because `_restart_when_due` refuses
    # to try again until the backoff has elapsed and every refresh inside it is
    # a refresh that counts nothing. Hand-wound rather than slept through: this
    # would otherwise be a five-minute test, and a real clock in a layout test
    # is a flake waiting for a slow machine.
    wound = [0.0]
    live._clock = lambda: wound[0]
    for _ in range(GIVING_UP_AFTER + 1):
        for pane in live._panes.values():
            pane.pretend_failed()
        live.refresh()
        wound[0] += RESTART_BACKOFF_MAX + 1.0
    for name in live.stream_names():
        assert GIVEN_UP_WORDS in live.stream_label_text(name), (
            f"the longest stream line never appeared: {live.stream_label_text(name)!r}"
        )

    # After the refreshes, which ask the camera what it last said and would put
    # this back to nothing.
    live._ptz_note.setText(UNANSWERED_NOTE)
    window.playback.click_at(0.5)
    window.settings_tab._set_message(
        "Not saved: the address rtsp:/10.0.0.2/thermal is not a stream address - "
        "it must start with rtsp:// or http://, and the part after the address is "
        "the path the camera answers on."
    )
    for index in range(60):
        window.logs._buffer.records.append(
            {
                "seq": index,
                "time": 0.0,
                "level": "ERROR",
                "source": "go2rtc",
                "text": "[streams] error: rtsp://camera/thermal: 401 Unauthorized - "
                "the camera refused the username and password in Settings",
            }
        )
    window.logs.refresh()


# ------------------------------------------------------- the fullscreen fitting


def as_fullscreen(qtbot, window: ConsoleWindow, width: int, height: int) -> list[QWidget]:
    """Every tab, given exactly the room a fullscreen console leaves it.

    A window shown on the machine running the tests is clamped to the machine's
    own screen, and 3840x2160 is not a screen most of them have - so the tab is
    given the geometry rather than the window. A child widget's geometry is not
    clamped by anything, and the layout inside it is computed the same way it
    would be inside a window of that size.

    The room a tab actually gets is the screen less the tab bar and the status
    line, and that chrome is measured off a real window rather than guessed at,
    because it is a font's height and fonts differ between machines.
    """
    window.show()
    QApplication.processEvents()
    chrome = max(window.height() - window.live.height(), 0)
    window.hide()

    # The host is a window of its own so that everything inside it is really
    # laid out - a hidden parent never polishes its children, and a form that has
    # never been laid out reports whatever geometry it was born with. It stays
    # small; the page inside it does not, and a child's geometry is nobody's
    # business but its own.
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(120, 80)
    host.show()
    # Read every page out first: taking one out of the tab widget renumbers the
    # rest, and half the console would go unmeasured.
    pages = [window.tabs.widget(index) for index in range(window.tabs.count())]
    for page in pages:
        page.setParent(host)
        page.setGeometry(0, 0, width, max(height - chrome, 1))
        page.setVisible(True)
        layout = page.layout()
        if layout is not None:
            layout.activate()
    QApplication.processEvents()
    for page in pages:
        # Once more now that everything has been polished: a form laid out
        # before its fonts were resolved is measured against the wrong line
        # height.
        page.setGeometry(0, 0, width, max(height - chrome, 1))
        layout = page.layout()
        if layout is not None:
            layout.activate()
    QApplication.processEvents()
    # Held on the window so nothing here is collected while the pages are being
    # measured; a deleted parent takes its children with it.
    window._fullscreen_host = host
    return pages


def still_given_up(window: ConsoleWindow) -> str:
    """The stream line, checked at the moment the measurement is taken.

    Laying the pages out runs the console's own code, and anything that put a
    pane back to "connecting" on the way would silently shrink the longest
    sentence on the Live tab back to 22 characters - which is exactly the way
    this file stopped measuring what it says it measures. Asserted here rather
    than assumed, and returned so a failure names the string that was sized.
    """
    for name in window.live.stream_names():
        text = window.live.stream_label_text(name)
        assert GIVEN_UP_WORDS in text, f"{name} was measured as {text!r}"
    return window.live.stream_label_text(window.live.stream_names()[0])


def speakers(page: QWidget) -> list[QWidget]:
    """Everything on this page that is showing words, and is big enough to see."""
    out = []
    for widget in page.findChildren(QWidget):
        if not isinstance(widget, SPEAKS) or not widget.isVisibleTo(page):
            continue
        if widget.width() <= 0 or widget.height() <= 0:
            continue
        if isinstance(widget, QLabel) and not widget.text():
            continue
        out.append(widget)
    return out


def describe(widget: QWidget) -> str:
    for name in ("text", "toPlainText", "title"):
        reader = getattr(widget, name, None)
        if callable(reader):
            try:
                words = reader()
            except Exception:  # noqa: BLE001 - this is a test's description
                continue
            if words:
                return f"{type(widget).__name__}({str(words)[:60]!r})"
    return f"{type(widget).__name__}(...)"


def _related(one: QWidget, other: QWidget) -> bool:
    """Is one inside the other? A form's line edit sits inside its group box on
    purpose; only widgets that are meant to be side by side may not overlap."""
    for a, b in ((one, other), (other, one)):
        parent = a
        while parent is not None:
            if parent is b:
                return True
            parent = parent.parentWidget()
    return False


def _where(page: QWidget, widget: QWidget) -> QRect:
    return QRect(widget.mapTo(page, QPoint(0, 0)), widget.size())


def mushed(page: QWidget) -> list[str]:
    """Every pair of sentences drawn through each other on this page."""
    said = speakers(page)
    through: list[str] = []
    for index, one in enumerate(said):
        box = _where(page, one)
        for other in said[index + 1 :]:
            if _related(one, other):
                continue
            overlap = box.intersected(_where(page, other))
            # A pixel of touching is a rounded border; two are not a sentence
            # drawn through a sentence either. Three is.
            if overlap.width() > 2 and overlap.height() > 2:
                through.append(f"{describe(one)} over {describe(other)}")
    return through


def starved(page: QWidget) -> list[str]:
    """Every widget given less height than the words in it need.

    Two ways to be short, and the operator cannot tell them apart: a word-wrapped
    label that was measured as one line and wraps to four, and any widget at all
    squeezed below the least height it says it can be drawn in.
    """
    short: list[str] = []
    for widget in speakers(page):
        if isinstance(widget, QHeaderView):
            # A table header asks for room for a sort indicator it never draws,
            # and reports the same shortfall in a window with space to spare, so
            # it says nothing about whether anything was squeezed.
            continue
        needed = widget.minimumSizeHint().height()
        if isinstance(widget, QLabel) and widget.wordWrap():
            needed = max(needed, widget.heightForWidth(max(widget.width(), 1)))
        if needed > widget.height() + 1:
            short.append(f"{describe(widget)} has {widget.height()} px of {needed}")
    return short


@pytest.mark.parametrize("width,height", SCREENS)
def test_no_tab_draws_a_sentence_through_another_one_when_fullscreen(
    qtbot, tmp_path: Path, width: int, height: int
) -> None:
    """The reported fault, at every size this console is run at.

    Fullscreen hands the window the screen and nothing more. A tab that wanted
    more than that does not get a scrollbar for free: Qt gives every widget in it
    a share of what there is, below its own minimum, and the sentences land on
    top of one another. The operator reads this window to find out what is wrong
    with the system, so a warning drawn through the line beneath it is the
    warning being unreadable at the moment it matters.
    """
    window = console(qtbot, tmp_path)
    in_the_worst_state(window)
    pages = as_fullscreen(qtbot, window, width, height)
    still_given_up(window)
    problems: list[str] = []
    for page, name in zip(pages, ["Live", "Playback", "Settings", "Logs"]):
        problems += [f"{name}: {line}" for line in mushed(page)]
    assert problems == [], "text drawn through text at %dx%d:\n%s" % (
        width,
        height,
        "\n".join(problems[:12]),
    )


@pytest.mark.parametrize("width,height", SCREENS)
def test_no_sentence_in_the_side_column_is_cut_in_half(
    qtbot, tmp_path: Path, width: int, height: int
) -> None:
    """The column's own question, asked the way the two panels in it ask it."""
    window = console(qtbot, tmp_path)
    in_the_worst_state(window)
    live, _playback, _settings, _logs = as_fullscreen(qtbot, window, width, height)
    still_given_up(window)
    assert live.camera_note(), "the camera note has to be saying something"
    assert live.clipped() == [], "half a sentence is worse than none"


def test_the_side_columns_wrapped_sentences_ask_for_the_height_they_need(
    qtbot, tmp_path: Path
) -> None:
    """What the two wrapped sentences in the side column ASK for, not only what
    they were given.

    They are whole on screen today, and they are whole for a reason that is not
    their own doing: the column is a QScrollArea, and a scroll area asks its
    widget's layout how tall it is at the width it has rather than believing the
    width-independent minimum. Take that away - a column that stops scrolling, a
    panel moved into a layout that believes what it is told - and a word-wrapped
    QLabel says it can live in one line. The camera note is two lines of the 340
    px column and the note under the movement list is four, so what the operator
    would lose is the sentence saying the camera never answered the last command,
    and the sentence saying a blank means unidentified rather than uncertain.

    Measured with the column squeezed to the least it says it can live with: 16
    px of the 42 the camera note needs.
    """
    window = console(qtbot, tmp_path)
    in_the_worst_state(window)
    live, _playback, _settings, _logs = as_fullscreen(qtbot, window, 1366, 768)
    still_given_up(window)

    for note in (live._ptz_note, live._movement_note):
        needed = note.heightForWidth(max(note.width(), 1))
        assert note.minimumHeight() >= needed, (
            f"{note.text()[:60]!r} asks for {note.minimumHeight()} px and needs {needed}"
        )


@pytest.mark.parametrize("width,height", SCREENS)
def test_no_tab_is_squeezed_below_the_words_it_holds_when_fullscreen(
    qtbot, tmp_path: Path, width: int, height: int
) -> None:
    """The same fault, measured one widget at a time rather than in pairs.

    Two sentences that miss each other by a pixel are still both unreadable when
    each is drawn in four pixels of a twenty-one pixel line.
    """
    window = console(qtbot, tmp_path)
    in_the_worst_state(window)
    pages = as_fullscreen(qtbot, window, width, height)
    still_given_up(window)
    problems: list[str] = []
    for page, name in zip(pages, ["Live", "Playback", "Settings", "Logs"]):
        problems += [f"{name}: {line}" for line in starved(page)]
    assert problems == [], "squeezed below what the words need at %dx%d:\n%s" % (
        width,
        height,
        "\n".join(problems[:12]),
    )


@pytest.mark.parametrize("width,height", SCREENS)
def test_unfolding_a_stream_card_does_not_draw_text_over_text(
    qtbot, tmp_path: Path, width: int, height: int
) -> None:
    """The Settings tab as he meets it, which is one press at a time.

    Everything above measures a window that was laid out once and looked at.
    That is not how this tab is used: he opens it, ticks **Watch for movement**
    on a view, presses **Ignore parts of the picture**, and the card grows by
    about 420 px underneath a form that has already settled. Pressing that
    button drew "How touchy:" through the last line of the note above it and
    two more sentences through each other, at every size on this list, and none
    of the tests here saw it because none of them pressed anything.

    So the folds are opened AFTER the window has been laid out, and only then is
    the tab measured. Two views with one of them unfolded, because that is the
    state in the screenshot and the state the grid gets wrong: a row of cards is
    as tall as its tallest card, and the height it remembers is the height they
    both used to be.
    """
    window = console(qtbot, tmp_path)
    window.resize(width, height)
    # Laid out at the size, never put in front of him: this console runs on a
    # laptop somebody is watching.
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    window.tabs.setCurrentIndex(2)
    QApplication.processEvents()

    settings = window.settings_tab
    rows = settings.stream_rows()
    assert len(rows) == 2, "the camera has two heads and the defect needs both"
    for row in rows:
        row.detect_field.setChecked(True)
    QApplication.processEvents()
    rows[0].details_button.setChecked(True)
    QApplication.processEvents()

    problems = mushed(settings) + starved(settings)
    window.hide()
    assert problems == [], "text drawn through text at %dx%d after a fold opened:\n%s" % (
        width,
        height,
        "\n".join(problems[:12]),
    )


def test_a_stream_card_that_grows_takes_the_form_with_it(
    qtbot, tmp_path: Path
) -> None:
    """The card with one more sentence on it than it has today.

    The collision above was found with two help paragraphs printed on every
    card. They have been said once above both cards instead, which is a better
    tab and also, by itself, enough to stop the text colliding - the card got
    short enough that the fault stopped showing. That is not the same as fixing
    it. The fault is that the grid holding the cards side by side goes on
    reporting the height the row was before a fold opened, and it comes back the
    day somebody puts one more line on a card, which is exactly how it arrived.

    So a card is given one more sentence here - a plain note of the kind this
    tab is full of - and then unfolded. Nothing about the measurement changes;
    only the amount of card there is to fit.
    """
    from vmd.desktop.settings_tab import CLASSIFY_HELP, _note

    window = console(qtbot, tmp_path)
    window.resize(1366, 768)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    window.tabs.setCurrentIndex(2)
    QApplication.processEvents()

    settings = window.settings_tab
    rows = settings.stream_rows()
    for row in rows:
        row.watched.layout().addWidget(_note(CLASSIFY_HELP))
        row.detect_field.setChecked(True)
    QApplication.processEvents()
    rows[0].details_button.setChecked(True)
    QApplication.processEvents()

    problems = mushed(settings) + starved(settings)
    window.hide()
    assert problems == [], "one more sentence on a card and it collides again:\n%s" % (
        "\n".join(problems[:12]),
    )
