"""What `import vmd.detect` costs the process that does it.

Two processes on this machine want one thing out of this package and nothing
else: the console reads the movement list out of `events.db`, and so does the
recorder when it reclaims footage. Both of them are `sqlite3` and a dataclass.
Neither of them has any use for OpenCV, and one of them - the console - has to
open on a laptop where the vision stack is missing or broken, because a console
that will not open is a perimeter nobody is watching.

So the cost of `from vmd.detect.events import EventStore` is a fact worth
pinning, and the only honest way to pin an import graph is to make the module
under suspicion genuinely unimportable and try it in a process of its own. A
check inside this interpreter proves nothing: pytest has already imported cv2
for the pipeline tests, so `import cv2` here would succeed no matter what this
package does.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Long enough that a cold interpreter on a busy laptop is not called a failure,
# short enough that a hang is a failed test rather than a stopped suite. The
# child imports sqlite3 and a dataclass; it does not open a socket, read the
# camera or load a model.
CHILD_TIMEOUT = 120

# Installed into `sys.meta_path` in the child, ahead of everything, so that the
# vision stack is not merely absent from the answer - it cannot be imported at
# all. That is the laptop this has to work on: opencv missing, or present and
# refusing to load because a Visual C++ runtime is not there.
BLOCK = """
import sys

class Blocked:
    def __init__(self, *names):
        self.names = names

    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root in self.names:
            raise ImportError(f"{name} is not installed on this machine")
        return None

sys.meta_path.insert(0, Blocked("cv2", "numpy"))
"""


def run_child(body: str) -> subprocess.CompletedProcess:
    """Run `body` in a fresh interpreter with cv2 and numpy unimportable."""
    return subprocess.run(
        [sys.executable, "-c", BLOCK + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT,
    )


def test_the_blocker_really_blocks():
    """The test's own instrument, checked before anything is measured.

    If this passed by accident - a meta path hook that does not fire - every
    other test in this file would pass while the package went on dragging
    OpenCV into the console.
    """
    result = run_child(
        """
        try:
            import cv2
        except ImportError as exc:
            print("blocked:", exc)
        else:
            raise SystemExit("cv2 imported; the blocker does not work")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "blocked:" in result.stdout


def test_the_event_store_can_be_opened_without_the_vision_stack(tmp_path):
    """The console's and the recorder's whole use of this package.

    It was `vmd/detect/__init__.py` importing `motion`, and `motion` importing
    cv2, that made this a lie: `from vmd.detect.events import EventStore` runs
    the package's `__init__` first, so the console got the entire detector.
    On a machine where that import fails, the console lost the movement list
    and every mark on the timeline, and said so only in the Logs tab.
    """
    database = tmp_path / "events.db"
    result = run_child(
        f"""
        import sys

        from vmd.detect.events import EventStore

        store = EventStore({str(database)!r})
        store.add(
            stream="thermal",
            started=1000.0,
            ended=1001.0,
            box=(1, 2, 3, 4),
            travelled_px=12.0,
            label="",
            confidence=0.0,
            clip_path="",
        )
        rows = store.recent(10)
        store.close()
        print("rows:", len(rows))
        print("cv2 imported:", "cv2" in sys.modules)
        print("numpy imported:", "numpy" in sys.modules)
        """
    )
    assert result.returncode == 0, result.stderr
    assert "rows: 1" in result.stdout
    assert "cv2 imported: False" in result.stdout
    assert "numpy imported: False" in result.stdout


def test_the_package_itself_imports_without_the_vision_stack():
    """`import vmd.detect` is what every submodule import runs first."""
    result = run_child(
        """
        import sys

        import vmd.detect

        print("cv2 imported:", "cv2" in sys.modules)
        """
    )
    assert result.returncode == 0, result.stderr
    assert "cv2 imported: False" in result.stdout


def test_the_detector_service_module_imports_without_the_vision_stack():
    """The console reads two facts out of `vmd.detect_main`.

    Where the detector publishes its state, and which streams are detected.
    Both were copied into `vmd/desktop/services.py` instead of imported,
    because importing this module used to pull cv2 and numpy into the window's
    process. The copies can only be deleted if this stays true, so it is
    pinned here rather than left as an intention.
    """
    result = run_child(
        """
        import sys

        from vmd.detect_main import STATUS_FILENAME, detected_streams

        print("status file:", STATUS_FILENAME)
        print("rule:", callable(detected_streams))
        print("cv2 imported:", "cv2" in sys.modules)
        print("numpy imported:", "numpy" in sys.modules)
        """
    )
    assert result.returncode == 0, result.stderr
    assert "status file: detection.json" in result.stdout
    assert "rule: True" in result.stdout
    assert "cv2 imported: False" in result.stdout
    assert "numpy imported: False" in result.stdout


def test_the_names_the_package_publishes_still_arrive():
    """Lazy must not mean gone. Anything that genuinely wants the pipeline
    still gets it from the package's own namespace, and gets the real class."""
    import vmd.detect as detect
    from vmd.detect.events import EventStore
    from vmd.detect.motion import MotionFinder
    from vmd.detect.runner import StreamDetector

    assert detect.EventStore is EventStore
    assert detect.MotionFinder is MotionFinder
    assert detect.StreamDetector is StreamDetector
    # Everything the package says it publishes can actually be reached, and
    # nothing it does not publish can be reached by accident.
    for name in detect.__all__:
        assert getattr(detect, name) is not None
    # Everything published is listed, so that tab completion and `help()` show
    # the same package a reader of `__all__` was promised.
    assert set(detect.__all__) <= set(dir(detect))


def test_asking_for_a_name_that_is_not_there_is_still_an_attribute_error():
    import vmd.detect as detect
    import pytest

    with pytest.raises(AttributeError):
        detect.NoSuchThing
