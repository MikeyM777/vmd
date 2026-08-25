"""Choosing between the two cameras, and deriving one from the other.

The fault this exists to remove is a real one: an address lives in three places
- the camera's own, and the URL of each picture - and changing two of the three
gave a console that showed a picture it could not steer. Everything below is
about those three staying in step.
"""

from __future__ import annotations

from pathlib import Path

from vmd.desktop.presets import (
    Preset,
    current,
    derive,
    install_root,
    last_part,
    others,
    presets,
    suggested_host,
    swap_address,
    write_preset,
)
from vmd.settings import CameraSettings, Settings, StreamSettings, load_settings


def a_camera(host: str = "192.168.1.250") -> Settings:
    return Settings(
        title="ירושלים",
        camera=CameraSettings(
            host=host,
            username="admin",
            password="p@ss",
            streams=[
                StreamSettings(name="thermal", url=f"rtsp://{host}:554/ch2", enabled=True),
                StreamSettings(name="visible", url=f"rtsp://{host}:554/ch0", enabled=True),
            ],
        ),
    )


# --------------------------------------------------------------------------- #
#  The three addresses
# --------------------------------------------------------------------------- #


def test_the_other_camera_gets_every_address_changed_at_once() -> None:
    """The whole point. Two of three right is the bug being removed."""
    other = derive(a_camera(), "192.168.1.251")

    assert other.camera.host == "192.168.1.251"
    assert other.camera.streams[0].url == "rtsp://192.168.1.251:554/ch2"
    assert other.camera.streams[1].url == "rtsp://192.168.1.251:554/ch0"


def test_nothing_but_the_address_is_carried_over_changed() -> None:
    """The login, the stream names and paths and everything about how this site
    is set up belong to the site, not to which of its cameras is being watched."""
    other = derive(a_camera(), "192.168.1.251")

    assert other.camera.username == "admin"
    assert other.camera.password == "p@ss"
    assert [stream.name for stream in other.camera.streams] == ["thermal", "visible"]
    assert other.camera.streams[0].url.endswith("/ch2")


def test_deriving_does_not_touch_the_camera_it_was_derived_from() -> None:
    mine = a_camera()

    derive(mine, "192.168.1.251")

    assert mine.camera.host == "192.168.1.250"
    assert mine.camera.streams[0].url == "rtsp://192.168.1.250:554/ch2"


def test_an_address_inside_a_url_with_a_login_is_still_replaced() -> None:
    assert (
        swap_address("rtsp://admin:pw@192.168.1.250:554/ch0", "192.168.1.251")
        == "rtsp://admin:pw@192.168.1.251:554/ch0"
    )


def test_a_url_with_no_address_in_it_is_left_alone() -> None:
    """A camera reached by name. Rewriting it would break a working stream."""
    assert swap_address("rtsp://gate-camera/ch0", "192.168.1.251") == "rtsp://gate-camera/ch0"


def test_a_stream_with_no_url_yet_stays_empty() -> None:
    settings = a_camera()
    settings.camera.streams[1].url = ""

    other = derive(settings, "192.168.1.251")

    assert other.camera.streams[1].url == ""


# --------------------------------------------------------------------------- #
#  Naming
# --------------------------------------------------------------------------- #


def test_a_camera_is_called_by_the_last_part_of_its_address() -> None:
    assert last_part("192.168.1.250") == "250"
    assert last_part("10.0.0.7") == "7"


def test_a_camera_reached_by_name_keeps_that_name() -> None:
    assert last_part("gate-camera") == "gate-camera"


def test_the_next_address_up_is_suggested_for_the_second_camera() -> None:
    assert suggested_host(a_camera("192.168.1.250"), []) == "192.168.1.251"


def test_an_address_already_set_up_is_not_suggested_again() -> None:
    taken = [Preset(name="251", settings_path=Path("x"), title="")]
    assert suggested_host(a_camera("192.168.1.250"), taken) == "192.168.1.249"


def test_nothing_is_suggested_for_a_camera_with_no_address() -> None:
    assert suggested_host(a_camera(""), []) == ""


# --------------------------------------------------------------------------- #
#  Finding what is set up
# --------------------------------------------------------------------------- #


