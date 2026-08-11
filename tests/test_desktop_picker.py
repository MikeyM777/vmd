"""Drawing the sky line and the ignored patches on a real picture.

Both settings are native-frame pixel coordinates, and until now both were typed
into a spin box with no picture on screen and nothing saying how tall the frame
even is. Nobody can read "340" off a treeline. A wrong sky line deletes real
movement silently, and a patch in the wrong place does the same over a
rectangle, so the numbers being guessable is the whole problem.

The mapping tests are the load-bearing ones. The preview is scaled to fit a
dialog and the setting is absolute, so getting the two confused misplaces every
region without a word of complaint - which is why it is checked at a preview
smaller than the frame, larger than the frame, and letterboxed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

from vmd.desktop.picker import (
    FrameUnavailable,
    FramePicker,
    PickerDialog,
    region_between,
    to_frame,
    to_view,
)
from vmd.desktop.settings_tab import CameraTools, SettingsTab
from vmd.settings import CameraSettings, Settings, StreamSettings

FRAME = QSize(1280, 720)


_MADE: dict[tuple[int, int], bytes] = {}


def a_frame(width: int = 1280, height: int = 720) -> bytes:
    """A real picture, in the bytes a camera grab would hand back.

    Made once and remembered, because a fake grab is called from a pool thread
    and encoding a picture there is Qt work on a thread that has no business
    doing any. The bytes themselves are just bytes.
    """
    if (width, height) not in _MADE:
        image = QImage(width, height, QImage.Format.Format_RGB32)
        # A gradient and not a flat fill. A rectangle of one colour is exactly
        # what the dialog now refuses - it is what a half-decoded first frame
        # off a live stream looks like - so a flat fixture would be testing the
        # drawing against a picture the console is right to reject.
        image.fill(0x203040)
        painter = QPainter(image)
        sky = QLinearGradient(0, 0, 0, height)
        sky.setColorAt(0.0, QColor(20, 24, 32))
        sky.setColorAt(1.0, QColor(190, 195, 205))
        painter.fillRect(0, 0, width, height, sky)
        painter.end()
        # Held in a name: a QByteArray passed straight into QBuffer is a Python
        # temporary, and Qt keeps the pointer after Python has freed it.
        store = QByteArray()
        buffer = QBuffer(store)
        buffer.open(QBuffer.OpenModeFlag.ReadWrite)
        image.save(buffer, "PNG")
        buffer.close()
        _MADE[(width, height)] = bytes(store)
    return _MADE[(width, height)]


a_frame()  # made on the main thread, before any test asks for it from a pool one


def a_dialog(qtbot, grab=None, horizon=None, regions=(), stream="thermal"):
    dialog = PickerDialog(
        stream=stream,
        horizon=horizon,
        regions=list(regions),
        grab=grab if grab is not None else a_frame,
    )
    qtbot.addWidget(dialog)
    return dialog


def sized(picker, width: int, height: int) -> None:
    """Force the preview to an exact size. The real one has a floor under it so
    that it is big enough to aim at; these tests are about the arithmetic."""
    picker.setMinimumSize(1, 1)
    picker.resize(width, height)


def with_a_picture(qtbot, **kwargs):
    dialog = a_dialog(qtbot, **kwargs)
    qtbot.waitUntil(dialog.picker.has_frame, timeout=5000)
    return dialog


# ------------------------------------------------ the preview is not the frame


def test_a_click_is_read_in_the_real_pictures_dots_however_it_is_shown() -> None:
    """The same click, at three preview sizes, means the same row of the frame."""
    # A quarter the size: the preview is smaller than the frame.
    assert to_frame(QPoint(160, 90), QSize(320, 180), FRAME) == (640, 360)
    # Twice the size: the preview is larger than the frame.
    assert to_frame(QPoint(1280, 720), QSize(2560, 1440), FRAME) == (640, 360)
    # A square window: the picture is letterboxed, and the bars are not picture.
    # 400/1280 fits before 400/720 does, so the picture is 400 x 225 with 87.5
    # dots of dead space above it.
    assert to_frame(QPoint(200, 200), QSize(400, 400), FRAME) == (640, 360)


def test_the_top_of_the_picture_is_the_top_of_the_frame_at_every_size() -> None:
    """The letterbox bars are not picture. Reading a click as if they were puts
    every patch out by the depth of the bar."""
    for view in (QSize(320, 180), QSize(2560, 1440), QSize(400, 400)):
        x, y = to_frame(to_view(0, 0, view, FRAME).toPoint(), view, FRAME)
        assert x == 0, view
        assert y <= 1, view  # within a dot of the top, whatever the rounding


def test_a_click_in_the_dead_space_is_pulled_back_into_the_picture() -> None:
    """A square window has bars above and below. A click there is still a click,
    and a negative row of dots is not a setting."""
    x, y = to_frame(QPoint(200, 2), QSize(400, 400), FRAME)
    assert y == 0
    assert 0 <= x < FRAME.width()
    x, y = to_frame(QPoint(398, 398), QSize(400, 400), FRAME)
    assert y == FRAME.height() - 1
    assert x < FRAME.width()


def test_a_dragged_box_is_in_the_real_pictures_dots_at_any_size() -> None:
    small = region_between(QPoint(40, 20), QPoint(120, 70), QSize(320, 180), FRAME)
    assert small == (160, 80, 320, 200)
    large = region_between(QPoint(320, 160), QPoint(960, 560), QSize(2560, 1440), FRAME)
    assert large == (160, 80, 320, 200)


def test_a_box_dragged_backwards_is_still_a_box() -> None:
    """Dragging up and to the left is how half of people draw a rectangle."""
    forwards = region_between(QPoint(120, 70), QPoint(40, 20), QSize(320, 180), FRAME)
    assert forwards == (160, 80, 320, 200)


def test_a_box_dragged_off_the_edge_stops_at_the_edge() -> None:
    x, y, w, h = region_between(QPoint(160, 90), QPoint(400, 400), QSize(320, 180), FRAME)
    assert x + w <= FRAME.width()
    assert y + h <= FRAME.height()


# ------------------------------------------------------- drawing on the picture


def test_clicking_the_picture_puts_the_sky_line_there_and_shows_the_number(
    qtbot,
) -> None:
    dialog = with_a_picture(qtbot)
    sized(dialog.picker, 320, 180)
    qtbot.mouseClick(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(100, 45))

    assert dialog.horizon() == 180
    # And as a number, because the setting stays typeable and the operator has
    # to be able to see what they just set.
    assert "180" in dialog.horizon_text()


def test_dragging_a_box_adds_a_patch_in_the_real_pictures_dots(qtbot) -> None:
    dialog = with_a_picture(qtbot)
    sized(dialog.picker, 320, 180)
    qtbot.mousePress(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    qtbot.mouseRelease(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(120, 70))

    assert dialog.regions() == [(160, 80, 320, 200)]
    assert dialog.horizon() is None, "a drag is not a click and must not move the line"


def test_a_drag_too_small_to_be_a_box_is_a_click(qtbot) -> None:
    """Nobody presses and releases on the exact same dot. A wobble of a dot or
    two is a click, and a click is the sky line."""
    dialog = with_a_picture(qtbot)
    sized(dialog.picker, 320, 180)
    qtbot.mousePress(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(100, 45))
    qtbot.mouseRelease(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(101, 46))

    assert dialog.regions() == []
    assert dialog.horizon() is not None


def test_the_patches_already_set_are_shown_and_can_be_deleted(qtbot) -> None:
    """The path that matters most today: an operator with a patch in the wrong
    place currently has no way out of it but editing the file by hand."""
    dialog = with_a_picture(qtbot, regions=[(10, 20, 30, 40), (50, 60, 70, 80)])
    assert dialog.regions() == [(10, 20, 30, 40), (50, 60, 70, 80)]
    assert dialog.regions_list.count() == 2
    # And they are on the picture, not only in the list.
    assert dialog.picker.regions() == dialog.regions()

    dialog.regions_list.setCurrentRow(0)
    qtbot.mouseClick(dialog.remove_button, Qt.MouseButton.LeftButton)
    assert dialog.regions() == [(50, 60, 70, 80)]
    assert dialog.picker.regions() == [(50, 60, 70, 80)]


def test_the_sky_line_can_be_taken_off_again(qtbot) -> None:
    dialog = with_a_picture(qtbot, horizon=340)
    assert dialog.horizon() == 340
    qtbot.mouseClick(dialog.clear_horizon_button, Qt.MouseButton.LeftButton)
    assert dialog.horizon() is None


def test_the_dialog_says_how_big_the_picture_really_is(qtbot) -> None:
    """The complaint these controls started from: nothing on screen ever said
    how tall the frame is, so "340" could not be judged against anything."""
    dialog = with_a_picture(qtbot)
    assert dialog.picker.frame_size() == FRAME
    said = dialog.size_text()
    assert "1280" in said and "720" in said


# ----------------------------------------------------------- when there is none


def test_a_camera_that_sends_no_picture_says_so_in_one_plain_sentence(qtbot) -> None:
    def refuse():
        raise FrameUnavailable(
            "The camera did not send a picture, so there is nothing to draw on."
        )

    dialog = a_dialog(qtbot, grab=refuse, horizon=340, regions=[(1, 2, 3, 4)])
    qtbot.waitUntil(lambda: bool(dialog.problem_text()), timeout=5000)

    told = dialog.problem_text()
    assert "did not send a picture" in told
    assert "traceback" not in told.lower()
    assert "exception" not in told.lower()
    # And the numbers the operator already had are handed straight back, so the
    # boxes in the form are exactly as they were.
    assert dialog.horizon() == 340
    assert dialog.regions() == [(1, 2, 3, 4)]
    assert not dialog.picker.has_frame()


def test_a_tool_that_throws_something_else_is_still_a_sentence(qtbot) -> None:
    def explode():
        raise RuntimeError("ffmpeg is not installed")

    dialog = a_dialog(qtbot, grab=explode)
    qtbot.waitUntil(lambda: bool(dialog.problem_text()), timeout=5000)
    assert "ffmpeg is not installed" in dialog.problem_text()


def test_bytes_that_are_not_a_picture_are_refused_rather_than_shown_blank(
    qtbot,
) -> None:
    """A blank preview is worse than none: the operator would trust the line
    they dragged on it."""
    dialog = a_dialog(qtbot, grab=lambda: b"this is not a picture")
    qtbot.waitUntil(lambda: bool(dialog.problem_text()), timeout=5000)
    assert not dialog.picker.has_frame()


# ------------------------------------------------------------------ the thread


def test_the_picture_is_fetched_off_the_window_thread(qtbot) -> None:
    """Pulling a frame over the radio link takes seconds. On the UI thread that
    is a console that looks dead, which is a console the operator restarts."""
    where: list[str] = []

    def grab():
        where.append(threading.current_thread().name)
        return a_frame()

    dialog = a_dialog(qtbot, grab=grab)
    qtbot.waitUntil(dialog.picker.has_frame, timeout=5000)
    assert where and where[0] != threading.main_thread().name


def test_the_window_answers_while_the_picture_is_being_fetched(qtbot) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(10)
        return a_frame()

    dialog = a_dialog(qtbot, grab=slow)
    qtbot.waitUntil(started.is_set, timeout=5000)
    # Still fetching, and the dialog is answering questions about itself.
    assert not dialog.picker.has_frame()
    assert dialog.problem_text() == ""
    release.set()
    qtbot.waitUntil(dialog.picker.has_frame, timeout=5000)


def test_closing_the_dialog_leaves_no_thread_behind(qtbot) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(3)
        return a_frame()

    dialog = a_dialog(qtbot, grab=slow)
    qtbot.waitUntil(started.is_set, timeout=5000)
    release.set()
    dialog.reject()
    assert dialog.busy() is False


def test_a_picture_that_arrives_after_the_dialog_is_gone_is_dropped(qtbot) -> None:
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(3)
        return a_frame()

    dialog = a_dialog(qtbot, grab=slow)
    qtbot.waitUntil(started.is_set, timeout=5000)
    dialog.reject()
    release.set()
    qtbot.wait(200)
    assert not dialog.picker.has_frame()


# ------------------------------------------------------------ the plain words


def test_nothing_on_screen_is_written_for_an_engineer(qtbot) -> None:
    dialog = with_a_picture(qtbot, horizon=100, regions=[(1, 2, 3, 4)])
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor")
    for text in dialog.words_on_screen():
        assert text.strip(), "an empty label tells nobody anything"
        assert not any(word in text.lower() for word in banned), text


# ------------------------------------------------------ back into the settings


def _settings() -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/ch2")],
        )
    )


def a_tab(qtbot, tmp_path: Path, grab=None):
    tools = CameraTools(
        ptz=None,
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: [],
        grab_frame=(lambda settings, stream: (grab or a_frame)()),
    )
    tab = SettingsTab(settings_path=tmp_path / "settings.json", tools=tools)
    qtbot.addWidget(tab)
    tab.load()
    tab.set_streams(list(_settings().camera.streams))
    return tab


def test_what_was_drawn_on_the_picture_is_what_the_form_holds(
    qtbot, tmp_path: Path
) -> None:
    tab = a_tab(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    dialog = tab.open_picker(row)
    assert dialog is not None
    qtbot.waitUntil(dialog.picker.has_frame, timeout=5000)

    sized(dialog.picker, 320, 180)
    qtbot.mouseClick(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(100, 45))
    qtbot.mousePress(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
    qtbot.mouseRelease(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(120, 70))
    dialog.accept()

    assert row.horizon() == 180, "a line drawn on the picture must reach the setting"
    assert row.horizon_enabled_field.isChecked() is True
    assert row.regions() == [(160, 80, 320, 200)]
    assert tab.save() is True
    from vmd.settings import load_settings

    stored = load_settings(tmp_path / "settings.json").camera.streams[0]
    assert stored.horizon_y == 180
    assert [r.as_tuple() for r in stored.ignore_regions] == [(160, 80, 320, 200)]


def test_cancelling_the_picture_changes_nothing_in_the_form(qtbot, tmp_path: Path) -> None:
    tab = a_tab(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    row.set_horizon(340)
    row.set_regions([(1, 2, 3, 4)])
    dialog = tab.open_picker(row)
    qtbot.waitUntil(dialog.picker.has_frame, timeout=5000)
    sized(dialog.picker, 320, 180)
    qtbot.mouseClick(dialog.picker, Qt.MouseButton.LeftButton, pos=QPoint(100, 45))
    dialog.reject()

    assert row.horizon() == 340
    assert row.regions() == [(1, 2, 3, 4)]


def test_a_camera_that_is_down_never_blocks_the_boxes_that_already_work(
    qtbot, tmp_path: Path
) -> None:
    """The camera being off must not stop anybody configuring the machine."""

    def refuse():
        raise FrameUnavailable("Nothing answered at that address.")

    tab = a_tab(qtbot, tmp_path, grab=refuse)
    row = tab.stream_rows()[0]
    dialog = tab.open_picker(row)
    qtbot.waitUntil(lambda: bool(dialog.problem_text()), timeout=5000)
    dialog.reject()

    # The numbers are still there to be typed, and they still save.
    row.horizon_enabled_field.setChecked(True)
    row.horizon_field.setValue(340)
    row.region_x.setValue(1)
    row.region_y.setValue(2)
    row.region_w.setValue(3)
    row.region_h.setValue(4)
    row.add_region()
    assert tab.save() is True
    from vmd.settings import load_settings

    stored = load_settings(tmp_path / "settings.json").camera.streams[0]
    assert stored.horizon_y == 340
    assert [r.as_tuple() for r in stored.ignore_regions] == [(1, 2, 3, 4)]


def test_a_stream_with_no_name_is_told_rather_than_asked_of_the_camera(
    qtbot, tmp_path: Path
) -> None:
    tab = a_tab(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    row.name_field.setText("")
    assert tab.open_picker(row) is None
    assert tab.message


def test_the_button_that_opens_the_picture_is_on_the_row(qtbot, tmp_path: Path) -> None:
    tab = a_tab(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    assert row.pick_button.text().strip()
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor")
    words = (row.pick_button.text() + row.pick_button.toolTip()).lower()
    assert not any(word in words for word in banned), words


# ------------------------------------------------------------- the grab itself


def test_a_stream_with_no_address_is_refused_before_any_camera_is_asked() -> None:
    from vmd.desktop.picker import grab_frame

    settings = Settings(camera=CameraSettings(host="", streams=[]))
    with pytest.raises(FrameUnavailable) as caught:
        grab_frame(settings, "thermal")
    assert "address" in str(caught.value).lower()


def test_what_the_camera_said_goes_to_the_log_and_not_to_the_operator(
    monkeypatch, caplog
) -> None:
    """ffmpeg's own words are "[tcp @ 000001bf] ... Error number -138 occurred".
    That is the whole story for whoever reads the Logs tab and nothing at all
    for the operator, who needs a sentence and a next thing to press."""
    import subprocess as real_subprocess

    from vmd.desktop import picker

    settings = _settings()
    monkeypatch.setattr(picker, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        picker.subprocess,
        "run",
        lambda command, **kwargs: real_subprocess.CompletedProcess(
            command, 1, "", "[tcp @ 000001bf] Connection failed: Error number -138 occurred"
        ),
    )
    with caplog.at_level("INFO"):
        with pytest.raises(FrameUnavailable) as caught:
            picker.grab_frame(settings, "thermal")

    said = str(caught.value)
    assert "did not send a picture" in said
    assert "[tcp @" not in said and "-138" not in said, said
    assert "Test the camera" in said, "a dead end is not an answer"
    assert "-138" in caplog.text, "the one place the real reason belongs"


def test_the_grab_never_puts_the_password_in_what_it_says(monkeypatch) -> None:
    """This message goes on screen. The address it works from carries the
    password percent-encoded into it, and ffmpeg quotes that address back in its
    own errors."""
    from urllib.parse import quote

    from vmd.desktop import picker

    settings = _settings()
    settings.camera.username = "admin"
    settings.camera.password = "p@ss/word"

    def failing_run(command, **kwargs):
        raise picker.subprocess.TimeoutExpired(command, 5)

    monkeypatch.setattr(picker.subprocess, "run", failing_run)
    monkeypatch.setattr(picker, "_ffmpeg", lambda: "ffmpeg")
    with pytest.raises(FrameUnavailable) as caught:
        picker.grab_frame(settings, "thermal")
    said = str(caught.value)
    assert settings.camera.password not in said
    assert quote(settings.camera.password, safe="") not in said


# ------------------------------------------------------- a picture of nothing
#
# The fault as it reached the operator: thermal video playing in the Live tab,
# PTZ working, and the picker still a black rectangle. Taking the FIRST decoded
# frame off a live RTSP stream gets whatever the decoder had before it had a
# whole keyframe, ffmpeg exits 0, and a valid JPEG of nothing passed every guard
# in the fetch. A black rectangle presented as a photograph is worse than the
# refusal, because there is nothing to act on - and a sky line dragged onto it
# is saved as a real setting that quietly throws away everything above it.


def flat(level: int, width: int = 320, height: int = 240) -> QImage:
    """A rectangle of exactly one colour: a half-decoded frame."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(level, level, level))
    return image


