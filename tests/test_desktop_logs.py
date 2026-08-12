"""Whatever the system says about itself, where the operator can read it."""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from vmd.desktop.logs import LogBuffer, LogsTab, without_passwords
from vmd.desktop.style import PALETTE

# --------------------------------------------------------------- the plan's own tests


def test_records_are_kept_in_order() -> None:
    buffer = LogBuffer(capacity=10)
    logger = logging.getLogger("vmd.test.order")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("first")
        logger.warning("second")
    finally:
        logger.removeHandler(buffer)

    lines = buffer.snapshot()
    assert [line["text"] for line in lines] == ["first", "second"]
    assert lines[1]["level"] == "WARNING"


def test_the_oldest_lines_fall_off_rather_than_growing_forever() -> None:
    buffer = LogBuffer(capacity=3)
    logger = logging.getLogger("vmd.test.capacity")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for i in range(10):
            logger.info("line %d", i)
    finally:
        logger.removeHandler(buffer)

    lines = buffer.snapshot()
    assert len(lines) == 3
    assert lines[-1]["text"] == "line 9"


def test_a_traceback_is_kept_with_its_message() -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.exception")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("it failed")
    finally:
        logger.removeHandler(buffer)

    assert "boom" in buffer.snapshot()[0]["text"]


def test_logging_never_raises_into_the_caller() -> None:
    """A broken log call must not take the console with it.

    propagate is disabled so the only handler in play is the buffer under
    test - pytest's own log-capture handler is attached to the root logger
    regardless of this test, and is not what is being verified here.
    """
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.bad")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("%d", "not a number")  # deliberately wrong
    finally:
        logger.removeHandler(buffer)
        logger.propagate = True

    assert len(buffer.snapshot()) == 1


