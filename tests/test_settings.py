import json

import pytest

from pydantic import ValidationError

from vmd.settings import (
    Settings,
    SettingsError,
    detect_free_bytes,
    load_settings,
    save_settings,
)


def test_missing_file_yields_defaults(tmp_path):
    settings = load_settings(tmp_path / "nope.json")
    assert isinstance(settings, Settings)
    assert settings.storage.budget_gb == 100.0
    assert settings.storage.budget_enabled is True
    assert settings.storage.retention_days is None
    assert settings.target_distance_m == 700.0


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.camera.host = "192.168.1.50"
    settings.camera.username = "admin"
    settings.storage.budget_gb = 600.0
    settings.storage.retention_days = 13
    save_settings(settings, path)

    loaded = load_settings(path)
    assert loaded.camera.host == "192.168.1.50"
    assert loaded.camera.username == "admin"
    assert loaded.storage.budget_gb == 600.0
    assert loaded.storage.retention_days == 13


def test_streams_are_loaded(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "camera": {
                    "host": "10.0.0.2",
                    "streams": [
                        {"name": "thermal", "url": "rtsp://10.0.0.2/thermal"},
                        {"name": "visible", "url": "rtsp://10.0.0.2/visible", "enabled": False},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert [s.name for s in settings.camera.streams] == ["thermal", "visible"]
    assert settings.camera.streams[0].enabled is True
    assert settings.camera.streams[1].enabled is False


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="could not be read"):
        load_settings(path)


def test_zero_budget_rejected(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"storage": {"budget_gb": 0}}), encoding="utf-8")
    with pytest.raises(SettingsError, match="budget_gb"):
        load_settings(path)


def test_budget_bytes_conversion():
    settings = Settings()
    settings.storage.budget_gb = 2.0
    assert settings.storage.budget_bytes == 2 * 1024**3


def test_detect_free_bytes_on_real_path(tmp_path):
    free = detect_free_bytes(tmp_path)
    assert free is not None
    assert free > 0


def test_detect_free_bytes_returns_none_for_bad_path(tmp_path):
    assert detect_free_bytes(tmp_path / "does" / "not" / "exist") is None