def flat_thermal(width: int = 640, height: int = 512) -> QImage:
    """A real thermal frame of a cold, flat scene at 700 m.

    This is the picture the guard must NOT refuse: almost no contrast, a slow
    gradient across the frame, and the fixed-pattern noise a heat camera puts on
    every pixel. Refusing it would take the picture away from the one view that
    most needs a sky line drawn on it.
    """
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        base = 78 + (y * 6) // max(height - 1, 1)  # six levels top to bottom
        for x in range(0, width, 4):
            shade = base + ((x * 7 + y * 3) % 5) - 2  # a couple of levels of noise
            for step in range(4):
                if x + step < width:
                    image.setPixel(x + step, y, QColor(shade, shade, shade).rgb())
    return image


def test_a_frame_of_one_colour_is_not_a_picture() -> None:
    from vmd.desktop.picker import is_blank

    assert is_blank(flat(0)), "a black rectangle is not a picture"
    assert is_blank(flat(128)), "nor is a grey one"
    assert is_blank(flat(255))


def test_a_genuinely_flat_thermal_frame_is_still_a_picture() -> None:
    """The threshold's whole reason for being where it is. A heat camera looking
    at a cold perimeter is low-contrast on purpose, and refusing a real thermal
    frame would be far worse than showing a dim one."""
    from vmd.desktop.picker import blankness, is_blank

    frame = flat_thermal()
    assert not is_blank(frame), (
        f"a real flat thermal frame was refused; it measures {blankness(frame):.2f}"
    )


