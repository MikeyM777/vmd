"""The settings form: what it loads, what it saves, and what it refuses."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

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
    # Both come back as used. There is no longer a switch for "use this view" -
    # a line on this list IS a view in use - so a file that had one switched off
    # is adopted rather than left in a state the operator cannot reach. The
    # reader, which is also off the screen now, is carried across untouched.
    assert tab.streams() == [
        ("IR-ch2", "rtsp://10.0.0.2/ch2", True, "auto"),
        ("day", "rtsp://10.0.0.2/ch0", True, "ffmpeg"),
    ]
    assert [row.name_field.text() for row in tab.stream_rows()] == ["IR-ch2", "day"]


def test_a_stream_added_in_the_window_is_the_stream_that_is_saved(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    qtbot.mouseClick(tab.add_stream_button, Qt.MouseButton.LeftButton)

    row = tab.stream_rows()[-1]
    qtbot.keyClicks(row.name_field, "IR-ch2")
    qtbot.keyClicks(row.url_field, "rtsp://10.0.0.2/ch2")

    assert tab.streams() == [("IR-ch2", "rtsp://10.0.0.2/ch2", True, "auto")]
    assert tab.save() is True

    stored = load_settings(path).camera.streams
    assert [(s.name, s.url, s.enabled, s.reader) for s in stored] == [
        ("IR-ch2", "rtsp://10.0.0.2/ch2", True, "auto")
    ]


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
    assert "remove" in said, said
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
    message line names the view and says what happens at the next Save, and
    points at Remove for the operator who really did mean it off.
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
    assert "remove" in tab.message.lower(), tab.message

    assert tab.save() is True
    assert [s.enabled for s in load_settings(path).camera.streams] == [True, True]


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
            row.thermal_field,
            row.details_button,
            row.remove_button,
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
    row.details_button.setChecked(True)
    tab.show()
    tab.setGeometry(0, 0, FORM_MAX_WIDTH, 1400)
    QApplication.processEvents()

    cut = [
        (control.text(), control.width(), control.minimumSizeHint().width())
        for control in (
            row.pick_button,
            row.add_region_button,
            row.remove_region_button,
            row.horizon_enabled_field,
            row.region_x,
            row.region_y,
            row.region_w,
            row.region_h,
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

    return Settings(storage=StorageSettings(root=root, budget_gb=budget_gb))


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

JARGON = ("yolo", "cnn", "classifier", "inference", "model", "sensor", "pixel")


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
    """"Too much going on." Seven controls per camera view, six of which mean
    nothing until the first one is switched on.

    Folded away, never deleted: he has said he wants to test movement detection
    in the next days, so every one of these has to be there the moment he ticks
    the box.
    """
    tab, _ = build(qtbot, tmp_path, _watched())
    tab.show()
    QApplication.processEvents()
    row = tab.stream_rows()[0]

    hidden = (
        row.thermal_field,
        row.classify_field,
        row.sensitivity_field,
        row.details_button,
    )
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
    assert stored.thermal is True, "the heat flag was lost when the box folded"
    assert stored.sensitivity == "high"


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
    assert row.detect_field.text() == "Watch for movement"

    said = tab.detect_help.text().lower()
    assert said.strip(), "the switch still explains itself only on hover"
    assert "move" in said, said
    # The two things he would actually notice: a line in the movement list, and
    # the red strip across the pictures.
    assert "strip" in said or "red" in said, said
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


def test_nothing_explains_a_control_that_is_not_on_the_screen(
    qtbot, tmp_path: Path
) -> None:
    """Moving the two duplicated paragraphs above the cards stopped them being
    printed twice. It did not stop them being printed at all - and with nothing
    watched they explain controls that are folded away, so a console nobody has
    set up yet opens with three paragraphs of preamble before the first box, on
    the tab whose complaint was that there is too much on it.

    They come back the moment there is something for them to be about.
    """
    tab, _ = build(qtbot, tmp_path, _watched(detect=False))
    # `isVisibleTo` and never `isVisible`: the tab has not been shown, so
    # `isVisible` is False for everything on it and the assertion would pass
    # whatever the code did. A mutation caught exactly that.
    assert not tab.classify_help.isVisibleTo(tab)
    assert not tab.ignore_help.isVisibleTo(tab)
    # The tick's own sentence stays: that tick is always on the screen, and it
    # is the one he asked about by name.
    assert tab.detect_help.isVisibleTo(tab)

    tab.stream_rows()[0].detect_field.setChecked(True)
    assert tab.classify_help.isVisibleTo(tab)
    assert tab.ignore_help.isVisibleTo(tab)


def test_the_naming_control_is_not_called_name_what_moved(qtbot, tmp_path: Path) -> None:
    """"'Name what moved' - what is that?" It reads as an instruction to the
    operator - go and name it - rather than as something the software attempts.
    """
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    row = tab.stream_rows()[0]
    assert row.classify_label.text().rstrip(":") == "Try to say what it was"
    said = tab.classify_help.text().lower()
    assert said.strip()
    # The three things it might say, so the words themselves say what "it" is.
    for example in ("person", "vehicle", "animal"):
        assert example in said, said
    # And the two things about it that matter more than the guess: it is a guess,
    # and it never decides whether anything is recorded or reported.
    assert "guess" in said, said
    assert "record" in said, said
    assert not any(word in said for word in JARGON), said
    _said_once(tab, said)


def test_the_ignore_control_says_it_is_about_parts_of_the_picture(
    qtbot, tmp_path: Path
) -> None:
    """"'Skyline and ignore...' - what is that?" Two nouns from the source code
    joined by an "and", naming neither what it is for nor what it acts on."""
    tab, _ = build(qtbot, tmp_path, _two_watched_views())
    row = tab.stream_rows()[0]
    assert row.details_button.text() == "Ignore parts of the picture"
    said = tab.ignore_help.text().lower()
    assert said.strip()
    for example in ("sky", "road", "tree"):
        assert example in said, said
    assert "wind" in said, said
    assert "not" in said and "report" in said, said
    assert not any(word in said for word in JARGON), said
    _said_once(tab, said)


def test_the_camera_tools_box_says_it_is_for_checking_the_camera(
    qtbot, tmp_path: Path
) -> None:
    """"The camera - is it relevant anymore?" It is: on a machine with no
    terminal it is the only way to find out whether the camera answers at all.
    What it was missing is a title saying that, next to a box already called
    Camera one screen above."""
    tab, _ = build(qtbot, tmp_path)
    assert tab.tools_box.title() == "Check the camera"


# ---------------------------------------------------------------- the storage
#
# "I want a button that scans the PC storage situation and then automatically
# adjusts the parameters like the budget, delete older than and so on. Make it
# nicer and easier, like a slider for the budget. If the user wants, he can edit."


def a_drive(total_gb: float, free_gb: float):
    """A drive of a stated size, in the shape shutil.disk_usage answers in."""
    from types import SimpleNamespace

    total = int(total_gb * 1024**3)
    free = int(free_gb * 1024**3)
    return lambda path: SimpleNamespace(total=total, used=total - free, free=free)


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


def test_typing_a_budget_bigger_than_the_slider_is_not_thrown_away(
    qtbot, tmp_path: Path
) -> None:
    """The slider has to end somewhere and a drive does not have to agree with
    where. The box is the setting; the slider is a way of moving it."""
    tab, path = build(qtbot, tmp_path)
    wanted = tab.budget_slider.maximum() + 4000
    tab.budget_gb = str(wanted)
    assert tab.save() is True, tab.message
    assert load_settings(path).storage.budget_gb == float(wanted)


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
        ("min_travel_px", "a lot", "Must travel at least (dots)"),
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