def test_a_relative_recording_folder_is_anchored_to_the_settings_file(tmp_path, monkeypatch):
    """Three processes read this file; all three must reach the same folder.

    `root` defaults to the relative "recordings", so a console or a recorder
    started from anywhere but the project directory would quietly fill a second
    tree beside whatever the shell happened to be sitting in - and the operator
    would have no way to find the footage that went into it.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "settings.json").write_text(
        json.dumps({"storage": {"root": "recordings"}}), encoding="utf-8"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    settings = load_settings(project / "settings.json")
    assert settings.storage.root.is_absolute()
    assert settings.storage.root == (project / "recordings").resolve()


def test_an_absolute_recording_folder_is_left_exactly_as_chosen(tmp_path):
    chosen = tmp_path / "D_drive" / "footage"
    (tmp_path / "settings.json").write_text(
        json.dumps({"storage": {"root": str(chosen)}}), encoding="utf-8"
    )
    assert load_settings(tmp_path / "settings.json").storage.root == chosen


def test_a_first_run_with_no_file_still_gets_an_absolute_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(tmp_path / "nothing-here.json")
    assert settings.storage.root == (tmp_path / "recordings").resolve()


# --------------------------------------------------------------------------
# Numbers that parse, and mean nothing
# --------------------------------------------------------------------------


def test_a_disk_warning_that_can_never_fire_is_refused():
    """`warn_at_fraction` above 1 is a warning that is switched off in disguise.

    The rule is `used >= warn_at_fraction * budget`, and used can never exceed
    the budget the retention sweep keeps it under. Above 1 the warning never
    fires - so the one thing that tells the operator the disk is filling is
    gone, and the settings file it went missing in looks perfectly normal.
    """
    from vmd.settings import StorageSettings

    with pytest.raises(ValidationError):
        StorageSettings(warn_at_fraction=1.5)
    with pytest.raises(ValidationError):
        StorageSettings(warn_at_fraction=0.0)
    assert StorageSettings(warn_at_fraction=1.0).warn_at_fraction == 1.0


def test_a_segment_length_of_zero_is_refused():
    """It is handed to ffmpeg as -segment_time, where it is not a length at all."""
    from vmd.settings import StorageSettings

    with pytest.raises(ValidationError):
        StorageSettings(segment_seconds=0)
    with pytest.raises(ValidationError):
        StorageSettings(segment_seconds=-60)


def test_a_bitrate_floor_above_the_ceiling_is_refused():
    """The pair has to be readable as a range or neither number means anything.

    The ceiling is what every encoder on the camera is capped to fit inside.
    A floor above it is two instructions that cannot both be obeyed, and
    nothing downstream would say which one it chose.
    """
    from vmd.settings import BitrateSettings

    with pytest.raises(ValidationError):
        BitrateSettings(floor_kbps=6000, ceiling_kbps=5000)
    with pytest.raises(ValidationError):
        BitrateSettings(ceiling_kbps=0)


def test_two_streams_with_the_same_name_are_refused():
    """Nothing downstream can tell them apart, and none of it says so.

    Every consumer keys on the name: go2rtc serves one stream under it, the
    recorder files segments under it, the detector attributes events to it and
    the Live tab picks a view by it. Two streams called ch1 means the second
    camera's footage and the second camera's events are filed as the first
    camera's, and the operator is looking at a perimeter that is not the one
    on the screen.
    """
    from vmd.settings import CameraSettings, StreamSettings

    with pytest.raises(ValidationError) as raised:
        CameraSettings(
            streams=[
                StreamSettings(name="ch1", url="rtsp://10.0.0.2/one"),
                StreamSettings(name="ch1", url="rtsp://10.0.0.2/two"),
            ]
        )
    assert "ch1" in str(raised.value)


def test_a_settings_file_with_a_meaningless_number_says_which_one(tmp_path):
    """Loading has to fail with a sentence, not a traceback: nothing restarts
    the detector on its own, and the operator has no terminal."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"storage": {"warn_at_fraction": 4.0}}),
        encoding="utf-8",
    )
    with pytest.raises(SettingsError) as raised:
        load_settings(path)
    assert "warn_at_fraction" in str(raised.value)


def test_a_stream_with_no_name_is_refused():
    """The name is an identifier, not a caption.

    It is the go2rtc stream id, the folder recordings are filed under, the
    value events are attributed to and the one `wall_view` remembers. An empty
    one is a stream nothing downstream can address, and every one of those
    consumers would carry on without a word.
    """
    from vmd.settings import StreamSettings

    with pytest.raises(ValidationError):
        StreamSettings(name="", url="rtsp://10.0.0.2/ch1")
    with pytest.raises(ValidationError):
        StreamSettings(name="   ", url="rtsp://10.0.0.2/ch1")


def test_a_delay_nobody_could_steer_through_is_refused() -> None:
    """It was 5000 when nothing read it, which cost nothing. Now libVLC is
    handed it, and a picture five seconds behind the world is a camera that gets
    driven past whatever it is being aimed at, every time."""
    import pytest
    from pydantic import ValidationError

    from vmd.settings import Settings

    with pytest.raises(ValidationError):
        Settings(live_delay_ms=2001)
    with pytest.raises(ValidationError):
        Settings(live_delay_ms=-1)
    assert Settings(live_delay_ms=0).live_delay_ms == 0
    assert Settings(live_delay_ms=2000).live_delay_ms == 2000


def test_a_settings_file_naming_the_old_video_fields_still_loads(tmp_path) -> None:
    """`video_mode` and `video_buffer_ms` were written for a browser console
    that no longer exists, and every settings file in the field has both. They
    are gone from the model; a file that has them must still open, because the
    only tool for fixing that file is inside the console it would have stopped
    from starting."""
    import json

    from vmd.settings import load_settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"video_mode": "mp4", "video_buffer_ms": 500, "title": "ירושלים"}),
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.title == "ירושלים"
    assert settings.live_delay_ms == 120, "the delay is the new field's default"