def an_install(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "VMD.bat").write_text("", encoding="utf-8")
    return root


def test_the_install_is_found_from_a_camera_settings_file(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    settings_file = root / "cameras" / "250" / "settings.json"
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{}", encoding="utf-8")

    assert install_root(settings_file) == root.resolve()


def test_a_settings_file_outside_any_install_finds_nothing(tmp_path: Path) -> None:
    stray = tmp_path / "somewhere" / "settings.json"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}", encoding="utf-8")

    assert install_root(stray) is None


def test_the_cameras_that_are_set_up_are_offered_in_order(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    write_preset(root, "251", a_camera("192.168.1.251"))
    write_preset(root, "250", a_camera("192.168.1.250"))

    found = presets(root)

    assert [preset.name for preset in found] == ["250", "251"]
    assert found[0].title == "ירושלים"


def test_a_folder_without_settings_is_not_a_camera(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    write_preset(root, "250", a_camera())
    (root / "cameras" / "notes").mkdir()

    assert [preset.name for preset in presets(root)] == ["250"]


def test_an_install_with_no_cameras_offers_none(tmp_path: Path) -> None:
    assert presets(an_install(tmp_path / "VMD")) == []


def test_a_console_knows_which_camera_it_is(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")
    write_preset(root, "250", a_camera("192.168.1.250"))
    write_preset(root, "251", a_camera("192.168.1.251"))
    found = presets(root)

    mine = current(root / "cameras" / "251" / "settings.json", found)

    assert mine is not None and mine.name == "251"
    assert [preset.name for preset in others(root / "cameras" / "251" / "settings.json", found)] == [
        "250"
    ]


def test_a_console_on_the_root_settings_is_not_one_of_them(tmp_path: Path) -> None:
    """The ordinary state of an install that has never had a second camera."""
    root = an_install(tmp_path / "VMD")
    write_preset(root, "250", a_camera())

    assert current(root / "settings.json", presets(root)) is None


# --------------------------------------------------------------------------- #
#  Writing one
# --------------------------------------------------------------------------- #


def test_a_written_preset_can_be_loaded_back(tmp_path: Path) -> None:
    root = an_install(tmp_path / "VMD")

    path = write_preset(root, "251", derive(a_camera(), "192.168.1.251"))

    loaded = load_settings(path)
    assert loaded.camera.host == "192.168.1.251"
    assert loaded.camera.streams[0].url == "rtsp://192.168.1.251:554/ch2"


def test_setting_up_a_camera_twice_never_overwrites_the_first(tmp_path: Path) -> None:
    """That folder holds its own detection areas, its own budget and its own
    remembered window. A second press of a button must not throw them away."""
    root = an_install(tmp_path / "VMD")
    write_preset(root, "251", derive(a_camera(), "192.168.1.251"))
    settled = load_settings(root / "cameras" / "251" / "settings.json")
    settled.title = "השיטה"
    from vmd.settings import save_settings

    save_settings(settled, root / "cameras" / "251" / "settings.json")

    write_preset(root, "251", derive(a_camera(), "192.168.1.99"))

    kept = load_settings(root / "cameras" / "251" / "settings.json")
    assert kept.title == "השיטה"
    assert kept.camera.host == "192.168.1.251"


# --------------------------------------------------------------------------- #
#  Starting the other console
# --------------------------------------------------------------------------- #


def test_the_built_exe_is_preferred_to_start_another_console(tmp_path: Path) -> None:
    from vmd.desktop.presets import console_command

    root = an_install(tmp_path / "VMD")
    (root / "VMD.exe").write_bytes(b"exe")
    settings_file = root / "cameras" / "251" / "settings.json"

    command = console_command(root, settings_file)

    assert command[0] == str(root / "VMD.exe")
    assert command[1:] == ["--settings", str(settings_file)]


def test_the_bat_starts_it_where_there_is_no_exe(tmp_path: Path) -> None:
    """VMD.exe is optional - both installers treat it so - and a console that
    could only be started by an exe that was never built is a button that does
    nothing on exactly the machines that have no other way in."""
    from vmd.desktop.presets import console_command

    root = an_install(tmp_path / "VMD")  # VMD.bat only

    assert console_command(root, root / "settings.json")[0] == str(root / "VMD.bat")
