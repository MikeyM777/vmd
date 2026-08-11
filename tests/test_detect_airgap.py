"""The classifier is the only part of this system that ever wanted a network.

The deployment laptop is offline and stays offline, and that has to be a
property of the code rather than an accident of where the machine is plugged
in. Three things are tested here:

* importing `vmd` turns ultralytics' telemetry, its online check, its
  AutoUpdate and its pip-installer off, before ultralytics can be imported;
* a missing weights file produces a sentence and unlabelled events, never a
  download and never a lost event;
* the URL the detector names in its log does not carry the camera's password.

Nothing here imports ultralytics or torch, and nothing here opens a socket.
"""

import logging
import subprocess
import sys

import numpy as np
import pytest

import vmd
from vmd.detect.classify import DEFAULT_WEIGHTS, YoloClassifier, load_yolo, weights_path
from vmd.detect.events import EventStore
from vmd.detect.motion import Box
from vmd.detect.pipeline import Detection
from vmd.detect.runner import StreamDetector, without_credentials
from vmd.detect.tracking import Track

# The strings ultralytics' own `env_bool` reads as true, from its docstring.
TRUTHY = {"1", "true", "yes", "on", "y", "t"}


# --------------------------------------------------------------------------
# Nothing reaches out
# --------------------------------------------------------------------------


def test_importing_vmd_turns_the_model_library_offline():
    """`YOLO_OFFLINE` is read at ultralytics import and never revisited.

    It short-circuits `is_online()` before any syscall, which is what stops the
    Google Analytics POST on every predict(), the two DNS lookups at import,
    the PyPI version check and AutoUpdate - all four read the same flag.
    """
    assert vmd  # the import is the thing under test
    import os

    assert os.environ["YOLO_OFFLINE"].strip().lower() in TRUTHY
    assert os.environ["YOLO_AUTOINSTALL"].strip().lower() not in TRUTHY


def test_the_model_library_keeps_its_settings_inside_the_app_folder():
    """Its settings file holds an id derived from this machine's MAC address.

    Left where it defaults to on Windows - `%APPDATA%\\Roaming` - that id sits
    in the one location a domain-joined machine synchronises off itself.
    """
    import os

    config_dir = os.environ["YOLO_CONFIG_DIR"]
    assert str(vmd.app_folder()) in config_dir
    assert "Roaming" not in config_dir


