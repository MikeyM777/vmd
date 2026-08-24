"""The settings form: what it loads, what it saves, and what it refuses."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
)

from vmd.desktop.settings_tab import PROBE_NAME, PTZ_SPEED_CHOICES, SettingsTab
from vmd.settings import (
    CameraSettings,
    IgnoreRegion,
    Settings,
    StreamSettings,
    load_settings,
    save_settings,
)


def a_drive(total_gb: float, free_gb: float):
    """A drive of a stated size, in the shape shutil.disk_usage answers in.

    Every tab in this file is given one. The form measures the real drive
    otherwise, and the form now refuses a size bigger than the drive - so a test
    that types 250 GB would pass on the machine it was written on and fail on a
    smaller one. A test whose answer depends on the laptop it runs on is not a
    test.
    """
    from types import SimpleNamespace

    total = int(total_gb * 1024**3)
    free = int(free_gb * 1024**3)
    return lambda path: SimpleNamespace(total=total, used=total - free, free=free)


def build(qtbot, tmp_path: Path, settings: Settings | None = None, drive=None):
    path = tmp_path / "settings.json"
    if settings is not None:
        save_settings(settings, path)
    tab = SettingsTab(settings_path=path)
    qtbot.addWidget(tab)
    # Before `load`, which is what measures the drive.
    tab.disk_usage = drive or a_drive(total_gb=1000, free_gb=500)
    tab.load()
    return tab, path


def test_a_first_run_loads_defaults_without_a_file(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    assert not path.exists()
    assert tab.camera_host == ""


def test_what_was_typed_is_what_is_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "192.168.1.250"
    tab.camera_username = "admin"
    tab.camera_password = "p@ss/word"
    tab.set_streams([("thermal", "rtsp://192.168.1.250:554/ch2", True, "auto")])
    assert tab.save() is True

    stored = load_settings(path)
    assert stored.camera.host == "192.168.1.250"
    assert stored.camera.password == "p@ss/word"
    assert stored.camera.streams[0].name == "thermal"


def test_show_only_the_pictures_round_trips_through_the_form(
    qtbot, tmp_path: Path
) -> None:
    """The checkbox reads and writes `stream_only` on the same path the boxes
    beside it use, in both directions.

    Turning it OFF is the direction that matters most here: it ships on, so the
    tick is how somebody gets the side column and the tab bar back, and a tick
    that did not save would strand him with no way to undo it.
    """
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/ch2", enabled=True)],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)
    assert tab.stream_only is True, "the form shows what the settings say"

    tab.stream_only = False
    assert tab.save() is True, tab.message
    assert load_settings(path).stream_only is False

    tab.stream_only = True
    assert tab.save() is True, tab.message
    assert load_settings(path).stream_only is True

    # And it comes back on the next load, from the file the save just wrote.
    again, _ = build(qtbot, tmp_path)
    assert again.stream_only is True


def test_recording_off_hides_the_folder_and_size_and_shows_them_when_on(
    qtbot, tmp_path: Path
) -> None:
    """"Why in the settings tab still storage appear, we said we getting rid of
    it." Recording is off, so the folder, the size and the age rule - which are
    only ever about where footage goes and how much to keep - are not on the
    screen. Ticking Record brings them back, because now they mean something;
    and it saves either way, without the folder blocking a live-only console."""
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/t", enabled=True)],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)

    assert tab.record is False, "off by default - this console is watched live"
    assert not tab._recording_details.isVisibleTo(tab), "no folder, no size, no age rule"
    # A live-only console saves without the storage folder standing in its way.
    assert tab.save() is True, tab.message
    assert load_settings(path).record is False

    # And Playback goes with it: watching footage back means nothing when none
    # is kept.
    assert not tab._playback_box.isVisibleTo(tab), "no Playback tab option either"

    tab.record = True
    assert tab._recording_details.isVisibleTo(tab), "the where-and-how-much is back"
    assert tab._playback_box.isVisibleTo(tab), "and Playback with it"

    # Clicking the tick, not just setting the property, does the same.
    tab.record = False
    assert not tab._recording_details.isVisibleTo(tab)
    assert not tab._playback_box.isVisibleTo(tab)
    tab._record.click()
    assert tab.record is True
    assert tab._recording_details.isVisibleTo(tab)
    assert tab._playback_box.isVisibleTo(tab)


def test_existing_streams_survive_a_load_and_save(qtbot, tmp_path: Path) -> None:
    """The browser form once deleted any stream it did not have a row for."""
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="IR-ch2", url="rtsp://10.0.0.2/ch2", enabled=True),
                StreamSettings(name="day", url="rtsp://10.0.0.2/ch0", enabled=False),
            ],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)
    assert tab.save() is True
    assert [s.name for s in load_settings(path).camera.streams] == ["IR-ch2", "day"]


def test_a_stream_ticked_to_record_with_no_address_is_refused(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "", True, "auto")])
    assert tab.save() is False
    assert "address" in tab.message.lower()
    assert not path.exists(), "a refused save must not write anything"


def test_two_streams_with_one_name_are_refused(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams(
        [("thermal", "rtsp://a/1", True, "auto"), ("thermal", "rtsp://a/2", True, "auto")]
    )
    assert tab.save() is False
    assert "thermal" in tab.message


def test_a_budget_the_model_rejects_is_reported_not_swallowed(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.budget_gb = "-5"
    assert tab.save() is False
    assert "budget" in tab.message.lower()


# --------------------------------------------------------------- the stream rows
#
# The tests above drive `set_streams`, which proves the saving and nothing about
# the form. These use the widgets an operator actually types into, because the
# bug that deleted people's streams lived exactly in the gap between the two.


def test_the_streams_on_screen_are_the_streams_from_the_file(qtbot, tmp_path: Path) -> None:
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="IR-ch2", url="rtsp://10.0.0.2/ch2", enabled=True),
                StreamSettings(name="day", url="rtsp://10.0.0.2/ch0", enabled=False, reader="ffmpeg"),
            ],
        )
    )
    tab, _ = build(qtbot, tmp_path, settings)
    # Both come back as used. There is no longer a switch for "use this view" -
    # a line on this list IS a view in use - so a file that had one switched off
    # is adopted rather than left in a state the operator cannot reach. The
    # reader, which is also off the screen now, is carried across untouched.
    assert tab.streams() == [
        ("IR-ch2", "rtsp://10.0.0.2/ch2", True, "auto"),
        ("day", "rtsp://10.0.0.2/ch0", True, "ffmpeg"),
    ]
    assert [row.name_field.text() for row in tab.stream_rows()] == ["IR-ch2", "day"]


def test_the_list_of_views_cannot_be_added_to_or_cut_down_from_this_form(
    qtbot, tmp_path: Path
) -> None:
    """**Add a stream** and **Remove** are both gone, and the second is the one
    that matters.

    He asked for Add to go: the camera is one gimbal with two heads and the
    views are a property of the hardware, so a button offering a third is a
    button offering a mistake. Remove had to go with it, and not out of
    symmetry - out of asymmetry. With Add gone, one stray click on Remove costs
    a camera view permanently, and the way back is hand-editing JSON on a
    machine with no terminal and no second computer.
    """
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    assert not hasattr(tab, "add_stream_button"), "Add is back"
    for row in tab.stream_rows():
        assert not hasattr(row, "remove_button"), "Remove is back"
    # And not merely renamed: nothing on this form adds or destroys a view.
    for button in tab.findChildren(QPushButton):
        said = button.text().lower()
        assert "add a stream" not in said, said
        assert said.strip() != "remove", said


def test_a_new_installation_gets_cards_to_type_the_views_into(
    qtbot, tmp_path: Path
) -> None:
    """The other half of locking the list, and it was missing.

    `scripts\\cameras.ps1` writes a settings file holding a title and a camera
    address, with `streams: []`, and a single-camera install has no file at all
    until the first Save. With **Add a stream** gone, both of those drew a form
    with no cards on it: the operator could type the camera's address and there
    was no box anywhere on the tab for a stream address, and no button to make
    one. Reported from the offline machine as "I can set the camera IP and I
    can't see the streams".
    """
    tab, _ = build(qtbot, tmp_path)
    rows = tab.stream_rows()
    assert len(rows) == 2, "a new installation has nowhere to type a view"
    assert all(row.is_blank() for row in rows)
    assert all(row.url_field.placeholderText() for row in rows)


def test_one_view_filled_in_and_one_card_left_empty_saves_one_view(
    qtbot, tmp_path: Path
) -> None:
    """An empty card costs nothing: not a refusal, and not a line in the file.

    Without this, drawing two cards on a console that watches one view would
    make that console impossible to save at all - `_problem` would report the
    empty card as a view with no address, for ever, with no Remove to press.
    """
    tab, path = build(qtbot, tmp_path)
    first, second = tab.stream_rows()
    first.name_field.setText("thermal")
    first.url_field.setText("rtsp://10.0.0.2/ch2")

    assert tab.save() is True, tab.message
    assert [s.name for s in load_settings(path).camera.streams] == ["thermal"]
    assert second.is_blank()


def test_a_file_with_one_view_still_offers_a_card_for_the_second(
    qtbot, tmp_path: Path
) -> None:
    """The second head, on the day it is set up.

    A form that drew exactly what the file held would let a console that had
    been saved with one view grow a second one only by hand-editing JSON, which
    is the thing this form exists to avoid.
    """
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/ch2")],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)
    rows = tab.stream_rows()
    assert len(rows) == 2
    assert rows[1].is_blank()

    # And saving without touching it leaves the file with the one view it had.
    assert tab.save() is True, tab.message
    assert [s.name for s in load_settings(path).camera.streams] == ["thermal"]

    rows[1].name_field.setText("day")
    rows[1].url_field.setText("rtsp://10.0.0.2/ch0")
    assert tab.save() is True, tab.message
    assert [s.name for s in load_settings(path).camera.streams] == ["thermal", "day"]


def test_a_half_typed_card_is_still_refused(qtbot, tmp_path: Path) -> None:
    """Blank is ignored; half-filled is a mistake, and says which half."""
    tab, path = build(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    row.name_field.setText("thermal")
    assert tab.save() is False
    assert "address" in tab.message.lower()

    row.name_field.setText("")
    row.url_field.setText("rtsp://10.0.0.2/ch2")
    assert tab.save() is False
    assert "name" in tab.message.lower()
    assert not path.exists()


def test_saving_does_not_move_the_operator_somewhere_else(
    qtbot, tmp_path: Path
) -> None:
    """"Always when saving, VMD jumps to the thermal name."

    This form lives in a QScrollArea, and a QScrollArea scrolls to whatever
    child takes the focus. So anything that moves the focus during a save drags
    the page with it, and the operator ends up in the first camera card's name
    box with a caret in it, several inches from what they were doing.

    The thief is modelled rather than named: what actually takes the focus on
    the real machine is the console handing the settings on - the streaming
    server restarting, the wall being rebuilt, real windows created and
    destroyed. Any handler of `saved` will do here, and the rule under test is
    the one that should hold whatever the thief turns out to be.
    """
    # 192.0.2.x rather than the usual fixture: showing this tab can start the
    # camera tools, and conftest refuses a socket to anything that is not a
    # documented test network. See the note at the top of tests\conftest.py.
    settings = Settings(
        camera=CameraSettings(
            host="192.0.2.10",
            streams=[
                StreamSettings(name="thermal", url="rtsp://192.0.2.10/ch2"),
                StreamSettings(name="visible", url="rtsp://192.0.2.10/ch0"),
            ],
        )
    )
    tab, _ = build(qtbot, tmp_path, settings)
    # The focus is parked on the budget field to prove a save does not steal it,
    # and the budget field is only on the screen when recording is on.
    tab.record = True
    tab.show()
    QApplication.processEvents()

    tab.saved.connect(lambda _settings: tab.stream_rows()[0].name_field.setFocus())

    tab._budget.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is tab._budget

    assert tab.save() is True, tab.message
    QApplication.processEvents()

    assert QApplication.focusWidget() is tab._budget, "the save moved the operator"


def test_a_settings_file_with_three_views_still_draws_and_saves_three(
    qtbot, tmp_path: Path
) -> None:
    """Locked is not fixed at two. `set_streams` is untouched, so the file's own
    list is what the form shows and what it writes back - which is the whole
    reason locking the list is safe rather than a second way to lose a camera.
    """
    tab, path = build(qtbot, tmp_path, _three_streams())
    assert len(tab.stream_rows()) == 3
    assert tab.save() is True, tab.message
    assert [s.name for s in load_settings(path).camera.streams] == ["one", "two", "three"]


def test_there_is_no_switch_for_whether_a_view_is_used(qtbot, tmp_path: Path) -> None:
    """`Use this view` is gone, and the operator's own words are the argument:
    "of course use that view, if it's added".

    It replaces the older test that policed what that tick was CALLED. Adding a
    camera view and then forgetting to tick a box beside it is a trap with no
    upside: the reward for getting it right is the state you were already in, and
    the punishment for missing it is a camera that is silently not watched. So
    the answer is not a better label, it is no control at all - a line on the
    list is a view in use, and the way to stop using one is to remove its line.
    """
    tab, path = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "rtsp://a/1", True, "auto")])
    row = tab.stream_rows()[0]
    assert not hasattr(row, "record_field"), "the tick is back"
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].enabled is True


def test_the_streams_box_says_that_a_line_on_it_is_a_view_in_use(
    qtbot, tmp_path: Path
) -> None:
    """On the form itself, not in a tooltip only. The sentence used to explain
    the tick; with the tick gone it has to explain what replaced it, which is
    the rule that a line here is a view that is used."""
    tab, _ = build(qtbot, tmp_path)
    said = tab.streams_help.text().lower()
    assert said.strip()
    assert "used" in said, said
    # And it must not send him to a button that is no longer on the form.
    assert "remove" not in said, said
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor")
    assert not any(word in said for word in banned), said


def test_a_view_switched_off_in_an_old_file_comes_back_on_and_the_form_says_so(
    qtbot, tmp_path: Path
) -> None:
    """The decision about `enabled: false` in a file written before this change.

    Leaving it off would be a setting with no control anywhere in the console -
    a camera view the operator can see on the form, cannot switch back on, and
    is given no reason for. So it is adopted: the row is a view in use, like
    every other row. Adopting it silently would be the mirror mistake, so the
    message line names the view and says what happens at the next Save.

    It used to end by pointing at **Remove**, and that button is gone. A
    sentence naming a control that is not on the screen sends him looking for
    it, which is worse than saying nothing.
    """
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="thermal", url="rtsp://a/1", enabled=True),
                StreamSettings(name="day", url="rtsp://a/2", enabled=False),
            ],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)

    assert "day" in tab.message, tab.message
    assert "thermal" not in tab.message, "only the one that was off is named"
    assert "remove" not in tab.message.lower(), tab.message
    assert "used again" in tab.message.lower(), tab.message

    assert tab.save() is True
    assert [s.enabled for s in load_settings(path).camera.streams] == [True, True]


def test_replacing_the_list_still_replaces_it(qtbot, tmp_path: Path) -> None:
    """The operator cannot cut a view out any more, but `load` still can - it
    empties the form and fills it from the file every time it runs, and that
    path goes straight through the row removal that used to be a button."""
    tab, path = build(qtbot, tmp_path)
    tab.set_streams(
        [("thermal", "rtsp://a/1", True, "auto"), ("day", "rtsp://a/2", True, "auto")]
    )
    assert [name for name, _, _, _ in tab.streams()] == ["thermal", "day"]

    tab.set_streams([("day", "rtsp://a/2", True, "auto")])
    assert [name for name, _, _, _ in tab.streams()] == ["day"]
    assert tab.save() is True
    assert [s.name for s in load_settings(path).camera.streams] == ["day"]


def test_a_stream_with_an_address_and_no_name_is_refused(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams([("", "rtsp://a/1", False, "auto")])
    assert tab.save() is False
    assert "name" in tab.message.lower()
    assert not path.exists()


# ------------------------------------------------------------ the other fields


def test_the_rest_of_the_form_is_saved_too(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.storage_root = "D:/footage"
    tab.budget_gb = "250"
    tab.retention_days = "14"
    tab.radio_host = "192.168.1.20"
    tab.radio_username = "ubnt"
    tab.radio_password = "ubnt"
    assert tab.save() is True

    stored = load_settings(path)
    assert stored.storage.root == Path("D:/footage")
    assert stored.storage.budget_gb == 250.0
    assert stored.storage.retention_days == 14
    assert stored.radio.host == "192.168.1.20"
    assert stored.radio.password == "ubnt"
    assert stored.radio.enabled is True


def test_an_empty_retention_means_no_age_rule_rather_than_an_error(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.retention_days = ""
    assert tab.save() is True
    assert load_settings(path).storage.retention_days is None


def test_settings_this_form_does_not_show_are_not_lost(qtbot, tmp_path: Path) -> None:
    """Saving the form must not reset the link ceiling to its default. That is
    the same failure as deleting a stream, one field further along."""
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(
                    name="IR-ch2",
                    url="rtsp://10.0.0.2/ch2",
                    detect=True,
                    thermal=True,
                    classify=False,
                    sensitivity="high",
                    horizon_y=340,
                    ignore_regions=[IgnoreRegion(x=10, y=20, w=30, h=40)],
                ),
                StreamSettings(name="day", url="rtsp://10.0.0.2/ch0"),
            ],
        )
    )
    settings.bitrate.ceiling_kbps = 4200
    settings.target_distance_m = 1200.0
    settings.storage.segment_seconds = 120

    tab, path = build(qtbot, tmp_path, settings)
    assert tab.save() is True

    stored = load_settings(path)
    assert stored.bitrate.ceiling_kbps == 4200
    assert stored.target_distance_m == 1200.0
    assert stored.storage.segment_seconds == 120

    # The rows were not touched, so every detection choice on them survives too.
    thermal, day = stored.camera.streams
    assert (thermal.detect, thermal.thermal, thermal.classify) == (True, True, False)
    assert thermal.sensitivity == "high"
    assert thermal.horizon_y == 340
    assert [r.as_tuple() for r in thermal.ignore_regions] == [(10, 20, 30, 40)]
    assert (day.detect, day.thermal, day.classify, day.horizon_y) == (False, False, None, None)


def test_credentials_are_shown_never_masked(qtbot, tmp_path: Path) -> None:
    """DESIGN.md: the failure this form suffers is a typo nobody can see, not a
    shoulder-surfer. If someone "fixes" this, this test is the argument."""
    tab, _ = build(qtbot, tmp_path)
    for field in tab.credential_fields():
        assert field.echoMode() == QLineEdit.EchoMode.Normal


def test_a_saved_form_says_so(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    assert tab.save() is True
    assert tab.message


def test_the_save_button_saves(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "10.0.0.9"
    qtbot.mouseClick(tab.save_button, Qt.MouseButton.LeftButton)
    assert load_settings(path).camera.host == "10.0.0.9"


# ------------------------------------------------------------------- detection
#
# Everything below exists because these settings shipped with no control at all:
# the only way to turn detection on was to hand-edit settings.json, which nobody
# is ever going to do. A setting with no control is a setting that does not
# exist.


def _watched(name: str = "IR-ch2", **kwargs) -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name=name, url="rtsp://10.0.0.2/ch2", **kwargs)],
        )
    )


def _two_watched_views() -> Settings:
    """The camera as it really is: one gimbal, two heads, both watched.

    Every duplication on this tab is invisible with one card on screen, because
    a paragraph printed once per view is printed once. It is the second view
    that makes it a defect, and the operator has always had two.
    """
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="thermal", url="rtsp://10.0.0.2/ch2", detect=True),
                StreamSettings(name="visible", url="rtsp://10.0.0.2/ch0", detect=True),
            ],
        )
    )


def _said_once(tab, said: str) -> None:
    """Assert a paragraph is on the form exactly once, wherever it lives.

    Not "is it on the card" but "how many times is it drawn": a sentence moved
    above the cards and left on them as well would pass every other assertion
    here and be the same defect it was before.
    """
    copies = [
        label
        for label in tab.findChildren(QLabel)
        if label.text().strip().lower() == said.strip()
    ]
    assert len(copies) == 1, f"the same paragraph is on the form {len(copies)} times"


def test_the_detection_choices_on_screen_are_the_ones_from_the_file(
    qtbot, tmp_path: Path
) -> None:
    settings = _watched(detect=True, sensitivity="high")
    tab, _ = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]

    assert row.detect_field.isChecked() is True
    assert row.sensitivity() == "high"


def test_ticking_the_watch_switch_is_what_reaches_the_file(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    row.detect_field.setChecked(True)
    row.set_sensitivity("low")
    assert tab.save() is True

    stored = load_settings(path).camera.streams[0]
    assert stored.detect is True
    assert stored.sensitivity == "low"


def test_every_sensitivity_offered_is_one_the_model_accepts(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    offered = [row.sensitivity_field.itemData(i) for i in range(row.sensitivity_field.count())]
    assert offered == ["low", "normal", "high"]
    for choice in offered:
        row.set_sensitivity(choice)
        assert tab.save() is True
        assert load_settings(path).camera.streams[0].sensitivity == choice


# --- naming what moved, which is gone ----------------------------------------
#
# "I need movement notifications, but not accurate identification." Two controls
# used to feed it - a chooser on each camera card and a master tick in the
# movement box - and both are off the form. `vmd/detect/config.py` is where it is
# actually switched off; this is only the half of it he can see.


def test_nothing_on_this_tab_offers_to_name_what_moved(qtbot, tmp_path: Path) -> None:
    """Not a control renamed, not a control folded away: no control.

    Both cards are unfolded here, and their Advanced fold opened too, because a
    chooser behind a fold is a chooser that comes back the first time he ticks
    a box.
    """
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    for row in tab.stream_rows():
        row.detect_field.setChecked(True)
        row.advanced_button.setChecked(True)
        assert not hasattr(row, "classify_field"), "the chooser is back on the card"
    assert not hasattr(tab, "_detection_classify"), "the master tick is back"
    assert not hasattr(SettingsTab, "detection_classify"), "the master tick is back"

    words = " ".join(
        [label.text() for label in tab.findChildren(QLabel)]
        + [button.text() for button in tab.findChildren(QPushButton)]
        + [box.text() for box in tab.findChildren(QCheckBox)]
    ).lower()
    for said in ("say what it was", "name what moved", "identif", "what it was"):
        assert said not in words, said


def test_a_file_that_asked_for_naming_keeps_its_answer_and_gets_nothing(
    qtbot, tmp_path: Path
) -> None:
    """Two halves, and both matter.

    The fields stay in the file: every settings file in the field has them, and
    a form that silently rewrote them would be doing the thing this whole file
    exists to prevent. And they buy nothing: `classify_enabled` returns False
    whatever they say, so this is not a running feature with its switch hidden.
    """
    from vmd.detect.config import classify_enabled

    settings = _watched(detect=True, classify=True)
    settings.detection.classify = True
    tab, path = build(qtbot, tmp_path, settings)
    assert tab.save() is True, tab.message

    stored = load_settings(path)
    assert stored.detection.classify is True, "a setting was rewritten behind him"
    assert stored.camera.streams[0].classify is True
    assert classify_enabled(stored.camera.streams[0], stored.detection) is False


# --- the heat camera tick, which is gone -------------------------------------


def test_the_heat_camera_tick_is_off_the_form_but_not_out_of_the_file(
    qtbot, tmp_path: Path
) -> None:
    """`stream.thermal` had exactly one consumer in the whole codebase - the
    line deciding whether naming ran. With naming gone it decides nothing, so
    the tick asked a question with no consequence anywhere.

    Off the form, not out of the file: a stream marked thermal is still marked
    thermal after a load and a save.
    """
    tab, path = build(qtbot, tmp_path, _watched(detect=True, thermal=True))
    row = tab.stream_rows()[0]
    assert not hasattr(row, "thermal_field"), "the tick is back"
    for box in tab.findChildren(QCheckBox):
        assert "heat" not in box.text().lower(), box.text()
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].thermal is True


# --- the sky line, which is gone from the form -------------------------------


def test_the_sky_line_is_off_the_form_but_not_out_of_the_file(
    qtbot, tmp_path: Path
) -> None:
    """It was a number of dots counted down from the top edge of a frame he was
    not looking at, and setting it too low deletes real movement below it and
    never says it did. A skyline is a shape across the top of a picture, and
    there is a tool for shapes now - one tool is better than two.

    `horizon_y` stays in the model and is carried across a save untouched.
    """
    tab, path = build(qtbot, tmp_path, _watched(detect=True, horizon_y=340))
    row = tab.stream_rows()[0]
    assert not hasattr(row, "horizon_field"), "the box is back"
    assert not hasattr(row, "horizon_enabled_field"), "the tick is back"
    row.detect_field.setChecked(True)
    row.advanced_button.setChecked(True)
    for box in tab.findChildren(QCheckBox):
        assert "sky" not in box.text().lower(), box.text()

    assert tab.save() is True
    assert load_settings(path).camera.streams[0].horizon_y == 340


# --- the parts of the picture to ignore --------------------------------------
#
# `120 x 80 dots, at 30 across and 40 down`, in a list, beside four spin boxes
# labelled across, down, wide and tall. Exactly what he asked to be rid of, and
# a sentence nobody can check against a picture they are not looking at.


def test_the_areas_are_never_shown_as_words_or_numbers(qtbot, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QListWidget, QSpinBox

    tab, _ = build(qtbot, tmp_path, _watched(detect=True))
    row = tab.stream_rows()[0]
    row.set_shapes([[(30, 40), (150, 40), (150, 120), (30, 120)]])
    row.detect_field.setChecked(True)
    row.advanced_button.setChecked(True)

    for gone in ("regions_list", "region_x", "region_y", "region_w", "region_h",
                 "add_region_button", "remove_region_button", "pick_button"):
        assert not hasattr(row, gone), gone
    assert tab.findChildren(QListWidget) == []
    assert tab.findChildren(QSpinBox) == []

    words = " ".join(
        [label.text() for label in tab.findChildren(QLabel)]
        + [button.text() for button in tab.findChildren(QPushButton)]
    ).lower()
    for number in ("30", "40", "150", "120"):
        assert number not in words, words
    for jargon in ("dots", "across and", " wide", " tall"):
        assert jargon not in words, words


def test_the_areas_in_the_file_are_loaded_and_the_button_says_how_many(
    qtbot, tmp_path: Path
) -> None:
    """The count and nothing else. It answers the one question anybody has
    before pressing the button - is there anything in there already - without
    printing a coordinate he could not have checked."""
    from vmd.settings import IgnoreShape

    settings = _watched(
        detect=True,
        ignore_shapes=[
            IgnoreShape(points=[(1, 2), (30, 2), (30, 40)]),
            IgnoreShape(points=[(5, 6), (7, 6), (7, 9), (5, 9)]),
        ],
    )
    tab, _ = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]
    assert row.shapes() == [
        [(1, 2), (30, 2), (30, 40)],
        [(5, 6), (7, 6), (7, 9), (5, 9)],
    ]
    assert "2" in row.mask_button.text(), row.mask_button.text()
    assert "Parts to ignore" in row.mask_button.text()

    row.set_shapes([])
    assert row.mask_button.text() == "Parts to ignore"


def test_an_area_drawn_on_the_picture_is_what_is_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched(detect=True))
    row = tab.stream_rows()[0]
    row.set_shapes([[(10, 20), (110, 20), (110, 90), (10, 90)]])
    assert tab.save() is True, tab.message

    stored = load_settings(path).camera.streams[0].ignore_shapes
    assert [shape.as_tuples() for shape in stored] == [
        [(10, 20), (110, 20), (110, 90), (10, 90)]
    ]


def test_the_older_rectangles_are_carried_across_untouched(qtbot, tmp_path: Path) -> None:
    """Nothing on this form shows them any more, and nothing on it rewrites
    them either. A form that quietly converted a setting the operator never
    touched is the same failure as one that quietly deletes it - and the
    detector still honours them."""
    settings = _watched(
        detect=True, ignore_regions=[IgnoreRegion(x=1, y=2, w=3, h=4)]
    )
    tab, path = build(qtbot, tmp_path, settings)
    tab.stream_rows()[0].set_shapes([[(9, 9), (99, 9), (99, 99)]])
    assert tab.save() is True

    stored = load_settings(path).camera.streams[0]
    assert [r.as_tuple() for r in stored.ignore_regions] == [(1, 2, 3, 4)]
    assert [sh.as_tuples() for sh in stored.ignore_shapes] == [[(9, 9), (99, 9), (99, 99)]]


# --- and the tool the button opens -------------------------------------------


class _FakeMask:
    """A stand-in for `MaskDialog`, which is modal and would stop a test dead.

    It records what it was handed, because that is half of what is being
    tested: the tab has to give the drawing tool a picture and the areas that
    are already on this view, not an empty list it will then overwrite.
    """

    opened: list = []

    def __init__(self, frame, shapes, problem="", parent=None) -> None:
        _FakeMask.opened.append(
            {"frame": frame, "shapes": shapes, "problem": problem}
        )
        self._shapes = list(shapes)
        self.accepted = True
        # The size of the picture he drew on. `(0, 0)` when there was none,
        # which is what the real dialog answers then.
        self.size = (1920, 1080)
        # What the operator draws while it is open, which is the only moment he
        # can: `exec` does not come back until the dialog is closed.
        self.draws: list = []

    def exec(self) -> bool:
        self._shapes.extend(self.draws)
        return self.accepted

    def shapes(self):
        return self._shapes

    def frame_size(self):
        return self.size


def _with_a_fake_mask(monkeypatch, accepted: bool = True, draws=(), size=(1920, 1080)):
    import vmd.desktop.mask as mask

    _FakeMask.opened = []
    made: list = []

    def build(frame, shapes, problem="", parent=None):
        dialog = _FakeMask(frame, shapes, problem, parent)
        dialog.accepted = accepted
        dialog.draws = [list(shape) for shape in draws]
        dialog.size = size
        made.append(dialog)
        return dialog

    monkeypatch.setattr(mask, "MaskDialog", build)
    return made


def _tab_with_a_camera(qtbot, tmp_path: Path, grab=None):
    from vmd.desktop.settings_tab import CameraTools

    tools = CameraTools(
        ptz=None,
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: [],
        grab_frame=(grab or (lambda settings, stream: b"a-picture")),
    )
    path = tmp_path / "settings.json"
    save_settings(_watched(name="thermal", detect=True), path)
    tab = SettingsTab(settings_path=path, tools=tools)
    qtbot.addWidget(tab)
    tab.load()
    return tab


def test_the_button_opens_the_drawing_tool_with_this_view_s_picture(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    made = _with_a_fake_mask(monkeypatch)
    tab = _tab_with_a_camera(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    row.set_shapes([[(1, 2), (3, 4), (5, 6)]])

    tab.open_picker(row)

    assert len(made) == 1
    handed = _FakeMask.opened[0]
    assert handed["frame"] == b"a-picture", "it opened with no picture in it"
    assert handed["shapes"] == [[(1, 2), (3, 4), (5, 6)]], handed["shapes"]
    assert handed["problem"] == ""


def test_what_was_drawn_on_the_picture_is_what_the_form_holds(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    _with_a_fake_mask(monkeypatch, draws=[[(10, 20), (110, 20), (110, 90)]])
    tab = _tab_with_a_camera(qtbot, tmp_path)
    row = tab.stream_rows()[0]

    tab.open_picker(row)
    assert row.shapes() == [[(10, 20), (110, 20), (110, 90)]]
    # And the button beside it counts what is now behind it.
    assert "1" in row.mask_button.text(), row.mask_button.text()

    assert tab.save() is True, tab.message
    stored = load_settings(tmp_path / "settings.json").camera.streams[0]
    assert [sh.as_tuples() for sh in stored.ignore_shapes] == [
        [(10, 20), (110, 20), (110, 90)]
    ]


def test_the_size_of_the_picture_he_drew_on_is_saved_with_the_area(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """The points are pixels with no scale of their own, and the stream's size
    is not fixed: it is a setting on the camera that this console has a button
    for, two boxes further down the same page.

    Trace a band over the treeline on a 1920x1080 still, drop the camera to
    1280x720, and every point lands a third too far right and a third too low.
    Every one of them is still inside the frame, so nothing is clipped and
    nothing complains - the mask covers sky and the treeline is watched again.
    The first anybody knows is a night of alarms nobody can explain.
    """
    _with_a_fake_mask(
        monkeypatch, draws=[[(10, 20), (110, 20), (110, 90)]], size=(1920, 1080)
    )
    tab = _tab_with_a_camera(qtbot, tmp_path)
    row = tab.stream_rows()[0]

    tab.open_picker(row)
    assert row.frame_sizes() == [(1920, 1080)]
    assert tab.save() is True, tab.message

    stored = load_settings(tmp_path / "settings.json").camera.streams[0].ignore_shapes
    assert [(sh.frame_w, sh.frame_h) for sh in stored] == [(1920, 1080)]
    assert [sh.as_tuples() for sh in stored] == [[(10, 20), (110, 20), (110, 90)]]


def test_an_area_drawn_before_the_size_was_kept_is_left_exactly_as_it_is(
    qtbot, tmp_path: Path
) -> None:
    """Zero is the "not recorded" value. Those are used as they are, which is
    what they have always done - the guess cannot be improved on, and refusing
    them would silently unmask a treeline somebody drew last week."""
    from vmd.settings import IgnoreShape

    settings = _watched(
        detect=True, ignore_shapes=[IgnoreShape(points=[(1, 2), (30, 2), (30, 40)])]
    )
    tab, path = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]
    assert row.frame_sizes() == [(0, 0)]

    assert tab.save() is True
    stored = load_settings(path).camera.streams[0].ignore_shapes
    assert [(sh.frame_w, sh.frame_h) for sh in stored] == [(0, 0)]
    assert [sh.as_tuples() for sh in stored] == [[(1, 2), (30, 2), (30, 40)]]


def test_a_size_already_on_a_shape_survives_a_load_and_a_save(
    qtbot, tmp_path: Path
) -> None:
    from vmd.settings import IgnoreShape

    settings = _watched(
        detect=True,
        ignore_shapes=[
            IgnoreShape(points=[(1, 2), (30, 2), (30, 40)], frame_w=1280, frame_h=720)
        ],
    )
    tab, path = build(qtbot, tmp_path, settings)
    assert tab.stream_rows()[0].frame_sizes() == [(1280, 720)]
    assert tab.save() is True

    stored = load_settings(path).camera.streams[0].ignore_shapes
    assert [(sh.frame_w, sh.frame_h) for sh in stored] == [(1280, 720)]


def test_closing_the_drawing_tool_without_using_it_changes_nothing(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    _with_a_fake_mask(
        monkeypatch, accepted=False, draws=[[(9, 9), (99, 9), (99, 99)]]
    )
    tab = _tab_with_a_camera(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    row.set_shapes([[(1, 2), (3, 4), (5, 6)]])

    tab.open_picker(row)

    assert row.shapes() == [[(1, 2), (3, 4), (5, 6)]], "a cancelled draw was kept"


def test_a_camera_that_is_down_still_opens_the_tool_and_says_why(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """The camera being off must not take away the ability to undo a mistake
    made while it was working. Areas already drawn are still his to delete, so
    the tool opens - carrying the reason there is no picture behind them."""

    def refuse(settings, stream):
        raise RuntimeError("Nothing answered at that address.")

    _with_a_fake_mask(monkeypatch)
    tab = _tab_with_a_camera(qtbot, tmp_path, grab=refuse)
    row = tab.stream_rows()[0]
    row.set_shapes([[(1, 2), (3, 4), (5, 6)]])

    assert tab.open_picker(row) is not None, "a dead camera locked him out"
    handed = _FakeMask.opened[0]
    assert "Nothing answered" in handed["problem"], handed["problem"]
    assert handed["shapes"] == [[(1, 2), (3, 4), (5, 6)]]


def test_the_real_drawing_tool_hands_back_the_size_it_drew_at(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """The one test here that uses the real `MaskDialog` rather than a stand-in.

    Everything above pins what this tab does with the answer; this pins that the
    two halves still fit together - that the dialog takes the picture and the
    areas in the order they are handed over, and that `frame_size` really is the
    size of the picture rather than of the widget it was drawn in.

    `exec` is replaced and nothing is shown. This console runs on a laptop
    somebody is watching, and a modal that appears during a test run is a modal
    in front of the operator.
    """
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

    import vmd.desktop.mask as mask

    picture = QImage(1920, 1080, QImage.Format.Format_RGB32)
    painter = QPainter(picture)
    # A gradient, not a flat fill: a rectangle of one colour is what a
    # half-decoded first frame off a live stream looks like, and the dialog is
    # right to refuse one.
    sky = QLinearGradient(0, 0, 0, 1080)
    sky.setColorAt(0.0, QColor(20, 24, 32))
    sky.setColorAt(1.0, QColor(190, 195, 205))
    painter.fillRect(0, 0, 1920, 1080, sky)
    painter.end()
    # Held in a name: a QByteArray passed straight into QBuffer is a Python
    # temporary, and Qt keeps the pointer after Python has freed it.
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    picture.save(buffer, "PNG")

    built: list = []
    real = mask.MaskDialog

    def build(frame, shapes, problem="", parent=None):
        dialog = real(frame, shapes, problem=problem, parent=parent)
        qtbot.addWidget(dialog)
        dialog.exec = lambda: 1  # accepted, and never shown
        built.append(dialog)
        return dialog

    monkeypatch.setattr(mask, "MaskDialog", build)
    tab = _tab_with_a_camera(
        qtbot, tmp_path, grab=lambda settings, stream: bytes(store)
    )
    row = tab.stream_rows()[0]
    row.set_shapes([[(10, 20), (110, 20), (110, 90)]], 640, 360)

    tab.open_picker(row)

    assert len(built) == 1
    assert built[0].shapes() == [[(10, 20), (110, 20), (110, 90)]], "it lost them"
    # The picture's own size, not the widget's.
    assert row.frame_sizes() == [(1920, 1080)], row.frame_sizes()
    assert tab.save() is True, tab.message
    stored = load_settings(tmp_path / "settings.json").camera.streams[0].ignore_shapes
    assert [(sh.frame_w, sh.frame_h) for sh in stored] == [(1920, 1080)]


def test_a_stream_with_no_name_is_told_rather_than_asked_of_the_camera(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    _with_a_fake_mask(monkeypatch)
    tab = _tab_with_a_camera(qtbot, tmp_path)
    row = tab.stream_rows()[0]
    row.name_field.setText("")

    assert tab.open_picker(row) is None
    assert tab.message
    assert _FakeMask.opened == [], "it went to the camera anyway"


def test_the_ignore_control_says_what_it_is_for(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    told = tab.ignore_help.text().lower()
    assert "tree" in told, told
    assert "sky" in told, told


# --- the global block --------------------------------------------------------


def test_the_global_detection_switch_is_on_screen_and_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.detection_enabled = False
    assert tab.save() is True

    assert load_settings(path).detection.enabled is False


def test_the_global_detection_block_on_screen_is_the_one_from_the_file(
    qtbot, tmp_path: Path
) -> None:
    """A form that shows a switch as on while the file says off is worse than no
    switch: the operator turns detection off and it comes back at the next save."""
    settings = Settings()
    settings.detection.enabled = False
    tab, path = build(qtbot, tmp_path, settings)

    assert tab.detection_enabled is False
    assert tab.save() is True
    assert load_settings(path).detection.enabled is False


def test_the_box_asking_how_far_a_thing_must_travel_is_off_the_form(
    qtbot, tmp_path: Path
) -> None:
    """**Must travel at least (dots)** asked for a count of dots in the camera's
    own frame - a quantity nobody can see, estimate or check - and its own
    tooltip said "Leave it empty". Its placeholder sent him to "the touchiness
    setting", which is called **How touchy:** and is folded away inside a camera
    card until that view is being watched.

    A control whose help tells you not to use it, by pointing at a control that
    is not on the screen, is not a setting. It is a developer's escape hatch,
    and it was the first thing on the movement box he met on a console nobody
    had set up yet.

    Off the form, not out of the settings: see the two tests below.
    """
    tab, _ = build(qtbot, tmp_path, _watched(detect=True))
    tab.detection_enabled = True

    assert not hasattr(tab, "min_travel_px"), "the box is back on the form"
    for label in tab.findChildren(QLabel):
        assert "must travel" not in label.text().lower(), label.text()
    # And nothing left over pointing at it: no field on the form sends the
    # reader to a control that is not there.
    for field in tab.findChildren(QLineEdit):
        assert "touchiness" not in field.placeholderText().lower()


def test_a_number_the_form_no_longer_shows_still_survives_a_save(
    qtbot, tmp_path: Path
) -> None:
    """Deleting a control is not deleting a setting. Somebody who has a number
    in his file - and the detector, which still reads it - must not lose it
    because the form stopped asking.

    This is the same rule that keeps `reader` and the link ceiling alive across
    a save, and it is the rule that makes taking a control off the screen a safe
    thing to do at all.
    """
    settings = Settings()
    settings.detection.min_travel_px = 20.0
    tab, path = build(qtbot, tmp_path, settings)

    # A save that touches everything else on the movement box.
    tab.detection_enabled = False
    tab.alarm_sound = False
    assert tab.save() is True
    assert load_settings(path).detection.min_travel_px == 20.0

    # And a second one, from the settings this save left behind: a value that
    # survives one round trip and not two is a value that is lost tomorrow.
    assert tab.save() is True
    assert load_settings(path).detection.min_travel_px == 20.0


def test_a_minimum_travel_the_model_rejects_is_reported_not_swallowed(
    qtbot, tmp_path: Path
) -> None:
    """It can no longer be typed, so the only way a bad one arrives is in the
    file - and that is still refused in words rather than swallowed.

    The tab does not die of it: it is the only tool on this machine that can fix
    that file, so the boxes fill with the standard settings and the reason goes
    under them.
    """
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"detection": {"enabled": True, "min_travel_px": -3}}),
        encoding="utf-8",
    )
    tab = SettingsTab(settings_path=path)
    qtbot.addWidget(tab)
    tab.load()

    said = tab.message.lower()
    assert said.strip(), "a settings file the console refused, refused silently"
    assert "settings file" in said and "save" in said, said


# --- the rules that must not break -------------------------------------------


def test_a_settings_file_written_before_detection_existed_is_not_damaged(
    qtbot, tmp_path: Path
) -> None:
    """The machine in the field has one of these. It has no detection block and
    its streams have none of the detection keys."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "target_distance_m": 1200.0,
                "camera": {
                    "host": "10.0.0.2",
                    "username": "admin",
                    "password": "p@ss/word",
                    "streams": [
                        {
                            "name": "ch1",
                            "url": "rtsp://10.0.0.2/ch1",
                            "enabled": True,
                            "reader": "ffmpeg",
                        }
                    ],
                },
                "bitrate": {"ceiling_kbps": 4200},
            }
        ),
        encoding="utf-8",
    )
    tab = SettingsTab(settings_path=path)
    qtbot.addWidget(tab)
    tab.load()
    assert tab.save() is True

    stored = load_settings(path)
    assert stored.camera.host == "10.0.0.2"
    assert stored.camera.password == "p@ss/word"
    assert stored.target_distance_m == 1200.0
    assert stored.bitrate.ceiling_kbps == 4200
    stream = stored.camera.streams[0]
    assert (stream.name, stream.url, stream.enabled, stream.reader) == (
        "ch1",
        "rtsp://10.0.0.2/ch1",
        True,
        "ffmpeg",
    )
    assert stream.detect is False
    assert stream.thermal is False
    assert stream.classify is None
    assert stream.horizon_y is None
    assert stream.ignore_regions == []
    assert stream.sensitivity == "normal"


