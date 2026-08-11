"""VMD - video motion detection with AI verification.

This module runs before anything else in the package, which is the only reason
the four lines at the bottom are here rather than somewhere more obviously
theirs. They have to be set **before ultralytics is imported**, because the
decisions they change are made at that import and never revisited.

The laptop this ships to is offline and stays offline. That is not a
deployment accident to be relied on: the moment it touches a network,
ultralytics does three things nobody asked it to.

* It posts to Google Analytics on every `predict()`. The callback is registered
  for every model instance, not just the CLI, and the payload carries the CPU
  model, the Python and torch versions, the environment, the model filename,
  the device and a persistent id derived from this machine's MAC address.
* It resolves `one.one.one.one` and `dns.google` at import, to decide whether
  it is online.
* It checks PyPI for a newer version, and pip-installs missing requirements.

`YOLO_OFFLINE` short-circuits `ultralytics.utils.is_online()` before any
syscall, which turns all three off at once - the analytics gate, the AutoUpdate
and the version check all read the flag it sets. `YOLO_AUTOINSTALL` stops the
install path separately, because a machine with no network should fail with a
sentence rather than by trying to fetch a wheel.

`YOLO_CONFIG_DIR` moves ultralytics' settings file - which is where that
MAC-derived id is kept - out of `%APPDATA%\\Roaming`, the one Windows location
that syncs off the machine when it is domain-joined, and into the app folder
next to everything else this system owns.

`setdefault`, not assignment: an operator who has deliberately set one of these
keeps what they set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__version__ = "0.1.0"


def app_folder() -> Path:
    """The folder the app lives in - the exe's folder once frozen.

    Anything the application owns on disk is found from here rather than from
    the working directory, which is only ever the right folder because the
    launchers set it.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("YOLO_AUTOINSTALL", "0")
os.environ.setdefault("YOLO_CONFIG_DIR", str(app_folder() / "ultralytics"))
