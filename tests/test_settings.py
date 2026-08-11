import json

import pytest

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