def _three_streams() -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(
                    name="one",
                    url="rtsp://a/1",
                    detect=True,
                    thermal=True,
                    classify=False,
                    sensitivity="low",
                    horizon_y=100,
                    ignore_regions=[IgnoreRegion(x=1, y=1, w=1, h=1)],
                ),
                StreamSettings(
                    name="two",
                    url="rtsp://a/2",
                    detect=True,
                    thermal=False,
                    classify=True,
                    sensitivity="high",
                    horizon_y=200,
                    ignore_regions=[IgnoreRegion(x=2, y=2, w=2, h=2)],
                ),
                StreamSettings(name="three", url="rtsp://a/3", sensitivity="normal"),
            ],
        )
    )


def _detection_of(path: Path) -> dict[str, tuple]:
    """Every per-view setting, whether the form still shows it or not.

    `thermal`, `classify`, `horizon_y` and the older rectangles are on this list
    on purpose. The form stopped showing them; carrying them is the rule that
    made taking them off the screen a safe thing to do at all, and a test that
    only checked the fields still drawn would not notice them being dropped.
    """
    return {
        s.name: (
            s.detect,
            s.thermal,
            s.classify,
            s.sensitivity,
            s.horizon_y,
            tuple(r.as_tuple() for r in s.ignore_regions),
            tuple(tuple(sh.as_tuples()) for sh in s.ignore_shapes),
        )
        for s in load_settings(path).camera.streams
    }