def test_the_tab_shows_what_the_buffer_holds(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.tab")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("visible line")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.row_count == 1
    assert "visible line" in tab.text_at(0)


def test_the_tab_can_show_only_what_went_wrong(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.filter")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("ordinary")
        logger.error("bad")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.set_level_filter("WARNING")
    tab.refresh()
    assert tab.row_count == 1
    assert "bad" in tab.text_at(0)


# ------------------------------------------------------- required beyond the plan


def test_filter_buttons_are_wired_to_the_filter(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.filterbuttons")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("ordinary")
        logger.error("bad")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.row_count == 2

    qtbot.mouseClick(tab.warnings_button, Qt.MouseButton.LeftButton)
    assert tab.row_count == 1
    assert "bad" in tab.text_at(0)

    qtbot.mouseClick(tab.all_button, Qt.MouseButton.LeftButton)
    assert tab.row_count == 2


def test_the_view_follows_new_lines_when_already_at_the_bottom(qtbot) -> None:
    buffer = LogBuffer(capacity=100)
    logger = logging.getLogger("vmd.test.follow.bottom")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for i in range(40):
            logger.info("line %d", i)
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.resize(320, 120)
    tab.follow_checkbox.setChecked(False)  # isolate the at-bottom detection itself
    tab.refresh()
    scrollbar = tab.table.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    assert scrollbar.value() == scrollbar.maximum()

    logger.addHandler(buffer)
    try:
        logger.info("line 40 - new")
    finally:
        logger.removeHandler(buffer)
    tab.refresh()

    assert scrollbar.value() == scrollbar.maximum()
    assert "line 40" in tab.text_at(tab.row_count - 1)


def test_the_follow_box_says_what_it_follows(qtbot) -> None:
    """It said "Follow". Follow what, and what happens if it is not ticked?

    It is a tick box, so what it carries is a state rather than an instruction,
    and the state is that the table stays at the newest line as lines arrive
    instead of staying where he has scrolled to.
    """
    tab = LogsTab(LogBuffer())
    qtbot.addWidget(tab)

    said = tab.follow_checkbox.text()
    assert said == "Keep showing the newest lines"
    assert said != "Follow"
    # It names the thing it acts on - the lines in the table - and the tooltip
    # says what unticking it buys, which is the half nobody could guess.
    assert "lines" in said.lower()
    assert "scroll" in tab.follow_checkbox.toolTip().lower()
    # And it is still on to begin with, which is what a console nobody is
    # sitting at wants.
    assert tab.follow_checkbox.isChecked()


def test_the_view_does_not_move_while_the_operator_has_scrolled_up(qtbot) -> None:
    buffer = LogBuffer(capacity=100)
    logger = logging.getLogger("vmd.test.follow.scrolled")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for i in range(40):
            logger.info("line %d", i)
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.resize(320, 120)
    tab.follow_checkbox.setChecked(False)
    tab.refresh()
    scrollbar = tab.table.verticalScrollBar()
    scrollbar.setValue(0)  # the operator scrolled to the top mid-incident
    assert scrollbar.value() == 0

    logger.addHandler(buffer)
    try:
        logger.info("line 40 - new")
    finally:
        logger.removeHandler(buffer)
    tab.refresh()

    assert scrollbar.value() == 0, "a scrolled-up operator must not be yanked to the bottom"


def test_error_rows_are_coloured_for_visibility_under_glare(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.colour")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("ordinary")
        logger.error("bad")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()

    assert tab.level_color_at(0) == QColor(PALETTE["muted"])
    assert tab.level_color_at(1) == QColor(PALETTE["alarm"])


def test_warning_rows_use_the_warn_colour(qtbot) -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.colour.warn")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.warning("careful")
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.level_color_at(0) == QColor(PALETTE["warn"])


def test_refresh_is_cheap_enough_to_run_on_a_timer(qtbot) -> None:
    buffer = LogBuffer(capacity=500)
    logger = logging.getLogger("vmd.test.perf")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for i in range(500):
            logger.info("line %d", i)
    finally:
        logger.removeHandler(buffer)

    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()  # first build, not part of the timing

    start = time.perf_counter()
    for _ in range(20):
        tab.refresh()
    elapsed = time.perf_counter() - start
    average_ms = (elapsed / 20) * 1000
    assert average_ms < 50, f"refresh averaged {average_ms:.2f}ms, too slow for a timer"


def test_the_buffer_survives_concurrent_writers_and_a_reading_ui_thread() -> None:
    """go2rtc's output pump and the supervisor log from their own threads while
    the UI thread reads. No exception, and every snapshot must be well-formed."""
    buffer = LogBuffer(capacity=50)
    logger = logging.getLogger("vmd.test.threads")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)

    errors: list[BaseException] = []
    stop = threading.Event()

    def write(worker: int) -> None:
        try:
            for i in range(200):
                logger.info("worker %d line %d", worker, i)
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion
            errors.append(exc)

    def read() -> None:
        try:
            while not stop.is_set():
                snapshot = buffer.snapshot()
                assert isinstance(snapshot, list)
                assert len(snapshot) <= 50
                for line in snapshot:
                    assert set(line.keys()) >= {"time", "level", "source", "text"}
                time.sleep(0.001)  # a UI timer polls; it does not spin
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion
            errors.append(exc)

    reader = threading.Thread(target=read)
    writers = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    reader.start()
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    reader.join()
    logger.removeHandler(buffer)

    assert errors == []
    final = buffer.snapshot()
    assert len(final) == 50


# ------------------------------------------------------------ what must not show
#
# The Logs tab is the one place every process on this machine is heard, and it
# is on screen. go2rtc carries a `[streams] retry=%d to url=%s` line, and the
# URL it retries is the one the console built for it - credentials and all.


def test_a_password_in_a_url_never_reaches_the_log() -> None:
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.credentials")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.warning(
            "go2rtc: [streams] retry=3 to url=%s",
            "rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.2:554/ch2",
        )
        logger.info("ffmpeg reading rtsp://viewer:hunter2@10.0.0.2/ch1")
    finally:
        logger.removeHandler(buffer)

    retried, reading = (line["text"] for line in buffer.snapshot())
    assert "p%40ss%3Aw%2Frd" not in retried
    assert "hunter2" not in reading
    # And it still says what an operator has to know: which account was refused,
    # and which camera refused it.
    assert "admin" in retried and "10.0.0.2:554/ch2" in retried
    assert "viewer" in reading and "10.0.0.2/ch1" in reading


def test_an_ordinary_line_is_left_exactly_as_it_was() -> None:
    """Local URLs carry no credentials, and most lines are not URLs at all."""
    buffer = LogBuffer(capacity=5)
    logger = logging.getLogger("vmd.test.ordinary")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("showing rtsp://127.0.0.1:8554/thermal")
        logger.info("recorder: segment closed at 12:04:07 - user@host wrote it")
    finally:
        logger.removeHandler(buffer)

    assert [line["text"] for line in buffer.snapshot()] == [
        "showing rtsp://127.0.0.1:8554/thermal",
        "recorder: segment closed at 12:04:07 - user@host wrote it",
    ]


def test_a_flood_of_a_line_is_not_searched_for_a_url_it_cannot_contain() -> None:
    """A child can write half a megabyte without a newline - the reason the
    line limit exists - and every one of those bytes passes through here. The
    line is returned untouched, not merely unchanged."""
    flood = "x" * 60_000
    started = time.monotonic()
    assert without_passwords(flood) is flood
    assert time.monotonic() - started < 1.0


def test_a_long_line_that_could_hold_a_url_is_still_searched_quickly() -> None:
    """Bounded, so that a line which happens to contain both `://` and `@` is
    scanned once rather than backtracked across at every position in it."""
    noisy = "x" * 30_000 + " rtsp://admin:hunter2@10.0.0.2/ch1 " + "y" * 30_000
    started = time.monotonic()
    scrubbed = without_passwords(noisy)
    assert time.monotonic() - started < 1.0
    assert "hunter2" not in scrubbed
    assert "rtsp://admin:****@10.0.0.2/ch1" in scrubbed


# ------------------------------------------------- who said it, and when it was said
#
# Two things the Logs tab was missing, both of which cost the operator the
# messages the tab exists for.


def test_the_table_says_which_process_a_line_came_from(qtbot) -> None:
    """LogBuffer has recorded a `source` since it was written and the table
    never showed it. The children were only distinguishable because they happen
    to prefix their own messages; the console's own modules were not
    distinguishable at all."""
    buffer = LogBuffer()
    buffer.emit(_record("go2rtc", "401 Unauthorized"))
    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.source_at(0) == "go2rtc"
    assert "401 Unauthorized" in tab.text_at(0)


def test_everything_logged_before_the_window_existed_still_reaches_the_tab(
    qtbot, tmp_path
) -> None:
    """The buffer used to be attached inside ConsoleWindow.__init__, which runs
    after the services have been started. Everything they say while starting -
    "adopted from an earlier run", "part of VMD is missing, so there are no
    pictures", "could not start the recorder" - went nowhere at all, and those are exactly
    the messages this tab exists for on a machine with no terminal."""
    from vmd.desktop.app import start_logging

    buffer = start_logging()
    logging.getLogger("vmd.desktop.services").warning(
        "recorder: adopted from an earlier run (pid 36668)"
    )
    tab = LogsTab(buffer)
    qtbot.addWidget(tab)
    tab.refresh()
    said = [tab.text_at(row) for row in range(tab.row_count)]
    assert any("adopted from an earlier run" in line for line in said)


def _record(name: str, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=logging.WARNING, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )


def test_the_logs_tab_says_when_nothing_has_been_logged(qtbot) -> None:
    """A black rectangle with a header on it is indistinguishable from a tab
    that failed to load, and this tab is the only place on the machine where
    the operator can read what went wrong."""
    from vmd.desktop.logs import LogBuffer, LogsTab

    tab = LogsTab(LogBuffer())
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab.row_count == 0
    assert tab.empty.isVisibleTo(tab)
    assert tab.table.isVisibleTo(tab) is False

    tab._buffer.records.append(
        {"seq": 1, "time": 0.0, "level": "INFO", "source": "go2rtc", "text": "listening"}
    )
    tab.refresh()
    assert tab.table.isVisibleTo(tab)
    assert tab.empty.isVisibleTo(tab) is False


def test_the_message_column_does_not_move_when_a_line_arrives(qtbot) -> None:
    """Nothing on a console anyone is watching should shift sideways because a
    value changed."""
    from vmd.desktop.logs import LogBuffer, LogsTab

    tab = LogsTab(LogBuffer())
    qtbot.addWidget(tab)
    tab.resize(900, 400)
    tab._buffer.records.append(
        {"seq": 1, "time": 0.0, "level": "INFO", "source": "go2rtc", "text": "listening"}
    )
    tab.refresh()
    before = [tab.table.columnWidth(i) for i in range(3)]
    tab._buffer.records.append(
        {
            "seq": 2, "time": 0.0, "level": "WARNING",
            "source": "vmd.desktop.services.supervisor",
            "text": "the recorder exited at once and was started again",
        }
    )
    tab.refresh()
    assert [tab.table.columnWidth(i) for i in range(3)] == before


# ------------------------------------------------------------ getting it out
#
# From the operator, after a day of it: he has been reading lines off this table
# and typing them into a chat by hand, because this machine has no terminal and
# the Logs tab is where everything the console, go2rtc and the recorder say ends
# up. A tab that is the only diagnostic on the machine has to be able to hand
# what it holds to a person who is not standing in front of it.


def buffer_with(name: str, *lines: tuple[str, str]) -> LogBuffer:
    buffer = LogBuffer(capacity=20)
    logger = logging.getLogger(name)
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        for level, message in lines:
            getattr(logger, level)(message)
    finally:
        logger.removeHandler(buffer)
    return buffer


def test_the_lines_on_screen_can_be_had_as_text(qtbot) -> None:
    tab = LogsTab(
        buffer_with(
            "vmd.test.copy",
            ("info", "the ordinary one"),
            ("error", "401 Unauthorized"),
        )
    )
    qtbot.addWidget(tab)
    tab.refresh()

    text = tab.text_for_copying()

    assert "the ordinary one" in text
    assert "401 Unauthorized" in text
    assert len(text.splitlines()) == 2, "one line per line, or it cannot be read"
    assert "ERROR" in text, "the level has to survive the copy"
    assert "copy" in text.lower(), "and which logger said it"


def test_only_what_is_on_screen_is_copied(qtbot) -> None:
    """The filter is how the operator narrows this to the fault he is chasing.
    Copying everything anyway would hand over the haystack he has just removed."""
    tab = LogsTab(
        buffer_with(
            "vmd.test.copyfilter",
            ("info", "ordinary"),
            ("error", "the fault"),
        )
    )
    qtbot.addWidget(tab)
    tab.set_level_filter("WARNING")
    tab.refresh()

    text = tab.text_for_copying()

    assert "the fault" in text
    assert "ordinary" not in text


def test_the_copy_button_puts_it_on_the_clipboard_and_says_so(qtbot) -> None:
    """A button that does nothing visible, on a machine with no other feedback,
    reads as a button that is broken - and he presses it again."""
    from PySide6.QtWidgets import QApplication

    tab = LogsTab(buffer_with("vmd.test.copybutton", ("error", "401 Unauthorized")))
    qtbot.addWidget(tab)
    tab.refresh()

    tab.copy_button.click()

    assert "401 Unauthorized" in QApplication.clipboard().text()
    said = tab.copy_note.text().lower()
    assert "1" in said and "copied" in said, said


def test_copying_an_empty_table_says_there_was_nothing(qtbot) -> None:
    tab = LogsTab(LogBuffer(capacity=5))
    qtbot.addWidget(tab)
    tab.refresh()

    tab.copy_button.click()

    assert tab.copy_note.text(), "silence here reads as a button that did not work"



def test_which_filter_is_on_is_visible_without_reading_the_table(qtbot) -> None:
    """The two renders of this tab used to be pixel-identical.

    The filter is how he narrows a screen of lines down to the fault he is
    chasing, and a filter whose state is invisible is a filter that makes him
    doubt the log: a quiet Logs tab means "nothing has gone wrong" and "you are
    only being shown the errors" and there was nothing on screen to say which.
    Both were plain buttons, neither checkable, neither in a group.

    Measured the way the operator reads it - is the one that is on drawn
    differently from the one that is not - rather than against a particular
    colour, so the palette can move without this failing.
    """
    tab = LogsTab(LogBuffer(capacity=5))
    qtbot.addWidget(tab)

    on, off = tab.all_button, tab.warnings_button
    assert on.isCheckable() and off.isCheckable(), "a filter has to hold its state"
    assert on.isChecked() and not off.isChecked(), "All is where this tab starts"

    marked = tab.filter_mark(on)
    assert marked and marked != tab.filter_mark(off), (
        "the filter that is on is drawn exactly like the one that is not"
    )
    # The accent, which is what this design reserves for an active control and
    # what the Playback tab's own zoom buttons already use.
    assert PALETTE["accent"] in marked, marked

    qtbot.mouseClick(off, Qt.MouseButton.LeftButton)
    assert off.isChecked() and not on.isChecked(), "both filters cannot be on"
    assert tab.filter_mark(off) == marked
    assert tab.filter_mark(on) != marked
