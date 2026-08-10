"""The settings form: what it loads, what it saves, and what it refuses."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from vmd.desktop.settings_tab import SettingsTab
from vmd.settings import CameraSettings, Settings, StreamSettings, load_settings, save_settings


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
    settings = Settings(camera=CameraSettings(host="10.0.0.2"))
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
