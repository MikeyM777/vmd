"""Does this copy of VMD actually work? One answer, one exit code.

Run by the updater after the new version is on the disk and before the console
is started, and it is the whole of what stands between an operator and a
console that will not open on a machine nobody can fix. Failing it puts the
previous version back.

What it checks is what an update can plausibly break:

  - the console's own modules import, which catches a half-copied tree and a
    library that did not arrive,
  - libVLC can be found, which is reported but not fatal - the console runs
    without a picture and says so, and refusing an update over it would be
    worse than the fault,
  - this machine's settings file still satisfies the model, which catches a
    change to the settings model that this particular file does not meet.

It never touches the camera, the network, the recorder or the disk budget: a
smoke test that can fail for a reason outside the software is a smoke test that
rolls back good updates.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that this copy of VMD works.")
    parser.add_argument("--settings", default="settings.json")
    args = parser.parse_args(argv)

    try:
        import vmd  # noqa: F401
        from vmd.desktop import app, live, settings_tab, window  # noqa: F401
        from vmd.settings import SettingsError, load_settings
    except Exception as failure:  # noqa: BLE001 - the point is to report it
        print(f"selftest failed: the console's own modules do not import: {failure}")
        return 1

    try:
        load_settings(Path(args.settings))
    except SettingsError as failure:
        print(f"selftest failed: this machine's settings file is not valid: {failure}")
        return 1
    except Exception as failure:  # noqa: BLE001
        print(f"selftest failed: the settings file could not be read: {failure}")
        return 1

    # Said, not judged. A console with no VLC opens, records and detects; it
    # shows no live picture and says so in its own words. That is not a reason
    # to throw away an update.
    try:
        from vmd.desktop.libvlc import prepare

        print(f"selftest: VLC found at {prepare().folder}")
    except Exception as failure:  # noqa: BLE001
        print(f"selftest: no live picture on this machine ({failure})")

    print("selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