def test_the_offline_flag_is_set_before_anything_can_read_it():
    """Set at import of `vmd`, which every entry point imports first.

    Checked in a fresh interpreter, because this one has had `vmd` imported for
    a while and could be showing a value some other line set.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import vmd, os; print(os.environ['YOLO_OFFLINE'])"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() in TRUTHY


def test_an_operator_who_set_the_flag_themselves_keeps_what_they_set():
    result = subprocess.run(
        [sys.executable, "-c", "import vmd, os; print(os.environ['YOLO_OFFLINE'])"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**__import__("os").environ, "YOLO_OFFLINE": "0"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


# --------------------------------------------------------------------------
# Missing weights degrade honestly
# --------------------------------------------------------------------------


def test_the_weights_are_looked_for_beside_the_app_not_in_the_working_directory():
    """A bare name is resolved against wherever the process was started.

    It only ever works today because both launchers set the working directory
    to the app folder. A service started any other way would look for the
    weights somewhere else and, finding nothing, fetch them from github.
    """
    resolved = weights_path(DEFAULT_WEIGHTS)
    assert resolved.is_absolute()
    assert resolved.parent == vmd.app_folder()


def test_missing_weights_are_refused_without_importing_the_model_library(tmp_path):
    """The check comes before the import, and that order is the whole point.

    Handed a name it cannot find, ultralytics recognises `yolo11n.pt` as one of
    its own published assets and fetches it from github.com, three times over.
    """
    before = "ultralytics" in sys.modules
    with pytest.raises(FileNotFoundError) as raised:
        load_yolo(tmp_path / "not-here.pt")
    assert "offline" in str(raised.value)
    assert "not-here.pt" in str(raised.value)
    assert ("ultralytics" in sys.modules) == before, "it imported the library anyway"


def test_missing_weights_cost_the_label_and_not_the_event(tmp_path, caplog):
    """The classifier has no veto, and least of all through its own absence."""
    classifier = YoloClassifier(weights=str(tmp_path / "not-here.pt"))
    frame = np.zeros((120, 160), dtype=np.uint8)

    with caplog.at_level(logging.WARNING, logger="vmd.detect.classify"):
        assert classifier.classify(frame, Box(10, 10, 40, 60)) == ("", 0.0)
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "not-here.pt" in said
    assert "offline" in said
    # A sentence, not a stack trace: the weights are an optional install.
    assert not any(record.exc_info for record in caplog.records)

    # ... and it does not go back and try again on every event for the rest of
    # the night.
    caplog.clear()
    assert classifier.classify(frame, Box(10, 10, 40, 60)) == ("", 0.0)
    assert caplog.records == []


def test_a_detector_with_no_weights_still_writes_the_event(tmp_path):
    """End to end: no weights on this machine, and the row is still there."""

    class OnePipeline:
        frames_suppressed = 0

        def feed(self, image, frame_index):
            if frame_index != 0:
                return []
            track = Track(id=1)
            track.observe(Box(10, 10, 5, 5), 0)
            track.observe(Box(40, 10, 5, 5), 0)
            return [Detection(track=track, box=track.box, frame_index=0)]

        def reset(self):
            pass

    class OneFrame:
        def __init__(self):
            self.frames = 1

        def read(self):
            if self.frames:
                self.frames -= 1
                return True, np.zeros((120, 160), dtype=np.uint8)
            return False, None

        def release(self):
            pass

    store = EventStore(tmp_path / "events.db")
    detector = StreamDetector(
        "rtsp://127.0.0.1:8554/thermal",
        "thermal",
        None,
        store,
        open_capture=lambda url: OneFrame(),
        pipeline=OnePipeline(),
        classifier=YoloClassifier(weights=str(tmp_path / "not-here.pt")),
        sleep=lambda _s: None,
    )
    try:
        assert detector.step() is True
        events = store.recent()
        assert len(events) == 1
        assert events[0].label == ""  # unnamed, and still an event
    finally:
        detector.close()
        store.close()


# --------------------------------------------------------------------------
# The password does not reach the screen
# --------------------------------------------------------------------------


def test_the_url_the_detector_names_has_no_password_in_it(caplog):
    """go2rtc being down makes this the camera's own URL, credentials and all."""
    url = "rtsp://admin:hunter2@10.0.0.2:554/thermal"

    class OneFrame:
        def read(self):
            return False, None

        def release(self):
            pass

    detector = StreamDetector(
        url,
        "thermal",
        None,
        None,
        open_capture=lambda _url: OneFrame(),
        pipeline=None,
        sleep=lambda _s: None,
    )
    try:
        with caplog.at_level(logging.INFO, logger="vmd.detect.runner"):
            detector.step()
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "hunter2" not in said, said
        assert "admin" in said  # which account was refused is the diagnosis
        assert "10.0.0.2" in said
    finally:
        detector.close()


def test_a_failure_to_open_does_not_quote_the_password_back(caplog):
    url = "rtsp://admin:hunter2@10.0.0.2:554/thermal"

    def refuse(target):
        raise OSError(f"could not open {target}: timed out")

    detector = StreamDetector(
        url, "thermal", None, None, open_capture=refuse, sleep=lambda _s: None
    )
    try:
        with caplog.at_level(logging.WARNING, logger="vmd.detect.runner"):
            detector.step()
        said = " ".join(record.getMessage() for record in caplog.records)
        assert "hunter2" not in said, said
    finally:
        detector.close()


def test_a_line_with_no_url_in_it_is_left_alone():
    assert without_credentials("thermal: 40 reads in a row") == "thermal: 40 reads in a row"
    assert without_credentials("rtsp://127.0.0.1:8554/thermal") == "rtsp://127.0.0.1:8554/thermal"