def test_replacing_the_list_does_not_move_detection_onto_another_stream(
    qtbot, tmp_path: Path
) -> None:
    """Attaching one view's settings to another head is this form's version of
    the bug that once deleted the operator's streams. It cannot be reached from
    a button any more, but `load` empties and refills the form every time."""
    tab, path = build(qtbot, tmp_path, _three_streams())
    before = _detection_of(path)
    kept = [s for s in load_settings(path).camera.streams if s.name != "one"]
    tab.set_streams(kept)
    assert tab.save() is True

    after = _detection_of(path)
    assert list(after) == ["two", "three"]
    for name in ("two", "three"):
        assert after[name] == before[name]


def test_a_view_added_to_the_file_does_not_disturb_the_ones_already_there(
    qtbot, tmp_path: Path
) -> None:
    tab, path = build(qtbot, tmp_path, _three_streams())
    before = _detection_of(path)
    grown = list(load_settings(path).camera.streams) + [
        StreamSettings(name="four", url="rtsp://a/4", thermal=True)
    ]
    tab.set_streams(grown)
    assert tab.save() is True

    after = _detection_of(path)
    assert list(after) == ["one", "two", "three", "four"]
    for name in ("one", "two", "three"):
        assert after[name] == before[name]
    assert after["four"] == (False, True, None, "normal", None, (), ())