def test_a_blank_picture_is_refused_in_words_rather_than_shown(qtbot) -> None:
    """And nothing the operator already had is touched."""
    from PySide6.QtCore import QBuffer, QByteArray

    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    flat(0, 640, 512).save(buffer, "PNG")
    buffer.close()
    black = bytes(store)

    dialog = a_dialog(qtbot, grab=lambda: black, horizon=340, regions=[(1, 2, 3, 4)])
    qtbot.waitUntil(lambda: bool(dialog.problem_text()), timeout=5000)

    assert "blank" in dialog.problem_text().lower()
    assert dialog.picker.has_frame() is False, "a blank frame may not be drawn on"
    assert dialog.horizon() == 340, "nothing the operator had may change"
    assert dialog.regions() == [(1, 2, 3, 4)]


def test_the_fetch_decodes_past_the_first_frame(monkeypatch, tmp_path: Path) -> None:
    """`-frames:v 1` is the bug itself, in the one place it can be pinned down
    without a camera: what the console asks ffmpeg for."""
    from vmd.desktop import picker as picker_module

    asked: list[list[str]] = []

    class Ran:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        asked.append(list(command))
        Path(command[command.index("-y") + 1]).write_bytes(b"not empty")
        return Ran()

    monkeypatch.setattr(picker_module, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(picker_module.subprocess, "run", fake_run)

    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/thermal")],
        )
    )
    picker_module.grab_frame(settings, "thermal")

    command = asked[0]
    frames = int(command[command.index("-frames:v") + 1])
    assert frames > 1, "one frame off a live stream is routinely black"
    # And each one written over the last, so what survives is the final frame
    # rather than a numbered sequence.
    assert "-update" in command


def test_what_ffmpeg_said_is_written_down_even_when_it_worked(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """A grab that "succeeded" into a black frame left no trace anywhere, which
    is why that fault took several rounds to find."""
    import logging

    from vmd.desktop import picker as picker_module

    class Ran:
        returncode = 0
        stdout = ""
        stderr = "[rtsp @ 0000] max delay reached. need to consume packet"

    def fake_run(command, **kwargs):
        Path(command[command.index("-y") + 1]).write_bytes(b"not empty")
        return Ran()

    monkeypatch.setattr(picker_module, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(picker_module.subprocess, "run", fake_run)

    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/thermal")],
        )
    )
    with caplog.at_level(logging.INFO, logger="vmd.desktop.picker"):
        picker_module.grab_frame(settings, "thermal")

    assert any("max delay reached" in record.message for record in caplog.records)