# ---------------------------------------------- how many cameras share one radio
#
# "The FLIR sends 2.5 Mbps and multiply it by 2 because there are 2 cameras."
# There is one radio and there are two consoles on it. Each holds a ceiling
# meaning "how much of the link the video may use", so two consoles each
# spending the whole link is twice the link - and each then sees the radio full
# and turns its own camera down, for ever, on a link that was never the problem.


def _camera(root, label: str, radio_host: str) -> "Path":
    import json
    from pathlib import Path

    folder = Path(root) / "cameras" / label
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "settings.json"
    path.write_text(
        json.dumps({"radio": {"host": radio_host}}), encoding="utf-8"
    )
    return path


def test_one_camera_beside_the_program_is_alone_on_its_link(tmp_path) -> None:
    """Every installation before today, and every single-camera one after it."""
    from vmd.settings import Settings, consoles_on_this_radio

    settings = Settings()
    settings.radio.host = "192.168.1.20"
    assert consoles_on_this_radio(tmp_path / "settings.json", settings) == 1


def test_two_cameras_on_one_radio_are_counted(tmp_path) -> None:
    from vmd.settings import Settings, consoles_on_this_radio

    mine = _camera(tmp_path, "250", "192.168.1.20")
    _camera(tmp_path, "251", "192.168.1.20")

    settings = Settings()
    settings.radio.host = "192.168.1.20"
    assert consoles_on_this_radio(mine, settings) == 2


def test_a_camera_on_a_different_radio_is_not_counted(tmp_path) -> None:
    """Two radios is two links, and dividing one by the other's cameras would
    hold a picture down for a reason that is not there."""
    from vmd.settings import Settings, consoles_on_this_radio

    mine = _camera(tmp_path, "250", "192.168.1.20")
    _camera(tmp_path, "251", "10.0.0.5")

    settings = Settings()
    settings.radio.host = "192.168.1.20"
    assert consoles_on_this_radio(mine, settings) == 1


def test_a_neighbour_whose_settings_will_not_parse_is_skipped(tmp_path) -> None:
    """Miscounting downward costs picture quality; miscounting upward costs the
    link. Neither is worth taking on the strength of somebody else's broken
    file, so it is skipped rather than guessed at."""
    from vmd.settings import Settings, consoles_on_this_radio

    mine = _camera(tmp_path, "250", "192.168.1.20")
    (tmp_path / "cameras" / "252").mkdir(parents=True)
    (tmp_path / "cameras" / "252" / "settings.json").write_text("{ not json", encoding="utf-8")

    settings = Settings()
    settings.radio.host = "192.168.1.20"
    assert consoles_on_this_radio(mine, settings) == 1


def test_a_console_with_no_radio_is_never_divided(tmp_path) -> None:
    """No radio means no airtime reading and no loop. Dividing a ceiling by the
    number of folders next door would take a picture away for nothing."""
    from vmd.settings import Settings, consoles_on_this_radio

    mine = _camera(tmp_path, "250", "")
    _camera(tmp_path, "251", "")
    assert consoles_on_this_radio(mine, Settings()) == 1


def test_a_settings_file_outside_a_cameras_folder_counts_nobody(tmp_path) -> None:
    """A folder of backups beside a settings file must not divide the link by
    six. Only the layout cameras.bat makes counts."""
    import json

    from vmd.settings import Settings, consoles_on_this_radio

    folder = tmp_path / "somewhere" / "250"
    folder.mkdir(parents=True)
    path = folder / "settings.json"
    path.write_text(json.dumps({"radio": {"host": "192.168.1.20"}}), encoding="utf-8")
    (tmp_path / "somewhere" / "251").mkdir()
    (tmp_path / "somewhere" / "251" / "settings.json").write_text(
        json.dumps({"radio": {"host": "192.168.1.20"}}), encoding="utf-8"
    )

    settings = Settings()
    settings.radio.host = "192.168.1.20"
    assert consoles_on_this_radio(path, settings) == 1