def test_reordering_the_streams_carries_each_ones_detection_with_it(
    qtbot, tmp_path: Path
) -> None:
    tab, path = build(qtbot, tmp_path, _three_streams())
    before = _detection_of(path)
    tab.set_streams(list(reversed(load_settings(path).camera.streams)))
    assert tab.save() is True

    after = _detection_of(path)
    assert list(after) == ["three", "two", "one"]
    assert after == before


# ----------------------------------------------- the folder the footage goes in
#
# The single most likely mistake a non-technical operator can make during setup.
# Pointed at a drive letter with nothing behind it, the form said "Saved." with
# no validation at all, the Logs tab filled with a traceback through pathlib
# ending "FileNotFoundError: [WinError 3]", and the Playback tab was replaced by
# "The Playback tab could not be opened: [WinError 3] The system cannot find the
# path specified: 'Q:\\'".


def test_a_recordings_folder_on_a_drive_that_is_not_there_is_refused_in_words(
    qtbot, tmp_path
) -> None:
    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.record = True  # the folder is only checked when there is footage to put in it
    tab.storage_root = "Q:\\not-a-drive\\vmd"

    assert tab.save() is False
    assert "Q:" in tab.message
    assert tab.message != "Saved."
    assert "traceback" not in tab.message.lower()
    assert not (tmp_path / "settings.json").exists(), "a refused save writes nothing"


def test_a_recordings_folder_that_is_really_a_file_is_refused(qtbot, tmp_path) -> None:
    a_file = tmp_path / "notes.txt"
    a_file.write_text("hello", encoding="utf-8")
    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.record = True  # the folder is only checked when there is footage to put in it
    tab.storage_root = str(a_file)

    assert tab.save() is False
    assert "folder" in tab.message.lower()


def test_a_recordings_folder_that_does_not_exist_yet_is_made_rather_than_refused(
    qtbot, tmp_path
) -> None:
    """First run: the folder has never existed. The recorder would make it, so
    refusing here would refuse the ordinary case."""
    wanted = tmp_path / "footage" / "vmd"
    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.record = True  # the folder is only made and checked when recording
    tab.storage_root = str(wanted)

    assert tab.save() is True, tab.message
    assert tab.message == "Saved."
    assert wanted.is_dir()


def test_a_relative_recordings_folder_is_judged_beside_the_settings_file(
    qtbot, tmp_path
) -> None:
    """"recordings" is the default and it is relative. It is anchored to the
    settings file everywhere else, and must be here too or the check would test
    a folder beside whatever shell started the console."""
    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.record = True  # the folder is only made and checked when recording
    tab.storage_root = "recordings"

    assert tab.save() is True, tab.message
    assert (tmp_path / "recordings").is_dir()


def test_a_recordings_folder_that_cannot_be_written_to_is_refused(qtbot, tmp_path) -> None:
    """It exists and it is a folder, and footage still cannot go in it."""
    root = tmp_path / "readonly"
    root.mkdir()
    # Something already occupying the probe's name that cannot be written over.
    (root / PROBE_NAME).mkdir()

    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.record = True  # the folder is only checked when there is footage to put in it
    tab.storage_root = str(root)

    assert tab.save() is False
    assert "written" in tab.message.lower()
    assert str(root) in tab.message


# ------------------------------------------------------------ how it is fitted
#
# "The program isn't fitted right" was mostly this tab: a thirteen-character
# address field stretched across 1900 px of a 4K panel, with the label at one
# end of the screen and the box it belongs to at the other.


def test_the_form_stops_growing_however_wide_the_screen_is(qtbot, tmp_path: Path) -> None:
    from vmd.desktop.style import FORM_MAX_WIDTH

    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    tab.show()
    tab.setGeometry(0, 0, 3840, 2160)
    QApplication.processEvents()

    widest = max(
        field.width()
        for field in (tab._host, tab._username, tab._root, tab._radio_host)
    )
    assert widest <= FORM_MAX_WIDTH, (
        f"a field is {widest} px wide; the column is meant to stop at {FORM_MAX_WIDTH}"
    )


def test_nothing_on_a_stream_row_is_cut_in_half_inside_the_column(
    qtbot, tmp_path: Path
) -> None:
    """Every control on a stream row on one line was about 1500 px of controls
    in a column that stops growing, so the tick boxes lost their last word and
    the button read "e and ignored p". A control whose label is cut in half is a
    control nobody can act on - and this row carries the heat-camera flag, which
    quietly changes what gets reported."""
    from vmd.desktop.style import FORM_MAX_WIDTH

    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    tab.load()
    row = tab.add_stream_row("thermal", "rtsp://10.0.0.2/thermal")
    # The detection controls only exist once he has asked for them, and the
    # question here is whether they fit when they do.
    row.detect_field.setChecked(True)
    tab.show()
    tab.setGeometry(0, 0, FORM_MAX_WIDTH, 900)
    QApplication.processEvents()

    cut = [
        control.text()
        for control in (
            row.detect_field,
            row.mask_button,
            row.advanced_button,
        )
        if control.width() < control.minimumSizeHint().width()
    ]
    assert cut == [], f"cut off: {cut}"


def test_the_report_box_says_what_it_is_for_before_anything_has_used_it(
    qtbot, tmp_path: Path
) -> None:
    """An empty report box is a black rectangle, and a black rectangle is not an
    answer to "has anything happened?"."""
    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    assert tab.output_text() == ""
    assert tab._output.placeholderText(), "an empty box has to say what it is for"


def test_no_button_on_a_stream_row_is_clipped_by_its_own_label(
    qtbot, tmp_path: Path
) -> None:
    """`Delete the selected patch` rendered as `elete the selected patc`.

    The form column stops at FORM_MAX_WIDTH, so a wider screen does not fix it,
    and Qt clips both ends rather than eliding one - which is how a button ends
    up with no first letter and no last. The comment above the row of switches
    describes fixing exactly this failure one row up; it did not reach this row.
    """
    from vmd.desktop.style import FORM_MAX_WIDTH

    from vmd.desktop.style import stylesheet

    tab = SettingsTab(settings_path=tmp_path / "settings.json")
    qtbot.addWidget(tab)
    # The console's own stylesheet, because that is where the padding around a
    # button's label comes from: without it the measurement is of a button
    # nobody ever sees.
    tab.setStyleSheet(stylesheet())
    tab.load()
    row = tab.add_stream_row("thermal", "rtsp://10.0.0.2/thermal")
    row.detect_field.setChecked(True)
    row.advanced_button.setChecked(True)
    tab.show()
    tab.setGeometry(0, 0, FORM_MAX_WIDTH, 1400)
    QApplication.processEvents()

    cut = [
        (control.text(), control.width(), control.minimumSizeHint().width())
        for control in (
            row.mask_button,
            row.advanced_button,
            row.sensitivity_field,
        )
        if control.width() < control.minimumSizeHint().width()
    ]
    assert cut == [], f"cut off: {cut}"


# ------------------------------------------------ lowering the disk budget
#
# The only irreversible destructive action in the interface, and it looks like
# an ordinary text field. Typing 10 where 100 was meant has retention delete
# about 90 GB of footage on its next pass - permanently, with no question asked
# and no line anywhere saying it was about to happen.


def with_footage(tmp_path: Path, megabytes: int = 3) -> Path:
    """A recordings folder with real segments in it, laid out as the recorder
    lays them out: one folder per stream."""
    root = tmp_path / "recordings"
    (root / "thermal").mkdir(parents=True, exist_ok=True)
    for n in range(megabytes):
        (root / "thermal" / f"{n:04d}.mp4").write_bytes(b"0" * 1024 * 1024)
    return root


def budgeted(root: Path, budget_gb: float) -> Settings:
    from vmd.settings import StorageSettings

    # record=True, because a budget is a recording setting: the folder, the size
    # and the warning about lowering it are only shown, and only checked, when
    # something is being recorded. A test about any of them is a test about a
    # recording console.
    return Settings(
        record=True, storage=StorageSettings(root=root, budget_gb=budget_gb)
    )


def test_lowering_the_budget_says_what_it_will_delete_before_deleting_it(
    qtbot, tmp_path: Path
) -> None:
    root = with_footage(tmp_path)
    tab, path = build(qtbot, tmp_path, budgeted(root, 1.0))

    tab.budget_gb = "0.001"  # about one megabyte, against three on disk
    assert tab.save() is False, "the budget was lowered with no warning at all"
    assert "cannot be undone" in tab.message, tab.message
    assert "Save again" in tab.message, tab.message
    assert "MB" in tab.message, tab.message
    assert load_settings(path).storage.budget_gb == 1.0, "it wrote anyway"


def test_he_can_go_ahead_and_lower_it(qtbot, tmp_path: Path) -> None:
    """This is a real thing he needs to be able to do. Warned, not refused."""
    root = with_footage(tmp_path)
    tab, path = build(qtbot, tmp_path, budgeted(root, 1.0))

    tab.budget_gb = "0.001"
    assert tab.save() is False
    assert tab.save() is True
    assert load_settings(path).storage.budget_gb == 0.001


def test_correcting_the_number_asks_again(qtbot, tmp_path: Path) -> None:
    """The second figure is a different amount of footage."""
    root = with_footage(tmp_path)
    tab, path = build(qtbot, tmp_path, budgeted(root, 1.0))

    tab.budget_gb = "0.001"
    assert tab.save() is False
    tab.budget_gb = "0.002"
    assert tab.save() is False, "a different number went through unasked"


def test_an_ordinary_save_is_never_made_to_ask_twice(qtbot, tmp_path: Path) -> None:
    """Only the case that destroys something asks. Everything else saves on the
    first press, as every other setting on this page does."""
    root = with_footage(tmp_path)

    tab, path = build(qtbot, tmp_path, budgeted(root, 1.0))
    tab.camera_host = "10.0.0.2"
    assert tab.save() is True, tab.message

    # Raising it deletes nothing.
    tab.budget_gb = "2"
    assert tab.save() is True, tab.message

    # And lowering it to something the folder is still inside deletes nothing.
    tab.budget_gb = "1"
    assert tab.save() is True, tab.message


def test_a_budget_lowered_on_an_empty_folder_saves_straight_away(
    qtbot, tmp_path: Path
) -> None:
    root = tmp_path / "recordings"
    root.mkdir()
    tab, path = build(qtbot, tmp_path, budgeted(root, 100.0))
    tab.budget_gb = "10"
    assert tab.save() is True, tab.message


# ------------------------------------------- matching the picture to the link


def test_the_link_switch_is_on_the_form_and_on_by_default(qtbot, tmp_path: Path) -> None:
    """It has to be switchable off from here. Every serious failure this system
    has had traces to the link, and an operator watching the picture blip has to
    be able to stop it happening without a terminal and without being told to
    edit a file."""
    tab, path = build(qtbot, tmp_path)
    assert tab.link_auto is True
    tab.link_auto = False
    assert tab.save() is True, tab.message
    assert load_settings(path).bitrate.mode == "manual"


def test_the_switch_comes_back_as_it_was_left(qtbot, tmp_path: Path) -> None:
    settings = Settings()
    settings.bitrate.mode = "manual"
    tab, _ = build(qtbot, tmp_path, settings)
    assert tab.link_auto is False


def test_the_link_switch_says_what_it_does_in_plain_words(qtbot, tmp_path: Path) -> None:
    """The operator is not technical and will never read the spec. The form says
    things like "Watch for movement" and "Heat camera"; this is held to the same
    standard, and it has to name what switching it off costs."""
    tab, _ = build(qtbot, tmp_path)
    words = (
        tab.link_auto_field.text()
        + " "
        + tab.link_auto_field.toolTip()
        + " "
        + tab.link_help.text()
    ).lower()

    assert tab.link_auto_field.text().strip()
    assert "link" in words
    assert "picture" in words
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor")
    assert not any(word in words for word in banned), words
    # No units, no acronyms and no protocol names on the face of it.
    for jargon in ("onvif", "kbps", "kb/s", "bitrate", "airtime", "encoder", "airos"):
        assert jargon not in tab.link_auto_field.text().lower(), tab.link_auto_field.text()


# ------------------------------------------------- the tab he can actually use
#
# He went through this tab and could not read half of it: "'Watch for movement' -
# what is that?", "'Use this view' is useless, of course use that view, if it's
# added", "what is the difference between auto and ffmpeg?", "'Name what moved' -
# what is that?", "'Skyline and ignore...' - what is that?". Everything below is
# one of those sentences turned into something that can fail.

# Extended by the wording pass of 12 August: "budget" is a money word for a
# question about disk space, "path" here only ever meant the `/ch2` on the end
# of an RTSP address, and "go2rtc" is the name of a program he has never heard
# of and cannot run. All three were on the screen; none of them is a word he
# would use.
JARGON = (
    "yolo",
    "cnn",
    "classifier",
    "inference",
    "model",
    "sensor",
    "pixel",
    "budget",
    "path",
    "go2rtc",
)


def test_the_reader_choice_is_off_the_screen_but_not_out_of_the_file(
    qtbot, tmp_path: Path
) -> None:
    """"What is the difference between auto and ffmpeg?" - and there is no answer
    he could act on, because the honest one is "try the other if the picture will
    not come up". A question the operator cannot answer does not belong on the
    page he has to get through to set the camera up.

    Off the screen, not out of the settings: a stream whose file says `ffmpeg`
    still reads with ffmpeg after a load and a save, because a camera that only
    works one way must not be quietly switched to the other by a form that no
    longer shows the choice.
    """
    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="day", url="rtsp://10.0.0.2/ch0", reader="ffmpeg")
            ],
        )
    )
    tab, path = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]
    assert not hasattr(row, "reader_field"), "the choice is back on the screen"
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].reader == "ffmpeg"


def test_the_detection_controls_are_out_of_sight_until_he_asks_for_them(
    qtbot, tmp_path: Path
) -> None:
    """"Too much going on." What is left under the tick is two buttons, and
    neither means anything until the first one is switched on.

    Folded away, never deleted: he has said he wants to test movement detection
    in the next days, so both have to be there the moment he ticks the box.
    """
    tab, _ = build(qtbot, tmp_path, _watched())
    tab.show()
    QApplication.processEvents()
    row = tab.stream_rows()[0]

    hidden = (row.mask_button, row.advanced_button)
    assert row.detect_field.isVisible(), "the one switch that stays has gone too"
    for control in hidden:
        assert not control.isVisible(), f"{control} is on screen with watching off"

    row.detect_field.setChecked(True)
    QApplication.processEvents()
    for control in hidden:
        assert control.isVisible(), f"{control} did not come back"

    row.detect_field.setChecked(False)
    QApplication.processEvents()
    for control in hidden:
        assert not control.isVisible(), f"{control} stayed out"


