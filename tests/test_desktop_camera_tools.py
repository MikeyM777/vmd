"""Find the right path, and fit the camera to the link, without a browser."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt

from vmd.desktop.settings_tab import CameraTools, SettingsTab
from vmd.settings import CameraSettings, Settings, StreamSettings


class FakePtz:
    def __init__(self) -> None:
        self.fitted_to: int | None = None

    def fit_encoders_to_link(self, ceiling_kbps: int) -> dict:
        self.fitted_to = ceiling_kbps
        return {"ok": True, "changed": ["visible: 16000 -> 2800 kb/s"]}


def settings_with_camera() -> Settings:
    return Settings(
        camera=CameraSettings(
            host="10.0.0.2",
            streams=[StreamSettings(name="thermal", url="rtsp://10.0.0.2/ch2", enabled=True)],
        )
    )


def test_finding_paths_reports_progress_and_results(qtbot) -> None:
    progress: list[str] = []
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda settings, on_progress: (
            on_progress("trying /ch1 (1/24)"),
            ["  [ok] /ch1   codec_name=h264"],
        )[1],
        diagnose=lambda settings: ["nothing to say"],
    )
    tools.on_progress = progress.append
    lines = tools.find_paths(settings_with_camera())
    assert progress == ["trying /ch1 (1/24)"]
    assert any("/ch1" in line for line in lines)


def test_fitting_to_the_link_uses_the_configured_ceiling(qtbot) -> None:
    ptz = FakePtz()
    tools = CameraTools(ptz=ptz, find_paths=lambda s, on_progress: [], diagnose=lambda s: [])
    settings = settings_with_camera()
    settings.bitrate.ceiling_kbps = 4200
    lines = tools.fit_to_link(settings)
    assert ptz.fitted_to == 4200
    assert any("2800" in line for line in lines)


def test_a_report_can_be_written_to_a_file_to_send_on(qtbot, tmp_path) -> None:
    """The spec's replacement for the browser's Copy a report: this window is
    the only thing on the machine, so the report has to become a file."""
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["camera address : 10.0.0.2", "  [ok] answers"],
    )
    target = tmp_path / "vmd-report.txt"
    written = tools.write_report(settings_with_camera(), target, extra=["recording: yes"])
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "10.0.0.2" in text
    assert "recording: yes" in text


def test_a_report_never_contains_the_password(qtbot, tmp_path) -> None:
    settings = settings_with_camera()
    settings.camera.password = "s3cret-in-the-field"
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["password       : set"],
    )
    target = tmp_path / "vmd-report.txt"
    tools.write_report(settings, target, extra=[])
    assert "s3cret-in-the-field" not in target.read_text(encoding="utf-8")


def test_a_report_never_contains_a_password_that_needed_encoding(qtbot, tmp_path) -> None:
    """The password reaches the report the way RTSP carries it: percent-encoded
    into the URL. `p@ss:w/rd` is written `p%40ss%3Aw%2Frd` there, so redacting
    only the typed form redacts nothing at all - in the one file this console
    produces for the express purpose of being sent to somebody else.

    The radio's password travels the same way and out of the same form.
    """
    from urllib.parse import quote

    from vmd.streaming.go2rtc import with_credentials

    settings = settings_with_camera()
    settings.camera.username = "admin"
    settings.camera.password = "p@ss:w/rd"
    settings.radio.password = "r@dio/pass"
    url = with_credentials("rtsp://10.0.0.2/ch2", "admin", settings.camera.password)
    assert quote(settings.camera.password, safe="") in url  # this is how it travels

    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: [f"  trying {url}", f"radio          : {s.radio.password}"],
    )
    target = tmp_path / "vmd-report.txt"
    tools.write_report(settings, target, extra=[])

    text = target.read_text(encoding="utf-8")
    for secret in (
        settings.camera.password,
        quote(settings.camera.password, safe=""),
        settings.radio.password,
        quote(settings.radio.password, safe=""),
    ):
        assert secret not in text, f"{secret!r} travelled in the report"
    assert "10.0.0.2" in text, "the report still has to say something useful"


def test_a_report_with_no_password_set_is_still_readable(qtbot, tmp_path) -> None:
    """The first-run state. Replacing the empty string would put the redaction
    between every character of the file."""
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["camera address : 10.0.0.2"],
    )
    target = tmp_path / "vmd-report.txt"
    tools.write_report(settings_with_camera(), target, extra=["recording: yes"])

    text = target.read_text(encoding="utf-8")
    assert "camera address : 10.0.0.2" in text
    assert "****" not in text


def test_a_camera_that_refuses_is_reported_in_its_own_words(qtbot) -> None:
    class Refusing:
        def fit_encoders_to_link(self, ceiling_kbps: int) -> dict:
            return {"ok": False, "error": "Sender not Authorized"}

    tools = CameraTools(ptz=Refusing(), find_paths=lambda s, on_progress: [], diagnose=lambda s: [])
    lines = tools.fit_to_link(settings_with_camera())
    assert any("Authorized" in line for line in lines)


# ----------------------------------------------------------- the tab's buttons
#
# The tools above are plain calls. These press the buttons that run them, which
# is where the thing that matters lives: probing two dozen RTSP paths takes up
# to a minute, and a console frozen for a minute is a console that looks dead.


def build_tab(qtbot, tmp_path: Path, tools: CameraTools) -> SettingsTab:
    tab = SettingsTab(settings_path=tmp_path / "settings.json", tools=tools)
    qtbot.addWidget(tab)
    tab.load()
    tab.camera_host = "10.0.0.2"
    tab.set_streams([("thermal", "rtsp://10.0.0.2/ch2", True, "auto")])
    return tab


def test_testing_the_camera_writes_its_answer_into_the_window(qtbot, tmp_path: Path) -> None:
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["  [x] Nothing answers on 10.0.0.2:554."],
    )
    tab = build_tab(qtbot, tmp_path, tools)
    qtbot.mouseClick(tab.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Nothing answers" in tab.output_text(), timeout=5000)


def test_fitting_to_the_link_from_the_window_reports_what_changed(qtbot, tmp_path: Path) -> None:
    ptz = FakePtz()
    tools = CameraTools(ptz=ptz, find_paths=lambda s, on_progress: [], diagnose=lambda s: [])
    tab = build_tab(qtbot, tmp_path, tools)
    qtbot.mouseClick(tab.fit_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "2800" in tab.output_text(), timeout=5000)
    assert ptz.fitted_to == 5000  # the default ceiling, carried from the form


def test_saving_a_report_from_the_window_writes_a_file(qtbot, tmp_path: Path) -> None:
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: ["camera address : 10.0.0.2"],
    )
    tab = build_tab(qtbot, tmp_path, tools)
    target = tmp_path / "vmd-report.txt"
    tab.save_report(target)
    qtbot.waitUntil(target.exists, timeout=5000)
    text = target.read_text(encoding="utf-8")
    assert "VMD report" in text and "10.0.0.2" in text


def test_a_form_the_model_refuses_is_not_sent_to_the_camera(qtbot, tmp_path: Path) -> None:
    """Nothing is worth asking the camera about until the form makes sense."""
    asked: list[str] = []
    tools = CameraTools(
        ptz=FakePtz(),
        find_paths=lambda s, on_progress: [],
        diagnose=lambda s: asked.append("asked") or ["never"],
    )
    tab = build_tab(qtbot, tmp_path, tools)
    tab.set_streams([("thermal", "", True, "auto")])
    qtbot.mouseClick(tab.test_button, Qt.MouseButton.LeftButton)
    assert asked == []
    assert "address" in tab.output_text().lower()


def test_the_window_stays_responsive_while_the_paths_are_probed(qtbot, tmp_path: Path) -> None:
    """Probing takes up to a minute. If it ran on the UI thread the click below
    would not return until it finished - and the button would be enabled again
    by then, which is what this asserts against."""
    started = threading.Event()
    release = threading.Event()

    def slow_find_paths(settings, on_progress):
        started.set()
        on_progress("trying /ch1 (1/24)")
        release.wait(10)
        return ["  [ok] /ch2   codec_name=h264"]

    tools = CameraTools(ptz=FakePtz(), find_paths=slow_find_paths, diagnose=lambda s: [])
    tab = build_tab(qtbot, tmp_path, tools)

    qtbot.mouseClick(tab.find_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=5000)

    # Still probing: the button is out of action and the window is answering.
    assert not tab.find_button.isEnabled()
    qtbot.waitUntil(lambda: "trying /ch1" in tab.output_text(), timeout=5000)

    release.set()
    qtbot.waitUntil(lambda: tab.find_button.isEnabled(), timeout=10000)
    assert "/ch2" in tab.output_text()


def test_a_tool_that_throws_says_so_instead_of_taking_the_console_with_it(
    qtbot, tmp_path: Path
) -> None:
    def exploding(settings):
        raise RuntimeError("ffprobe is not installed")

    tools = CameraTools(ptz=FakePtz(), find_paths=lambda s, on_progress: [], diagnose=exploding)
    tab = build_tab(qtbot, tmp_path, tools)
    qtbot.mouseClick(tab.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "ffprobe is not installed" in tab.output_text(), timeout=5000)
    assert tab.test_button.isEnabled()
