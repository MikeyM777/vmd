"""The settings form: what it loads, what it saves, and what it refuses."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from vmd.desktop.settings_tab import PROBE_NAME, SettingsTab
from vmd.settings import (
    CameraSettings,
    IgnoreRegion,
    Settings,
    StreamSettings,
    load_settings,
    save_settings,
)


def build(qtbot, tmp_path: Path, settings: Settings | None = None):
    path = tmp_path / "settings.json"
    if settings is not None:
        save_settings(settings, path)
    tab = SettingsTab(settings_path=path)
    qtbot.addWidget(tab)
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
    assert tab.streams() == [
        ("IR-ch2", "rtsp://10.0.0.2/ch2", True, "auto"),
        ("day", "rtsp://10.0.0.2/ch0", False, "ffmpeg"),
    ]
    assert [row.name_field.text() for row in tab.stream_rows()] == ["IR-ch2", "day"]


def test_a_stream_added_in_the_window_is_the_stream_that_is_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    qtbot.mouseClick(tab.add_stream_button, Qt.MouseButton.LeftButton)

    row = tab.stream_rows()[-1]
    qtbot.keyClicks(row.name_field, "IR-ch2")
    qtbot.keyClicks(row.url_field, "rtsp://10.0.0.2/ch2")
    row.reader_field.setCurrentText("ffmpeg")

    assert tab.streams() == [("IR-ch2", "rtsp://10.0.0.2/ch2", True, "ffmpeg")]
    assert tab.save() is True

    stored = load_settings(path).camera.streams
    assert [(s.name, s.url, s.enabled, s.reader) for s in stored] == [
        ("IR-ch2", "rtsp://10.0.0.2/ch2", True, "ffmpeg")
    ]


def test_unticking_record_is_saved_rather_than_deleting_the_stream(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams([("thermal", "rtsp://a/1", True, "auto")])
    tab.stream_rows()[0].record_field.setChecked(False)
    assert tab.save() is True

    stored = load_settings(path).camera.streams
    assert len(stored) == 1 and stored[0].enabled is False


def test_removing_a_row_removes_that_stream_and_leaves_the_rest(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.set_streams(
        [("thermal", "rtsp://a/1", True, "auto"), ("day", "rtsp://a/2", True, "auto")]
    )
    qtbot.mouseClick(tab.stream_rows()[0].remove_button, Qt.MouseButton.LeftButton)

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
    settings.video_mode = "mp4"
    settings.target_distance_m = 1200.0
    settings.storage.segment_seconds = 120

    tab, path = build(qtbot, tmp_path, settings)
    assert tab.save() is True

    stored = load_settings(path)
    assert stored.bitrate.ceiling_kbps == 4200
    assert stored.video_mode == "mp4"
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


def test_the_detection_choices_on_screen_are_the_ones_from_the_file(
    qtbot, tmp_path: Path
) -> None:
    settings = _watched(
        detect=True,
        thermal=True,
        classify=True,
        sensitivity="high",
        horizon_y=340,
        ignore_regions=[IgnoreRegion(x=10, y=20, w=30, h=40)],
    )
    tab, _ = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]

    assert row.detect_field.isChecked() is True
    assert row.thermal_field.isChecked() is True
    assert row.classify() is True
    assert row.sensitivity() == "high"
    assert row.horizon() == 340
    assert row.horizon_enabled_field.isChecked() is True
    assert row.regions() == [(10, 20, 30, 40)]


def test_ticking_detect_and_thermal_is_what_reaches_the_file(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    row.detect_field.setChecked(True)
    row.thermal_field.setChecked(True)
    row.set_sensitivity("low")
    assert tab.save() is True

    stored = load_settings(path).camera.streams[0]
    assert stored.detect is True
    assert stored.thermal is True
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


# --- the three states of `classify` -----------------------------------------
#
# None means "follow the sensor" and is the default. A two-state checkbox cannot
# say it, and collapsing it to False would quietly turn the classifier off on the
# visible head, where it is the whole point of having one.


def test_classify_offers_three_states_and_all_three_round_trip(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    offered = [row.classify_field.itemData(i) for i in range(row.classify_field.count())]
    assert offered == [None, True, False]

    for choice in (None, True, False):
        row.set_classify(choice)
        assert row.classify() is choice
        assert tab.save() is True
        assert load_settings(path).camera.streams[0].classify is choice


def test_follow_the_sensor_is_the_state_a_new_stream_starts_in(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    qtbot.mouseClick(tab.add_stream_button, Qt.MouseButton.LeftButton)
    row = tab.stream_rows()[-1]
    qtbot.keyClicks(row.name_field, "day")
    qtbot.keyClicks(row.url_field, "rtsp://10.0.0.2/ch0")

    assert row.classify() is None
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].classify is None


def test_the_classify_control_says_what_it_means_in_plain_words(qtbot, tmp_path: Path) -> None:
    """The operator is not technical and will never read the spec. Whatever the
    wording, it has to name the three choices without an acronym in sight."""
    tab, _ = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    labels = [row.classify_field.itemText(i) for i in range(row.classify_field.count())]
    assert all(label.strip() for label in labels)
    assert len(set(labels)) == 3
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor")
    for text in labels + [row.classify_field.toolTip()]:
        assert not any(word in text.lower() for word in banned), text
    # Why the thermal head is different has to be on screen, not in a document -
    # and on both controls, because a tooltip is only read by whoever hovers
    # over that one control.
    for told in (row.classify_field.toolTip(), row.thermal_field.toolTip()):
        assert "700" in told and "13" in told, told


# --- the horizon -------------------------------------------------------------


def test_the_horizon_is_off_until_it_is_turned_on(qtbot, tmp_path: Path) -> None:
    """A wrong horizon deletes real detections and says nothing, so a number
    typed into the box means nothing until the operator ticks it on."""
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    assert row.horizon_enabled_field.isChecked() is False
    row.horizon_field.setValue(340)
    assert row.horizon() is None
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].horizon_y is None


def test_turning_the_horizon_on_saves_the_pixel_row(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    row.horizon_enabled_field.setChecked(True)
    row.horizon_field.setValue(340)
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].horizon_y == 340


def test_turning_the_horizon_off_again_disables_the_rule(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched(horizon_y=340))
    row = tab.stream_rows()[0]
    row.horizon_enabled_field.setChecked(False)
    assert tab.save() is True
    assert load_settings(path).camera.streams[0].horizon_y is None


def test_the_horizon_control_says_what_the_number_is(qtbot, tmp_path: Path) -> None:
    """A bare number box is useless: nobody knows whether 340 is metres, degrees
    or a row of pixels, and the picture is not on screen to check against."""
    tab, _ = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    # On the box itself, not only in a tooltip nobody hovers over: the number
    # is meaningless without its unit and its direction.
    beside_the_box = row.horizon_field.suffix().lower()
    assert "top" in beside_the_box, beside_the_box
    assert any(word in beside_the_box for word in ("dot", "pixel")), beside_the_box
    # And the longer warning, wherever it is written.
    told = (row.horizon_field.toolTip() + row.horizon_enabled_field.toolTip()).lower()
    assert "sky" in told, told


# --- ignore regions ----------------------------------------------------------


def test_the_regions_already_in_the_file_are_listed(qtbot, tmp_path: Path) -> None:
    settings = _watched(
        ignore_regions=[IgnoreRegion(x=1, y=2, w=3, h=4), IgnoreRegion(x=5, y=6, w=7, h=8)]
    )
    tab, _ = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]
    assert row.regions() == [(1, 2, 3, 4), (5, 6, 7, 8)]
    assert row.regions_list.count() == 2


def test_a_region_typed_in_is_added_and_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    row.region_x.setValue(100)
    row.region_y.setValue(200)
    row.region_w.setValue(50)
    row.region_h.setValue(60)
    qtbot.mouseClick(row.add_region_button, Qt.MouseButton.LeftButton)

    assert row.regions() == [(100, 200, 50, 60)]
    assert tab.save() is True
    stored = load_settings(path).camera.streams[0].ignore_regions
    assert [r.as_tuple() for r in stored] == [(100, 200, 50, 60)]


def test_a_region_with_no_area_is_refused_rather_than_written(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    row.region_x.setValue(10)
    row.region_y.setValue(10)
    row.region_w.setValue(0)
    row.region_h.setValue(0)
    qtbot.mouseClick(row.add_region_button, Qt.MouseButton.LeftButton)
    assert row.regions() == []
    assert tab.message


def test_a_wrong_region_can_be_deleted(qtbot, tmp_path: Path) -> None:
    """An operator who has a region in the wrong place needs a way out of it,
    and hand-editing the file is not one."""
    settings = _watched(
        ignore_regions=[IgnoreRegion(x=1, y=2, w=3, h=4), IgnoreRegion(x=5, y=6, w=7, h=8)]
    )
    tab, path = build(qtbot, tmp_path, settings)
    row = tab.stream_rows()[0]
    row.regions_list.setCurrentRow(0)
    qtbot.mouseClick(row.remove_region_button, Qt.MouseButton.LeftButton)

    assert row.regions() == [(5, 6, 7, 8)]
    assert tab.save() is True
    stored = load_settings(path).camera.streams[0].ignore_regions
    assert [r.as_tuple() for r in stored] == [(5, 6, 7, 8)]


def test_the_regions_control_says_what_a_region_is_for(qtbot, tmp_path: Path) -> None:
    tab, _ = build(qtbot, tmp_path, _watched())
    row = tab.stream_rows()[0]
    told = (row.regions_help.text() + row.regions_list.toolTip()).lower()
    assert "tree" in told, told


# --- the global block --------------------------------------------------------


def test_the_global_detection_switches_are_on_screen_and_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.detection_enabled = False
    tab.detection_classify = True
    tab.min_travel_px = "12"
    assert tab.save() is True

    stored = load_settings(path).detection
    assert stored.enabled is False
    assert stored.classify is True
    assert stored.min_travel_px == 12.0


def test_the_global_detection_block_on_screen_is_the_one_from_the_file(
    qtbot, tmp_path: Path
) -> None:
    """A form that shows a switch as on while the file says off is worse than no
    switch: the operator turns detection off and it comes back at the next save."""
    settings = Settings()
    settings.detection.enabled = False
    settings.detection.classify = True
    tab, path = build(qtbot, tmp_path, settings)

    assert tab.detection_enabled is False
    assert tab.detection_classify is True
    assert tab.save() is True

    stored = load_settings(path).detection
    assert stored.enabled is False
    assert stored.classify is True


def test_a_blank_minimum_travel_follows_the_preset(qtbot, tmp_path: Path) -> None:
    settings = Settings()
    settings.detection.min_travel_px = 20.0
    tab, path = build(qtbot, tmp_path, settings)
    assert tab.min_travel_px == "20.0"
    tab.min_travel_px = ""
    assert tab.save() is True
    assert load_settings(path).detection.min_travel_px is None


def test_a_minimum_travel_the_model_rejects_is_reported_not_swallowed(
    qtbot, tmp_path: Path
) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.min_travel_px = "-3"
    assert tab.save() is False
    assert "travel" in tab.message.lower()


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
                "video_mode": "mp4",
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
    assert stored.video_mode == "mp4"
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
    return {
        s.name: (
            s.detect,
            s.thermal,
            s.classify,
            s.sensitivity,
            s.horizon_y,
            tuple(r.as_tuple() for r in s.ignore_regions),
        )
        for s in load_settings(path).camera.streams
    }


def test_removing_a_row_does_not_move_detection_onto_another_stream(
    qtbot, tmp_path: Path
) -> None:
    """Attaching the thermal flag to the wrong head is this form's version of the
    bug that once deleted the operator's streams."""
    tab, path = build(qtbot, tmp_path, _three_streams())
    qtbot.mouseClick(tab.stream_rows()[0].remove_button, Qt.MouseButton.LeftButton)
    assert tab.save() is True

    after = _detection_of(path)
    assert list(after) == ["two", "three"]
    assert after["two"] == (True, False, True, "high", 200, ((2, 2, 2, 2),))
    assert after["three"] == (False, False, None, "normal", None, ())


def test_adding_a_row_does_not_disturb_the_streams_already_there(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path, _three_streams())
    before = _detection_of(path)
    qtbot.mouseClick(tab.add_stream_button, Qt.MouseButton.LeftButton)
    row = tab.stream_rows()[-1]
    qtbot.keyClicks(row.name_field, "four")
    qtbot.keyClicks(row.url_field, "rtsp://a/4")
    row.thermal_field.setChecked(True)
    assert tab.save() is True

    after = _detection_of(path)
    assert list(after) == ["one", "two", "three", "four"]
    for name in ("one", "two", "three"):
        assert after[name] == before[name]
    assert after["four"] == (False, True, None, "normal", None, ())


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
    tab.storage_root = str(root)

    assert tab.save() is False
    assert "written" in tab.message.lower()
    assert str(root) in tab.message