def test_how_touchy_is_behind_advanced_and_starts_at_normal(
    qtbot, tmp_path: Path
) -> None:
    """He asked for **How touchy:** to be removed or hidden. Hidden.

    Removing it would leave him with a detector aimed at a treeline 700 m away
    that either alarms all night or says nothing, and no control anywhere that
    moves it between those two states - and he has said he means to test
    movement detection over the coming days. So it stays, behind a door, at the
    setting that is right until something proves otherwise.
    """
    tab, _ = build(qtbot, tmp_path, _watched(detect=True))
    tab.show()
    QApplication.processEvents()
    row = tab.stream_rows()[0]

    assert row.sensitivity() == "normal", "it does not start where it should"
    assert not row.sensitivity_field.isVisible(), "it is on the card at rest"
    assert not row.sensitivity_label.isVisible()
    assert row.advanced_button.isVisible(), "and no way to reach it"
    assert row.advanced_button.isChecked() is False, "the door starts open"

    row.advanced_button.setChecked(True)
    QApplication.processEvents()
    assert row.sensitivity_field.isVisible()
    assert row.sensitivity_label.isVisible()


def test_a_touchiness_chosen_behind_advanced_survives_the_door_closing(
    qtbot, tmp_path: Path
) -> None:
    """Hidden is not reset. This is the same rule as the fold above it, and it
    is the one that makes hiding a setting instead of deleting it honest."""
    tab, path = build(qtbot, tmp_path, _watched(detect=True))
    row = tab.stream_rows()[0]
    row.advanced_button.setChecked(True)
    row.set_sensitivity("high")
    row.advanced_button.setChecked(False)
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].sensitivity == "high"


def test_folding_the_detection_controls_away_does_not_forget_them(
    qtbot, tmp_path: Path
) -> None:
    """Hidden is not off. A choice he made and then folded away is still the
    choice that gets saved."""
    tab, path = build(
        qtbot, tmp_path, _watched(detect=True, thermal=True, sensitivity="high")
    )
    row = tab.stream_rows()[0]
    row.detect_field.setChecked(False)
    assert tab.save() is True

    stored = load_settings(path).camera.streams[0]
    assert stored.detect is False
    assert stored.sensitivity == "high", "the touchiness went when the box folded"
    assert stored.thermal is True, "a field the form no longer shows was reset"


def test_the_switch_for_watching_says_on_the_form_what_watching_does(
    qtbot, tmp_path: Path
) -> None:
    """"'Watch for movement' - what is that?" - asked by the person the label was
    written for. The name survives; what was missing is the sentence saying what
    actually happens when it is on, on the form rather than on hover.

    Said ONCE, and that is half of what is being tested. The views sit side by
    side now, so a sentence printed under each tick is the same paragraph twice,
    six inches apart, on the tab whose whole complaint was "too much going on" -
    and two copies of a paragraph do not explain a thing twice as well, they
    make the reader stop and check whether they differ.
    """
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    row = tab.stream_rows()[0]
    assert row.detect_field.text() == "Watch thermal for movement"

    said = tab.detect_help.text().lower()
    assert said.strip(), "the switch still explains itself only on hover"
    assert "move" in said, said
    # The two things he would actually notice. This used to ask for the words
    # "strip" or "red", after the red strip across the pictures - which was
    # taken out of the console a long time ago, along with both its buttons, so
    # the test was holding the help text to a promise the software had stopped
    # keeping. What he notices now is the line in the movement list and the
    # sound.
    assert "list" in said, said
    assert "sound" in said, said
    assert not any(word in said for word in JARGON), said

    # And nowhere else. Any label repeating it is the duplication coming back.
    _said_once(tab, said)


def test_ctrl_s_saves_without_scrolling_to_the_bottom_of_the_form(
    qtbot, tmp_path: Path
) -> None:
    """Save is at the bottom of a form about 1700 px tall on his screen, so
    reaching it means scrolling past everything he has just typed - and there
    was not one keyboard shortcut anywhere in this console.

    Scoped to this tab on purpose. The Live tab reads arrow keys straight out of
    its own key handler so that nothing can swallow a key release and leave the
    camera slewing; a window-wide shortcut would be the first thing in this
    program allowed to intercept anything.
    """
    tab, path = build(qtbot, tmp_path)
    assert "Ctrl+S" in tab.save_button.toolTip(), "a shortcut nobody is told about"

    tab.camera_host = "10.0.0.9"
    qtbot.keyClick(tab, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert path.exists(), "Ctrl+S wrote nothing"
    assert load_settings(path).camera.host == "10.0.0.9"


def test_ctrl_s_is_refused_while_a_save_is_still_being_applied(
    qtbot, tmp_path: Path
) -> None:
    """The same reason the button is disabled then: a second restart queued
    behind the first, of up to three child processes."""
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "10.0.0.9"
    tab.save_button.setEnabled(False)
    qtbot.keyClick(tab, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert not path.exists()


def test_an_ordinary_s_still_types_an_s(qtbot, tmp_path: Path) -> None:
    """A key handler that ate every S would make the address fields unusable."""
    tab, path = build(qtbot, tmp_path)
    qtbot.keyClick(tab, Qt.Key.Key_S)
    assert not path.exists(), "a bare S saved the file"


def test_the_alarm_sound_can_be_switched_off_and_the_choice_is_saved(
    qtbot, tmp_path: Path
) -> None:
    """Somebody sleeping in the same room has a good reason. Offering the switch
    is what stops the speakers being unplugged instead - which is the same
    silence, with nobody in charge of it and no way to tell it happened."""
    tab, path = build(qtbot, tmp_path)
    assert tab.alarm_sound is True, "it is on unless he says otherwise"
    tab.alarm_sound = False
    assert tab.save() is True
    assert load_settings(path).detection.alarm_sound is False


def test_the_alarm_sound_switch_says_what_it_costs_to_turn_off(
    qtbot, tmp_path: Path
) -> None:
    """It is the only setting on this tab that changes what happens in the room
    rather than what happens in the software, and the reason to turn it off is
    not the reason to turn most things off."""
    tab, _ = build(qtbot, tmp_path)
    said = tab._alarm_sound.text().lower() + " " + tab._alarm_sound.toolTip().lower()
    assert "sound" in said
    assert "sleep" in said, "no reason given for the one state that loses an alarm"
    banned = ("chime", "decibel", "wav", "audio device", "winsound")
    assert not any(word in said for word in banned), said


def test_which_lens_a_view_zooms_can_be_chosen_and_is_saved(
    qtbot, tmp_path: Path
) -> None:
    """The fault he reported: "only the vis is zooming". Which media profile
    belongs to which picture is worked out from the camera's own naming, which
    is a guess - and a wrong guess is silent, because the camera accepts the
    command and carries it out on the other lens.

    He can see which picture answers. Nothing in this program can.
    """
    tab, path = build(qtbot, tmp_path, _watched(name="thermal"))
    row = tab.stream_rows()[0]
    assert row.chosen_lens() == "", "it starts on the worked-out answer"

    row.set_lenses(
        [
            {"token": "p-vis", "name": "Visible", "can_zoom": True},
            {"token": "p-ir", "name": "Thermal", "can_zoom": True},
        ]
    )
    row.lens_field.setCurrentIndex(row.lens_field.findData("p-ir"))
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].ptz_profile == "p-ir"


def test_the_lens_chooser_stays_out_of_the_way_until_there_is_a_choice(
    qtbot, tmp_path: Path
) -> None:
    """A single-sensor camera has one answer, and a control offering one answer
    is furniture on the tab whose complaint was that there is too much on it."""
    tab, _ = build(qtbot, tmp_path, _watched(name="thermal"))
    row = tab.stream_rows()[0]
    assert not row.lens_row.isVisibleTo(tab), "offered before the camera said anything"

    row.set_lenses([{"token": "only", "name": "mainstream", "can_zoom": True}])
    assert not row.lens_row.isVisibleTo(tab), "one lens is not a choice"

    row.set_lenses(
        [
            {"token": "a", "name": "one", "can_zoom": True},
            {"token": "b", "name": "two", "can_zoom": True},
        ]
    )
    assert row.lens_row.isVisibleTo(tab)


def test_a_lens_that_cannot_zoom_is_offered_saying_so(qtbot, tmp_path: Path) -> None:
    """Offering it silently is offering a setting that cannot work. On a
    multi-spectral head it is common for only one picture to carry PTZ, and that
    is the likeliest reason a thermal slider does nothing."""
    tab, _ = build(qtbot, tmp_path, _watched(name="thermal"))
    row = tab.stream_rows()[0]
    row.set_lenses(
        [
            {"token": "p-vis", "name": "Visible", "can_zoom": True},
            {"token": "p-ir", "name": "Thermal", "can_zoom": False},
        ]
    )
    labels = [row.lens_field.itemText(i) for i in range(row.lens_field.count())]
    said = " | ".join(labels).lower()
    assert "cannot zoom" in said, labels


def test_a_lens_saved_against_another_camera_is_kept_rather_than_dropped(
    qtbot, tmp_path: Path
) -> None:
    """Silently resetting a setting he made is how he stops trusting the form.
    `Lenses` refuses to send an unknown token anyway, and says so in the log."""
    tab, _ = build(qtbot, tmp_path, _watched(name="thermal", ptz_profile="p-elsewhere"))
    row = tab.stream_rows()[0]
    row.set_lenses([{"token": "a", "name": "one", "can_zoom": True}])
    assert row.chosen_lens() == "p-elsewhere"
    labels = [row.lens_field.itemText(i) for i in range(row.lens_field.count())]
    assert any("not on this camera" in text for text in labels), labels


def test_what_the_camera_said_about_its_lenses_is_put_into_words() -> None:
    from vmd.desktop.settings_tab import lens_lines

    said = " | ".join(
        lens_lines(
            {
                "ok": True,
                "shared": False,
                "profiles": [
                    {"token": "p-vis", "name": "Visible", "can_zoom": True},
                    {"token": "p-ir", "name": "Thermal", "can_zoom": False},
                ],
                "using": {"visible": "p-vis", "thermal": "p-ir"},
            }
        )
    ).lower()
    assert "visible" in said and "thermal" in said
    assert "cannot zoom" in said, said
    assert "zoom drives" in said, "it never says where to change it"
    banned = ("onvif", "profile token", "absolutemove", "ptzconfiguration")
    assert not any(word in said for word in banned), said


def test_a_camera_that_would_not_answer_about_lenses_says_why() -> None:
    from vmd.desktop.settings_tab import lens_lines

    said = " ".join(lens_lines({"ok": False, "error": "cannot reach 192.168.1.251"}))
    assert "cannot reach 192.168.1.251" in said


def test_both_pictures_on_one_lens_is_explained_rather_than_left_a_mystery() -> None:
    from vmd.desktop.settings_tab import lens_lines

    said = " ".join(
        lens_lines(
            {
                "ok": True,
                "shared": True,
                "profiles": [{"token": "only", "name": "main", "can_zoom": True}],
                "using": {"thermal": "only", "visible": "only"},
            }
        )
    ).lower()
    assert "same lens" in said
    assert "not a fault in vmd" in said


def test_nothing_explains_a_control_that_is_not_on_the_screen(
    qtbot, tmp_path: Path
) -> None:
    """The paragraph about parts to ignore is about a button that is folded away
    until a view is watched - so on a console nobody has set up yet it is
    preamble before the first box, on the tab whose complaint was that there is
    too much on it.

    It comes back the moment there is something for it to be about.
    """
    tab, _ = build(qtbot, tmp_path, _watched(detect=False))
    # `isVisibleTo` and never `isVisible`: the tab has not been shown, so
    # `isVisible` is False for everything on it and the assertion would pass
    # whatever the code did. A mutation caught exactly that.
    assert not tab.ignore_help.isVisibleTo(tab)
    # The tick's own sentence stays: that tick is always on the screen, and it
    # is the one he asked about by name.
    assert tab.detect_help.isVisibleTo(tab)

    tab.stream_rows()[0].detect_field.setChecked(True)
    assert tab.ignore_help.isVisibleTo(tab)


def test_the_ignore_control_is_called_parts_to_ignore(qtbot, tmp_path: Path) -> None:
    """"'Skyline and ignore...' - what is that?" Two nouns from the source code
    joined by an "and", naming neither what it is for nor what it acts on. Then
    **Ignore parts of the picture**, which named an action rather than a thing.
    It is a list of parts of the picture, so that is what it says."""
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    row = tab.stream_rows()[0]
    assert row.mask_button.text() == "Parts to ignore"
    said = tab.ignore_help.text().lower()
    assert said.strip()
    for example in ("sky", "road", "tree"):
        assert example in said, said
    assert "wind" in said, said
    assert "not" in said and "report" in said, said
    assert not any(word in said for word in JARGON), said
    _said_once(tab, said)


def test_the_camera_tools_are_shut_away_until_he_goes_looking_for_them(
    qtbot, tmp_path: Path
) -> None:
    """"The camera - is it relevant anymore?" - and then, plainly: get rid of it.

    Refused. It is the only diagnostic on a machine with no terminal and no
    second computer, and *Which lens is behind which picture?* is the only cure
    for the fault he reported himself. What is true in the complaint is that it
    was the biggest thing on the page and always open: five buttons and a black
    rectangle under a form he came here to type four numbers into.

    So it is shut, and it is last. He will not meet it again unless he goes
    looking, and it is there on the day he needs it.
    """
    tab, _ = build(qtbot, tmp_path)
    tab.show()
    QApplication.processEvents()

    assert "Check the camera" in tab.tools_button.text()
    assert tab.tools_button.isChecked() is False, "it opens on the busiest box"
    for button in (tab.test_button, tab.find_button, tab.lens_button, tab.report_button):
        assert not button.isVisible(), button.text()
    assert not tab._output.isVisible(), "the black rectangle is still there"

    tab.tools_button.setChecked(True)
    QApplication.processEvents()
    for button in (tab.test_button, tab.find_button, tab.lens_button, tab.report_button):
        assert button.isVisible(), button.text()
    assert tab._output.isVisible()


def test_a_fold_says_which_way_it_is_pointing(qtbot, tmp_path: Path) -> None:
    """Three things on this tab are now behind a button, and the application
    stylesheet has no opinion about a checked QPushButton - so a fold he has
    opened and one he has not were drawn as the same rectangle.

    A caret rather than a colour, and rather than the button's pressed-in look:
    DESIGN.md says colour never carries meaning alone, and on a form he has to
    scroll the panel a fold opens is often off the bottom of the screen, so the
    button has to answer "is this open?" on its own.
    """
    from vmd.desktop.settings_tab import OPEN, SHUT

    tab, _ = build(qtbot, tmp_path, _watched(detect=True))
    folds = [tab.tools_button, tab.stream_rows()[0].advanced_button]
    for fold in folds:
        assert fold.text().startswith(SHUT), fold.text()
        fold.setChecked(True)
        assert fold.text().startswith(OPEN), fold.text()
        fold.setChecked(False)
        assert fold.text().startswith(SHUT), fold.text()
    assert OPEN != SHUT


def test_the_camera_tools_are_the_last_thing_before_save(qtbot, tmp_path: Path) -> None:
    """Shut is only half of it. A shut box in the middle of the form is still a
    thing he scrolls past twice on the way to the number he came for.

    One box is below the tools and below Save, and deliberately: **Software**
    is not a setting Save writes, it is the control that changes the program,
    and it is the last thing on the page for the same reason the tools are the
    last thing before it.
    """
    tab, _ = build(qtbot, tmp_path)
    tab.show()
    tab.setGeometry(0, 0, 1366, 2400)
    QApplication.processEvents()

    tools = tab.tools_button.mapTo(tab, QPoint(0, 0)).y()
    for box in tab.findChildren(QGroupBox):
        if box is tab.update_panel:
            continue
        assert box.mapTo(tab, QPoint(0, 0)).y() < tools, box.title()
    assert tab.save_button.mapTo(tab, QPoint(0, 0)).y() > tools
    assert tab.update_panel.mapTo(tab, QPoint(0, 0)).y() > tab.save_button.mapTo(
        tab, QPoint(0, 0)
    ).y()


def test_the_software_box_is_on_the_form_and_not_behind_a_fold(
    qtbot, tmp_path: Path
) -> None:
    """The only control on an air-gapped machine that changes the software. A
    fold would mean the operator has to be told it is there, over a telephone,
    on the day something has already gone wrong."""
    tab, _ = build(qtbot, tmp_path)
    tab.show()
    QApplication.processEvents()

    assert tab.update_panel.title() == "Software"
    assert tab.update_panel.isVisible()
    assert tab.update_panel.this_system.text().startswith("This system: VMD")


def test_no_camera_tool_button_is_clipped_by_its_own_label(
    qtbot, tmp_path: Path
) -> None:
    """Five of these on one line was about 1500 px of buttons in a column that
    stops at 980, and Qt clips a button at BOTH ends rather than eliding one -
    which is how the longest of them came out reading "urn the picture down to
    what the link can carr"."""
    from vmd.desktop.style import FORM_MAX_WIDTH, stylesheet

    tab, _ = build(qtbot, tmp_path)
    tab.setStyleSheet(stylesheet())
    tab.tools_button.setChecked(True)
    tab.show()
    tab.setGeometry(0, 0, FORM_MAX_WIDTH, 1600)
    QApplication.processEvents()

    cut = [
        (button.text(), button.width(), button.minimumSizeHint().width())
        for button in (
            tab.tools_button,
            tab.test_button,
            tab.find_button,
            tab.fit_button,
            tab.lens_button,
            tab.report_button,
        )
        if button.width() < button.minimumSizeHint().width()
    ]
    assert cut == [], f"cut off: {cut}"


def test_no_two_switches_for_watching_movement_say_the_same_thing(
    qtbot, tmp_path: Path
) -> None:
    """There were three of them on one screen and two were word for word
    identical: **Watch for movement** on each camera card, and **Watch for
    movement at all** below them - which is the card's sentence with two more
    words on the end.

    Nothing in either card switch said which head of the gimbal it belonged to,
    so the only way to tell them apart was to look at which card they were
    drawn on. The name is already on the card, in the box above the switch, and
    it is the one thing that does tell them apart - so the switch says it.
    """
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    thermal, visible = tab.stream_rows()

    master = tab._detection_enabled.text()
    assert master == "Watch for movement on any view"
    assert thermal.detect_field.text() == "Watch thermal for movement"
    assert visible.detect_field.text() == "Watch visible for movement"

    said = [master, thermal.detect_field.text(), visible.detect_field.text()]
    assert len(set(said)) == 3, said
    # And not merely different: none of them is another one with words added,
    # which is what made the old pair read as one control seen twice.
    for one in said:
        for other in said:
            if one is not other:
                assert not other.startswith(one), f"{other!r} begins with {one!r}"

    # It follows the name as it is typed. A card whose name has just been
    # corrected must not go on offering to watch the one it used to be.
    thermal.name_field.setText("north fence")
    assert thermal.detect_field.text() == "Watch north fence for movement"
    # And before there is a name there is still a sentence.
    thermal.name_field.setText("")
    assert thermal.detect_field.text() == "Watch this view for movement"


def test_the_room_on_the_drive_is_not_called_a_budget(qtbot, tmp_path: Path) -> None:
    """"Budget" is a money word, and nothing here is money: he is being asked
    how much of the drive VMD may fill with footage.

    Nowhere on the tab, not just on the label - a note or a scan report still
    calling it a budget names a box that is not called that any more, which is
    the same defect one line along.
    """
    tab, _ = build(qtbot, tmp_path)
    said = [label.text() for label in tab.findChildren(QLabel)]
    assert "How much space VMD may use (GB)" in said, said
    for text in said + [tab.budget_slider.toolTip()]:
        assert "budget" not in text.lower(), text


def test_the_scan_button_says_what_it_looks_at_and_what_it_gives_back(
    qtbot, tmp_path: Path
) -> None:
    """"Scan this PC" reads as a virus scan, or as a hunt for cameras. It reads
    one drive and suggests two numbers."""
    tab, _ = build(qtbot, tmp_path)
    assert tab.scan_button.text() == "Look at this drive and suggest a size"
    assert "scan" not in tab.scan_button.text().lower()
    # And the sentence under it does not send him looking for a button by a name
    # that is no longer on it.
    note = tab.storage_scan_note.text().lower()
    assert "scan this pc" not in note, note
    words = (tab.scan_button.text() + " " + tab.scan_button.toolTip()).lower()
    assert not any(word in words for word in JARGON), words


def test_the_link_note_gives_the_figure_rather_than_naming_a_hidden_setting(
    qtbot, tmp_path: Path
) -> None:
    """It said "It never goes below the lowest picture you allow", and there is
    no such control on this screen or on any screen - it is a number in the
    file. A limit "you allow" that he has never been shown and cannot change is
    worse than one that is not mentioned: he goes looking for it.

    So it says the figure, and it says the figure that is actually in force.
    """
    settings = Settings()
    settings.bitrate.floor_kbps = 2000
    tab, _ = build(qtbot, tmp_path, settings)
    said = tab.link_help.text()
    assert "2 Mb/s" in said, said
    assert "you allow" not in said.lower(), said

    # Read off the file, not typed once into the source: a different floor says
    # a different number.
    settings.bitrate.floor_kbps = 1500
    other, _ = build(qtbot, tmp_path / "other", settings)
    assert "1.5 Mb/s" in other.link_help.text(), other.link_help.text()


def test_the_camera_tool_buttons_say_what_they_do_to_the_camera(
    qtbot, tmp_path: Path
) -> None:
    """"Find the right path" - a path to him is a track; this one was the `/ch2`
    on the end of an address. "Fit the camera to the link" reads as an
    instruction about mounting one."""
    tab, _ = build(qtbot, tmp_path)
    assert tab.find_button.text() == "Find the camera's address"
    assert tab.fit_button.text() == "Turn the picture down to what the link can carry"
    for button in (tab.test_button, tab.find_button, tab.fit_button, tab.report_button):
        assert not any(word in button.text().lower() for word in JARGON), button.text()


# ---------------------------------------------------------------- the storage
#
# "I want a button that scans the PC storage situation and then automatically
# adjusts the parameters like the budget, delete older than and so on. Make it
# nicer and easier, like a slider for the budget. If the user wants, he can edit."


def test_scanning_this_pc_fills_in_the_budget_and_the_age_rule(
    qtbot, tmp_path: Path
) -> None:
    """The two numbers he was expected to invent, worked out from the drive."""
    root = with_footage(tmp_path)
    tab, _ = build(qtbot, tmp_path, budgeted(root, 100.0))
    tab.disk_usage = a_drive(total_gb=1000, free_gb=500)

    tab.scan_this_pc()

    # Everything free, less a slice of the drive kept back so it is never filled.
    assert float(tab.budget_gb) == 450.0, tab.budget_gb
    # And an age rule that matches what the budget holds, so footage goes for one
    # reason rather than two.
    assert tab.retention_days == "8", tab.retention_days
    # And the slider now measures this drive rather than an invented scale: the
    # handle three-quarters along means three-quarters of what is really there.
    assert tab.budget_slider.maximum() == 1000, tab.budget_slider.maximum()


def test_the_scan_says_in_plain_words_what_it_found(qtbot, tmp_path: Path) -> None:
    """"Show what it found in plain words." Two numbers changing in two boxes is
    not an answer to "what is the storage situation on this PC"."""
    root = with_footage(tmp_path)
    tab, _ = build(qtbot, tmp_path, budgeted(root, 100.0))
    # An empty box is a black rectangle, and this one has to say what the button
    # beside it is for before anyone has pressed it.
    assert tab.storage_scan_note.text().strip()

    tab.disk_usage = a_drive(total_gb=1000, free_gb=500)
    tab.scan_this_pc()
    said = tab.storage_scan_note.text()

    assert "1000" in said or "1,000" in said, said  # the whole drive
    assert "500" in said, said                      # what is free
    assert "450" in said, said                      # what it suggests
    assert "8 days" in said, said                   # what that buys
    lower = said.lower()
    assert "free" in lower, said
    # It has to be readable as a suggestion rather than as a decision taken.
    assert "suggest" in lower, said
    for jargon in ("bytes", "gib", "disk_usage", "retention", "budget_gb"):
        assert jargon not in lower, said


def test_the_scan_names_the_drive_and_says_why_it_disagrees_with_the_label(
    qtbot, tmp_path: Path
) -> None:
    """"The laptop have 950gb", and VMD says 884.

    Both are right. VMD divides by 1024 three times and calls the answer GB,
    which is exactly what Windows does - so This PC on his own machine says 884
    too - while the sticker counts a gigabyte as a thousand million bytes. A
    number he believes is wrong is a number he stops reading, and this is the
    number the whole Storage box is about.

    So the drive is named, which is the one word that lets him put the two
    figures side by side, and the reason is said once where the figure first
    appears.
    """
    root = with_footage(tmp_path)
    tab, _ = build(qtbot, tmp_path, budgeted(root, 100.0))
    tab.disk_usage = a_drive(total_gb=884, free_gb=500)
    tab.scan_this_pc()
    said = tab.storage_scan_note.text()

    # Named, so This PC can be opened beside it.
    assert "Drive " in said, said
    assert "Windows" in said, said
    # And the two numbers that do not agree, both of them, in one sentence.
    assert "950" in said, said
    assert "884" in said, said
    assert "gib" not in said.lower(), "the exact unit is the one he cannot check"


def test_a_folder_that_cannot_be_read_is_not_reported_as_empty(
    qtbot, tmp_path: Path
) -> None:
    """"Nothing recorded" and "nobody could look" are different sentences and
    only one of them is good news. `recorded_bytes` answers None for the second
    and its own docstring says that must not be read as zero - and "VMD's own
    footage comes to nothing yet" under a folder full of footage nobody could
    count is the reading that loses him the most."""
    from vmd.desktop.settings_tab import scan_drive

    there = tmp_path / "recordings"
    there.mkdir()
    words = scan_drive(
        there,
        1_000_000.0,
        usage=a_drive(total_gb=1000, free_gb=500),
        recorded=lambda root: None,
    ).words
    assert "nothing yet" not in words, words
    assert "could not be read" in words, words

    # And the ordinary first run - the folder is simply not there yet, because
    # the recorder makes it - still says the plain thing.
    first_run = scan_drive(
        tmp_path / "not-yet",
        1_000_000.0,
        usage=a_drive(total_gb=1000, free_gb=500),
        recorded=lambda root: None,
    ).words
    assert "nothing yet" in first_run, first_run


def test_what_the_scan_suggested_is_still_his_to_change(qtbot, tmp_path: Path) -> None:
    """"If the user wants, he can edit." A suggestion that cannot be overruled is
    a decision wearing a suggestion's clothes."""
    root = with_footage(tmp_path)
    tab, path = build(qtbot, tmp_path, budgeted(root, 100.0))
    tab.disk_usage = a_drive(total_gb=1000, free_gb=500)
    tab.scan_this_pc()

    tab.budget_gb = "77"
    tab.retention_days = "3"
    assert tab.save() is True, tab.message

    stored = load_settings(path).storage
    assert stored.budget_gb == 77.0
    assert stored.retention_days == 3


def test_a_drive_that_cannot_be_read_changes_nothing_and_says_so(
    qtbot, tmp_path: Path
) -> None:
    """The scan touches the filesystem, which is exactly the thing that is broken
    in the cases that matter. It must not answer a failed reading with a number.
    """
    root = with_footage(tmp_path)
    tab, _ = build(qtbot, tmp_path, budgeted(root, 100.0))

    def refuses(path):
        raise OSError(5, "The device is not ready")

    tab.disk_usage = refuses
    tab.scan_this_pc()

    assert float(tab.budget_gb) == 100.0, "it guessed at a drive it could not read"
    assert "not ready" in tab.storage_scan_note.text(), tab.storage_scan_note.text()


def test_the_slider_and_the_typed_budget_are_one_number(qtbot, tmp_path: Path) -> None:
    """"Make it nicer and easier, like a slider for the budget. If the user
    wants, he can edit." Two controls, one setting - and a form where the two
    disagree would save whichever one the code happened to read."""
    tab, path = build(qtbot, tmp_path)

    tab.budget_slider.setValue(250)
    assert tab.budget_gb == "250"
    assert tab.save() is True, tab.message
    assert load_settings(path).storage.budget_gb == 250.0

    tab.budget_gb = "60"
    assert tab.budget_slider.value() == 60


def test_the_slider_says_how_many_days_of_footage_the_budget_buys(
    qtbot, tmp_path: Path
) -> None:
    """A budget in gigabytes is not a quantity anybody has an instinct for. The
    number he is really choosing is how far back he can look."""
    tab, _ = build(qtbot, tmp_path, _watched())

    tab.budget_slider.setValue(100)
    small = tab.budget_days_note.text()
    tab.budget_slider.setValue(500)
    large = tab.budget_days_note.text()

    assert "day" in small.lower(), small
    assert small != large, "the same answer for a fifth of the disk"
    assert any(character.isdigit() for character in small), small


def test_the_slider_stops_where_the_drive_stops(qtbot, tmp_path: Path) -> None:
    """From the first time the tab is opened, not from the first time he presses
    the scan button.

    The slider used to run to an invented 2000 GB. On a drive that holds 884 the
    far end of it is a size nothing can ever reach, so the handle says nothing
    about how full anything is - and three quarters along means three quarters
    of a number that does not exist.
    """
    tab, _ = build(qtbot, tmp_path, drive=a_drive(total_gb=884, free_gb=400))
    assert tab.budget_slider.maximum() == 884, tab.budget_slider.maximum()


def test_a_size_bigger_than_the_drive_is_refused_in_words(
    qtbot, tmp_path: Path
) -> None:
    """"The laptop have 950gb and the VMD doesnt stop me in the budget."

    The deleting was never wrong - retention removes the oldest segments to stay
    inside the size. The fault is that the form let him name a size the drive
    can never reach, and a threshold nothing can cross is a threshold that never
    fires: nothing is deleted, and the drive fills up until recording stops.
    """
    tab, path = build(qtbot, tmp_path, drive=a_drive(total_gb=884, free_gb=400))
    tab.record = True  # the size is only checked against the drive when recording
    tab.budget_gb = "2000"

    assert tab.save() is False, "a size the drive cannot hold was accepted"
    said = tab.message
    assert "884" in said, said
    assert "2000" in said, said
    # Named, so it can be put beside This PC on his own machine.
    assert "Drive" in said, said
    # And it says what goes wrong, not merely that it is not allowed.
    assert "deleted" in said.lower(), said
    assert not path.exists() or load_settings(path).storage.budget_gb != 2000.0


def test_a_size_past_the_end_of_the_drive_is_said_as_it_is_typed(
    qtbot, tmp_path: Path
) -> None:
    """Not only at Save. The line under the box was promising "about 19 days of
    footage" for a size the drive can never hold, which is the form agreeing
    with the mistake for as long as it takes him to reach the button."""
    tab, _ = build(qtbot, tmp_path, drive=a_drive(total_gb=884, free_gb=400))
    tab.budget_gb = "2000"

    said = tab.budget_days_note.text().lower()
    assert "884" in said, said
    assert "day" not in said, "it still promises footage it can never hold"

    tab.budget_gb = "800"
    assert "day" in tab.budget_days_note.text().lower()


def test_the_size_he_typed_is_never_quietly_rewritten(qtbot, tmp_path: Path) -> None:
    """Refused, not clamped. Rewriting a number he typed is how a form loses a
    field, and this is the field that decides how much footage he keeps: he has
    to see that what he chose is not what will be in force."""
    tab, _ = build(qtbot, tmp_path, drive=a_drive(total_gb=884, free_gb=400))
    tab.budget_gb = "2000"
    tab.save()
    assert tab.budget_gb == "2000", "the form corrected him behind his back"


def test_a_size_the_drive_can_hold_saves_as_it_always_did(
    qtbot, tmp_path: Path
) -> None:
    """Only the impossible case is refused. Everything else goes through on the
    first press, as every other setting on this page does."""
    tab, path = build(qtbot, tmp_path, drive=a_drive(total_gb=884, free_gb=400))
    tab.budget_gb = "800"
    assert tab.save() is True, tab.message
    assert load_settings(path).storage.budget_gb == 800.0


def test_a_drive_that_cannot_be_read_refuses_nothing_on_a_guess(
    qtbot, tmp_path: Path
) -> None:
    """A folder on a drive letter with nothing behind it is already refused a
    line above this, in words about the real fault. Refusing it a second time on
    the strength of a size nobody could measure would name the wrong problem."""

    def refuses(path):
        raise OSError(5, "The device is not ready")

    tab, _ = build(qtbot, tmp_path, drive=refuses)
    assert tab._drive_gb is None
    tab.budget_gb = "100000"
    assert tab._bigger_than_the_drive(tab.settings_from_form()) == ""


# ------------------------------------------- what the age rule actually does
#
# "Delete older than (days)" stays: asked whether there is a legal requirement,
# he said yes. What was wrong is that it was tied to nothing - he can set 90
# days while holding six, and a rule that will not fire for another eighty-four
# reads exactly like a rule that is working.


def holding(tab, days: float) -> None:
    """Put a stated number of days of footage on the drive, without writing it.

    His case is six days against a ninety-day rule, and six days of footage at
    the rate this camera records is about 1.3 GB of files - not something a
    test writes. What the form actually reads is one measurement of the folder,
    so that is what is set: the walk is what is being skipped, not the
    arithmetic.
    """
    tab._footage_bytes = int(days * 86400 * tab._footage_rate())
    tab._say_what_the_age_rule_does()


def test_the_age_rule_says_it_is_deleting_nothing_when_it_is_deleting_nothing(
    qtbot, tmp_path: Path
) -> None:
    """His complaint, in his own numbers: ninety days set while holding six.

    Nothing on the screen said the rule would not fire for another eighty-four
    days, so the box read exactly like a rule that was working - and it is the
    number he would point at if anybody asked how long footage is kept.
    """
    tab, _ = build(qtbot, tmp_path)
    tab.retention_days = "90"
    holding(tab, days=6)

    said = tab.retention_note.text().lower()
    assert said.strip(), "the box still says nothing about what it does"
    assert "6 days" in said, said
    assert "90 days" in said, said
    assert "nothing" in said, said
    # And it points at the rule that IS deleting his footage.
    assert "size above" in said, said


def test_the_age_rule_says_so_when_it_is_the_one_deleting(
    qtbot, tmp_path: Path
) -> None:
    """The other half. A rule shorter than the footage there is really does
    delete, and then it is the rule that decides rather than the size."""
    tab, _ = build(qtbot, tmp_path)
    tab.retention_days = "3"
    holding(tab, days=6)

    said = tab.retention_note.text().lower()
    assert "6 days" in said, said
    assert "3 days" in said, said
    assert "deleted" in said, said
    assert "deletes nothing" not in said, said


def test_an_empty_age_rule_says_what_that_means(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.retention_days = ""
    holding(tab, days=6)
    said = tab.retention_note.text().lower()
    assert "age" in said, said
    assert "size above" in said, said


def test_a_few_minutes_of_footage_is_not_rounded_up_to_a_day(
    qtbot, tmp_path: Path
) -> None:
    """`_days_of_footage` never answers zero, and it is right not to for a size:
    a size that holds part of a day holds something. This is not a size, it is a
    measurement - and "about 1 day" for four minutes of footage is the box lying
    in the direction that makes the age rule look reasonable."""
    root = with_footage(tmp_path, megabytes=3)
    tab, _ = build(qtbot, tmp_path, budgeted(root, 100.0))
    tab.retention_days = "90"

    said = tab.retention_note.text().lower()
    assert "less than a day" in said, said
    assert "1 day of footage" not in said, said


def test_the_age_rule_note_invents_nothing_when_the_folder_is_not_there(
    qtbot, tmp_path: Path
) -> None:
    """First run: the recorder has not made the folder yet. An invented amount
    of footage under a rule about deleting footage is the worst sentence this
    box could carry, so it says nothing about how much there is - and still says
    what the rule does."""
    tab, _ = build(qtbot, tmp_path, budgeted(tmp_path / "not-yet", 100.0))
    tab.retention_days = "90"

    said = tab.retention_note.text().lower()
    assert "you have" not in said, said
    assert "90 days" in said, said


# ------------------------------------------------- the sliders that are crossed
#
# He pulled the last fix for this and reported back: still crossed. "The thermal
# zoom slider moves the vis and the vis zoom slider moves the thermal."
#
# There has been a per-view override on each card all along - Zoom drives - and
# it was useless to him twice over. It is hidden until the camera has said it
# has more than one lens, and the only thing that made the camera say so was a
# button buried inside Check the camera, which he has no reason to have pressed.
# A fix that lives behind a tool he has never heard of is not a fix.


class _FakePtz:
    """A camera that answers about its lenses, and counts being asked."""

    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.asked = 0

    def zoom_profiles(self) -> dict:
        self.asked += 1
        return self.answer


TWO_LENSES = [
    {"token": "p-vis", "name": "Visible", "can_zoom": True},
    {"token": "p-ir", "name": "Thermal", "can_zoom": True},
]


def crossed(**over) -> dict:
    """What the camera says when the two sliders are crossed: thermal on the
    visible lens and the other way round. Which is indistinguishable, from here,
    from them being right - the camera answers the same either way, and only the
    operator can see which picture moved."""
    answer = {
        "ok": True,
        "error": "",
        "shared": False,
        "profiles": TWO_LENSES,
        "using": {"thermal": "p-vis", "visible": "p-ir"},
    }
    answer.update(over)
    return answer


def a_camera(qtbot, tmp_path: Path, answer: dict):
    from vmd.desktop.settings_tab import CameraTools

    ptz = _FakePtz(answer)
    tools = CameraTools(
        ptz=ptz, find_paths=lambda s, on_progress: [], diagnose=lambda s: []
    )
    path = tmp_path / "settings.json"
    save_settings(_two_watched_views(), path)
    tab = SettingsTab(settings_path=path, tools=tools)
    qtbot.addWidget(tab)
    tab.disk_usage = a_drive(total_gb=1000, free_gb=500)
    tab.load()
    return tab, ptz, path


def test_swapping_exchanges_the_two_views_lenses(qtbot, tmp_path: Path) -> None:
    """One press, and nothing to understand. He can SEE that each slider moves
    the other picture; that sentence is the whole of the input this needs."""
    tab, _ptz, _path = a_camera(qtbot, tmp_path, crossed())
    thermal, visible = tab.stream_rows()
    assert thermal.chosen_lens() == "", "it starts on the worked-out answer"

    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: thermal.chosen_lens() != "", timeout=5000)

    # The camera said thermal was on p-vis and visible on p-ir. Exchanged.
    assert thermal.chosen_lens() == "p-ir"
    assert visible.chosen_lens() == "p-vis"
    assert tab.message
    assert "save" in tab.message.lower(), tab.message


def test_a_swap_survives_a_save(qtbot, tmp_path: Path) -> None:
    """It writes ptz_profile on both views, which is what makes it stick across
    a restart. Written as a choice, not as a fact about this afternoon."""
    tab, _ptz, path = a_camera(qtbot, tmp_path, crossed())
    thermal, _visible = tab.stream_rows()
    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: thermal.chosen_lens() != "", timeout=5000)

    assert tab.save() is True, tab.message
    stored = {s.name: s.ptz_profile for s in load_settings(path).camera.streams}
    assert stored == {"thermal": "p-ir", "visible": "p-vis"}


def test_swapping_twice_puts_them_back(qtbot, tmp_path: Path) -> None:
    """The one thing a non-engineer must be able to do with a control he does
    not understand: undo it by pressing it again. `using` comes back from the
    camera reflecting what is set now, so the second press is the first press
    read backwards."""
    tab, ptz, _path = a_camera(qtbot, tmp_path, crossed())
    thermal, visible = tab.stream_rows()

    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: thermal.chosen_lens() == "p-ir", timeout=5000)

    ptz.answer = crossed(using={"thermal": "p-ir", "visible": "p-vis"})
    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: thermal.chosen_lens() == "p-vis", timeout=5000)
    assert visible.chosen_lens() == "p-ir"


def test_a_swap_says_it_happened_where_he_is_looking(qtbot, tmp_path: Path) -> None:
    """The button is under the camera cards near the top of a form about 1400 px
    tall; the message line is beside Save at the bottom of it. So the only sign
    that anything happened was off the screen he was looking at - and an
    operator who presses this, sees nothing and presses it again has undone it,
    which is the one way this control can fail him."""
    from vmd.desktop.settings_tab import SWAP_DONE, SWAP_INVITATION

    tab, _ptz, _path = a_camera(qtbot, tmp_path, crossed())
    assert tab.swap_note.text() == SWAP_INVITATION

    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: tab.swap_note.text() != SWAP_INVITATION, timeout=5000)
    assert tab.swap_note.text() == SWAP_DONE
    assert "save" in tab.swap_note.text().lower()


def test_a_swap_that_did_not_happen_does_not_claim_it_did(
    qtbot, tmp_path: Path
) -> None:
    """Every way this can fail puts the line back, and the case that matters is
    the second press: a caption still reading "Swapped" over two views whose
    lenses were not touched this time is worse than no caption, because it is
    the only thing near the button and it is wrong."""
    from vmd.desktop.settings_tab import SWAP_DONE, SWAP_INVITATION

    for answer, expect in (
        ({"ok": False, "error": "cannot reach it", "profiles": []}, "cannot reach"),
        (
            crossed(
                profiles=[{"token": "only", "name": "main", "can_zoom": True}],
                using={"thermal": "only", "visible": "only"},
            ),
            "same lens",
        ),
        (crossed(using={"thermal": "p-vis", "visible": ""}), "nothing to swap"),
    ):
        tab, ptz, _path = a_camera(qtbot, tmp_path / expect[:6], crossed())
        # One that works, so the caption is showing the wrong thing to leave up.
        tab.swap_the_zoom_sliders()
        qtbot.waitUntil(lambda: tab.swap_note.text() == SWAP_DONE, timeout=5000)

        ptz.answer = answer
        tab.swap_the_zoom_sliders()
        qtbot.waitUntil(lambda: expect in tab.message.lower(), timeout=5000)
        assert tab.swap_note.text() == SWAP_INVITATION, (
            f"{expect}: the caption still says {tab.swap_note.text()!r}"
        )


def test_swap_is_offered_only_where_it_means_something(qtbot, tmp_path: Path) -> None:
    """"Swap" has no meaning with one view and none with three. Both of those
    are answered by Zoom drives on the cards, which names the lens."""
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    tab.show()
    QApplication.processEvents()
    assert tab.swap_row.isVisible()

    tab.set_streams(list(_three_streams().camera.streams))
    QApplication.processEvents()
    assert not tab.swap_row.isVisible(), "offered with three views"

    tab.set_streams([("only", "rtsp://a/1", True, "auto")])
    QApplication.processEvents()
    assert not tab.swap_row.isVisible(), "offered with one view"


def test_the_swap_control_says_the_fault_in_his_words(qtbot, tmp_path: Path) -> None:
    """His sentence, not ours: "the thermal zoom slider moves the vis". He is
    not looking for a setting called ptz_profile, he is looking for the line
    that describes what he can see happening."""
    from vmd.desktop.style import FORM_MAX_WIDTH, stylesheet

    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    said = (tab.swap_note.text() + " " + tab.swap_button.text()).lower()
    assert "zoom" in said, said
    assert "wrong" in said, said
    assert "swap" in said, said
    words = said + " " + tab.swap_button.toolTip().lower()
    for jargon in ("onvif", "profile", "token", "ptz", "media"):
        assert jargon not in words, words

    # And it is drawn on one line beside its button. A `WrappedNote` in a row
    # with a button is handed its narrowest useful width, which broke six words
    # over three lines with 700 px of empty column next to them.
    tab.setStyleSheet(stylesheet())
    tab.show()
    tab.setGeometry(0, 0, FORM_MAX_WIDTH, 1200)
    QApplication.processEvents()
    assert tab.swap_note.height() < 2 * tab.swap_button.height(), (
        f"{tab.swap_note.height()} px for one line of caption"
    )
    assert tab.swap_note.width() >= tab.swap_note.minimumSizeHint().width()


def test_a_camera_that_cannot_be_asked_says_so_and_swaps_nothing(
    qtbot, tmp_path: Path
) -> None:
    """A button that reports a crossed pair of sliders and then does nothing
    visible is the fault he already reported, wearing a fix's clothes."""
    tab, _ptz, _path = a_camera(
        qtbot,
        tmp_path,
        {"ok": False, "error": "cannot reach 192.0.2.99", "profiles": []},
    )
    thermal, visible = tab.stream_rows()

    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: "cannot reach" in tab.message, timeout=5000)

    assert "192.0.2.99" in tab.message, tab.message
    assert "nothing was changed" in tab.message.lower(), tab.message
    assert thermal.chosen_lens() == ""
    assert visible.chosen_lens() == ""


def test_both_pictures_on_one_lens_is_explained_rather_than_swapped(
    qtbot, tmp_path: Path
) -> None:
    """A swap cannot fix a camera sending one lens down both pictures, and the
    operator has to be told that rather than left pressing a button that
    changes nothing."""
    tab, _ptz, _path = a_camera(
        qtbot,
        tmp_path,
        crossed(
            shared=True,
            profiles=[{"token": "only", "name": "main", "can_zoom": True}],
            using={"thermal": "only", "visible": "only"},
        ),
    )
    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: "same lens" in tab.message, timeout=5000)

    said = tab.message.lower()
    assert "same lens" in said, said
    assert "not a fault in vmd" in said, said
    assert tab.stream_rows()[0].chosen_lens() == ""


def test_a_view_the_camera_says_nothing_about_is_not_silently_swapped(
    qtbot, tmp_path: Path
) -> None:
    tab, _ptz, _path = a_camera(
        qtbot, tmp_path, crossed(using={"thermal": "p-vis", "visible": ""})
    )
    tab.swap_the_zoom_sliders()
    qtbot.waitUntil(lambda: "nothing to swap" in tab.message, timeout=5000)

    assert "visible" in tab.message, tab.message
    assert "nothing to swap" in tab.message.lower(), tab.message
    assert tab.stream_rows()[0].chosen_lens() == ""


def test_the_lens_choosers_are_filled_when_the_tab_is_first_opened(
    qtbot, tmp_path: Path
) -> None:
    """Zoom drives is hidden until the camera has said it has more than one
    lens, and the only thing that made it say so was a button inside Check the
    camera - now shut by default, and never pressed by him. A control that
    appears only after a tool he has never heard of is a control that does not
    exist."""
    tab, ptz, _path = a_camera(qtbot, tmp_path, crossed())
    thermal = tab.stream_rows()[0]
    assert not thermal.lens_row.isVisibleTo(tab), "offered before the camera answered"

    tab.show()
    qtbot.waitUntil(lambda: ptz.asked >= 1, timeout=5000)
    qtbot.waitUntil(lambda: thermal.lens_row.isVisibleTo(tab), timeout=5000)
    offered = [thermal.lens_field.itemData(i) for i in range(thermal.lens_field.count())]
    assert "p-vis" in offered and "p-ir" in offered, offered

    # Once. The tab is shown every time he comes back to it, and this crosses
    # the radio link.
    tab.hide()
    tab.show()
    QApplication.processEvents()
    assert ptz.asked == 1, ptz.asked


def test_opening_the_tab_never_reports_a_question_he_did_not_ask(
    qtbot, tmp_path: Path
) -> None:
    """The console starts on the Live tab of a machine whose camera may be off.
    Opening Settings must not greet him with a camera failure he did not ask
    about - and must not fill the report box, which is the record of what the
    buttons under it said."""
    tab, ptz, _path = a_camera(
        qtbot, tmp_path, {"ok": False, "error": "cannot reach it", "profiles": []}
    )
    tab.show()
    qtbot.waitUntil(lambda: ptz.asked >= 1, timeout=5000)
    QApplication.processEvents()

    assert "cannot reach" not in tab.message, tab.message
    assert tab.output_text() == "", tab.output_text()
    assert not tab.stream_rows()[0].lens_row.isVisibleTo(tab)


def test_opening_the_tab_does_not_overwrite_what_load_had_to_say(
    qtbot, tmp_path: Path
) -> None:
    """`settings_from_form` writes to the message line when it refuses, and the
    message line is where `load` puts the reason a settings file would not read
    - the one sentence telling him how to get his console back. A question he
    did not ask must not take it off the screen."""
    settings = _two_watched_views()
    settings.camera.streams[1].url = ""  # a view the form will refuse
    tab, ptz, _path = a_camera(qtbot, tmp_path, crossed())
    tab.set_streams(list(settings.camera.streams))
    tab._set_message("The settings file could not be read, so press Save.")

    tab.show()
    qtbot.waitUntil(lambda: ptz.asked >= 1, timeout=5000)
    QApplication.processEvents()
    assert "could not be read" in tab.message, tab.message


# ------------------------------------------------------- the two heads, at once


def test_the_camera_views_are_side_by_side_rather_than_stacked(
    qtbot, tmp_path: Path
) -> None:
    """"Make the vis and thermal in the settings side by side instead of one
    under the other, so it's easier." They are one camera with two heads and he
    sets them up together, so reading one against the other should not mean
    scrolling."""
    from PySide6.QtCore import QPoint

    settings = Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[
                StreamSettings(name="thermal", url="rtsp://a/1"),
                StreamSettings(name="visible", url="rtsp://a/2"),
            ],
        )
    )
    tab, _ = build(qtbot, tmp_path, settings)
    tab.show()
    tab.setGeometry(0, 0, 1366, 768)
    QApplication.processEvents()

    thermal, visible = tab.stream_rows()
    left = thermal.mapTo(tab, QPoint(0, 0))
    right = visible.mapTo(tab, QPoint(0, 0))
    assert left.y() == right.y(), "one is still under the other"
    assert right.x() > left.x()
    # And neither is squeezed into a sliver to make room for the other.
    assert abs(thermal.width() - visible.width()) <= 2, (
        f"{thermal.width()} px against {visible.width()}"
    )


def test_a_number_that_will_not_parse_is_refused_by_the_name_on_the_form(
    qtbot, tmp_path: Path
) -> None:
    """`storage.retention_days: Input should be a valid integer, unable to parse
    string as an integer` is a library's sentence with a Python attribute path in
    front of it, shown to a man who has never seen either.

    Two things are wrong with it and only one of them is the wording. The other
    is that it names the offending field by a name that appears nowhere on the
    screen - so on a form he has to scroll, the one sentence telling him what to
    correct does not tell him where it is. Every field a number can be typed into
    has a label a foot away from it; that label is what the message has to use.
    """
    for field, typed, label in (
        ("retention_days", "two weeks", "Delete older than (days)"),
        ("budget_gb", "as much as it likes", "How much space VMD may use (GB)"),
    ):
        tab, path = build(qtbot, tmp_path / field)
        tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
        setattr(tab, field, typed)

        assert tab.save() is False, f"{field}={typed!r} was accepted"
        said = tab.message
        assert label in said, said
        assert "storage." not in said and "detection." not in said, said
        assert "_" not in said, said
        assert "parse" not in said.lower() and "input should be" not in said.lower(), said
        assert typed in said, said


# ------------------------------------------------------- the Playback switch
#
# "Make it off by default and through the settings it will be possible to turn
# on this widget and everything it includes, I want it to have an 'Are you sure'
# pane when you turn it on."


def test_the_playback_switch_starts_off_and_asks_nothing(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.record = True  # Playback lives inside the recording settings and only shows with them
    assert tab.show_playback is False
    assert tab.asking_about_playback() is False


def test_turning_playback_on_asks_first_and_does_not_tick_the_box(
    qtbot, tmp_path: Path
) -> None:
    """The tick goes straight back down while the question is up.

    So a man who walks away from the question, or presses Save without answering
    it, has turned nothing on. A box left showing "on" for something that has
    not been agreed to is a promise the console has not kept.
    """
    tab, _ = build(qtbot, tmp_path)
    tab.record = True
    tab._show_playback.click()

    assert tab.asking_about_playback() is True
    assert tab.show_playback is False

    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    assert tab.save() is True
    assert load_settings(tab.settings_path).show_playback is False


def test_answering_yes_turns_it_on_and_saving_writes_it(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.record = True
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab._show_playback.click()
    tab.playback_yes.click()

    assert tab.show_playback is True
    assert tab.asking_about_playback() is False
    assert tab.save() is True
    assert load_settings(tab.settings_path).show_playback is True


def test_answering_no_leaves_it_off_and_takes_the_question_away(
    qtbot, tmp_path: Path
) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.record = True
    tab._show_playback.click()
    tab.playback_no.click()

    assert tab.show_playback is False
    assert tab.asking_about_playback() is False


def test_turning_playback_off_again_asks_nothing(qtbot, tmp_path: Path) -> None:
    """The question is about the way in. Switching it off costs nothing and
    destroys nothing, so asking would be furniture."""
    settings = Settings()
    settings.record = True
    settings.show_playback = True
    settings.camera.streams = [StreamSettings(name="thermal", url="rtsp://10.0.0.2/t")]
    tab, _ = build(qtbot, tmp_path, settings)
    assert tab.show_playback is True

    tab._show_playback.click()
    assert tab.show_playback is False
    assert tab.asking_about_playback() is False
    assert tab.save() is True
    assert load_settings(tab.settings_path).show_playback is False


def test_a_file_with_playback_on_does_not_ask_while_the_form_fills(
    qtbot, tmp_path: Path
) -> None:
    """`toggled` fires when the form fills itself from the file, and a question
    about something nobody did is how a console teaches people to click past
    questions."""
    settings = Settings()
    settings.record = True
    settings.show_playback = True
    tab, _ = build(qtbot, tmp_path, settings)
    assert tab.show_playback is True
    assert tab.asking_about_playback() is False


# ------------------------------------------------------ the name of the place


def test_the_name_is_saved_as_it_was_typed_without_the_spaces(
    qtbot, tmp_path: Path
) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.camera_title = "  ירושלים  "

    assert tab.save() is True
    assert load_settings(tab.settings_path).title == "ירושלים"


def test_the_name_comes_back_onto_the_form(qtbot, tmp_path: Path) -> None:
    settings = Settings()
    settings.title = "השיטה"
    settings.camera.streams = [StreamSettings(name="thermal", url="rtsp://10.0.0.2/t")]
    tab, _ = build(qtbot, tmp_path, settings)
    assert tab.camera_title == "השיטה"


def test_a_save_that_touches_nothing_else_keeps_the_name(qtbot, tmp_path: Path) -> None:
    """Every field this form does not show survives a save because `payload`
    starts from the file. The name is shown, so it has to survive on its own
    account - and the way it would not is somebody correcting a password."""
    settings = Settings()
    settings.title = "ירושלים"
    settings.camera.streams = [StreamSettings(name="thermal", url="rtsp://10.0.0.2/t")]
    tab, _ = build(qtbot, tmp_path, settings)
    tab.camera_password = "changed"

    assert tab.save() is True
    written = load_settings(tab.settings_path)
    assert written.title == "ירושלים"
    assert written.camera.password == "changed"


# ------------------------------------------------------ which screen it opens on


def test_the_screen_starts_at_wherever_it_was_left(qtbot, tmp_path: Path) -> None:
    """A machine with one monitor, or a window somebody has dragged where he
    wants it, must stay where it was put."""
    tab, _ = build(qtbot, tmp_path)
    assert tab.screen is None


def test_the_chosen_screen_is_saved_and_comes_back(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.screen = 2

    assert tab.save() is True
    assert load_settings(tab.settings_path).screen == 2

    again, _ = build(qtbot, tmp_path)
    assert again.screen == 2


def test_a_screen_this_machine_does_not_have_reads_as_wherever_it_was_left(
    qtbot, tmp_path: Path
) -> None:
    """The form says what the console will actually do with it - which is to
    warn and leave the window where it was."""
    settings = Settings()
    settings.screen = 9
    tab, _ = build(qtbot, tmp_path, settings)
    assert tab.screen is None


# --------------------------------------------- how far behind the picture runs


def test_the_delay_starts_at_the_recommended_step(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    assert tab.live_delay_ms == 120


def test_a_chosen_delay_is_saved_and_comes_back(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "rtsp://10.0.0.2/t", True, "auto")])
    tab.live_delay_ms = 50

    assert tab.save() is True
    assert load_settings(tab.settings_path).live_delay_ms == 50

    again, _ = build(qtbot, tmp_path)
    assert again.live_delay_ms == 50


def test_a_delay_that_is_not_one_of_the_steps_lands_on_the_nearest(
    qtbot, tmp_path: Path
) -> None:
    """The file holds a plain integer and this form is four of them. A file
    written by hand must put the box somewhere honest rather than silently at
    the top of the list, which would read as "fastest" for a console running at
    half a second behind."""
    settings = Settings()
    settings.live_delay_ms = 400
    tab, _ = build(qtbot, tmp_path, settings)
    assert tab.live_delay_ms == 300


def test_the_delay_the_operator_can_ask_for_is_one_he_can_steer_through(
    qtbot, tmp_path: Path
) -> None:
    """Every step on this list is a delay somebody could be asked to steer a
    camera through. The model's own ceiling is 2 s and this form does not go
    near it."""
    from vmd.desktop.settings_tab import LIVE_DELAY_CHOICES

    offered = [ms for _words, ms in LIVE_DELAY_CHOICES]
    assert offered == sorted(offered), "the list reads fastest-first"
    assert max(offered) <= 600
    assert min(offered) >= 50, "zero leaves nothing to cover a late packet with"


def test_there_is_no_switch_for_turning_the_picture_upside_down(
    qtbot, tmp_path: Path
) -> None:
    """It was a bench switch - a camera mounted inverted, or a rig on a desk -
    and it is gone at the operator's request now the cameras are up.

    Policed rather than merely deleted: a control taken off a form and left
    in the settings model is a setting somebody turns on in the file one
    afternoon, on a console with no control to turn it off again.
    """
    tab, path = build(qtbot, tmp_path)
    assert not hasattr(tab, "flip_video"), "the flip setting is back"
    for box in tab.findChildren(QCheckBox):
        assert "upside down" not in box.text().lower(), box.text()

    tab.set_streams([("thermal", "rtsp://192.0.2.10/t", True, "auto")])
    assert tab.save() is True, tab.message
    assert "flip_video" not in json.loads(path.read_text(encoding="utf-8"))


def test_the_report_carries_what_detection_is_doing(qtbot, tmp_path: Path) -> None:
    """"It's marking steady and static things." The counters that answer that
    have been written every few seconds since they were added and nothing ever
    read them. The report is the one file that gets sent to somebody who can act
    on it, so it is where they belong."""
    import json

    from vmd.desktop.settings_tab import what_detection_is_doing

    settings = Settings()
    settings.storage.root = tmp_path / "recordings"
    (tmp_path / "recordings").mkdir(parents=True)
    (tmp_path / "recordings" / "detection.json").write_text(
        json.dumps(
            {
                "streams": [
                    {
                        "stream": "thermal",
                        "frames": 90_000,
                        "blobs": 52_000,
                        "rejected": {"too_small": 800},
                        "suppressed": 0,
                        "events": 48_000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    said = "\n".join(what_detection_is_doing(settings, tmp_path / "settings.json"))
    assert "thermal" in said
    assert "52,000" in said
    assert "NEARLY EVERYTHING IS BEING REPORTED" in said


def test_a_report_with_no_detection_file_still_gets_written(qtbot, tmp_path: Path) -> None:
    """Detection off, or a folder that has moved. An ordinary state, and it must
    cost one line rather than the whole report."""
    from vmd.desktop.settings_tab import what_detection_is_doing

    settings = Settings()
    settings.storage.root = tmp_path / "nothing-here"
    said = what_detection_is_doing(settings, tmp_path / "settings.json")
    assert any("nothing has been published" in line for line in said)


# --------------------------------------------------------------------------- #
#  Steering speed
# --------------------------------------------------------------------------- #


def test_the_steering_speed_is_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "192.168.1.250"
    tab.ptz_speed = "fast"

    assert tab.save() is True

    assert load_settings(path).camera.ptz_speed == "fast"


def test_the_form_opens_showing_the_speed_that_was_saved(qtbot, tmp_path: Path) -> None:
    settings = Settings(camera=CameraSettings(host="10.0.0.2", ptz_speed="slow"))
    tab, _path = build(qtbot, tmp_path, settings)

    assert tab.ptz_speed == "slow"


def test_a_form_nobody_touches_keeps_the_speed_it_was_given(
    qtbot, tmp_path: Path
) -> None:
    """Saving the form for some other reason must not quietly reset the camera
    to the middle speed."""
    settings = Settings(camera=CameraSettings(host="10.0.0.2", ptz_speed="slow"))
    tab, path = build(qtbot, tmp_path, settings)

    assert tab.save() is True

    assert load_settings(path).camera.ptz_speed == "slow"


def test_a_speed_the_console_does_not_offer_shows_as_the_normal_one(
    qtbot, tmp_path: Path
) -> None:
    """A hand-edited settings file. The form must show something true rather
    than leave whichever item happened to be selected."""
    tab, _path = build(qtbot, tmp_path)

    tab.ptz_speed = "ludicrous"

    assert tab.ptz_speed == "normal"


def test_every_speed_the_form_offers_is_one_the_settings_accept(
    qtbot, tmp_path: Path
) -> None:
    """The dropdown and the model must not be able to drift apart: an option
    the form offers but the model refuses is a Save that fails on the one
    machine nobody can debug."""
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "10.0.0.2"

    for _label, value in PTZ_SPEED_CHOICES:
        tab.ptz_speed = value
        assert tab.save() is True, f"the form offered {value} and saving it failed"
        assert load_settings(path).camera.ptz_speed == value
