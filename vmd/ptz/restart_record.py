"""When this console last restarted its camera, kept beside settings.json.

Read by two processes: the console, so a console reopened in the same minute
does not restart the camera again, and the recorder, so the stream deaths a
restart causes are not reported as a recorder that keeps failing. The recorder
only needs the file's age, which is why `restarted_within` goes by mtime.

Nothing here raises. A missing or broken file means "no restart known".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FILENAME = "last_restart.json"


def path_beside(settings_path: str | Path) -> Path:
    return Path(settings_path).parent / FILENAME


def read(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(payload["at"])
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken file is no restart, never a crash
        logger.warning("could not read %s; treating it as no restart", path)
        return None


def write(path: Path, when: datetime, why: str) -> None:
    """Atomically: the recorder must never read a half-written file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"at": when.isoformat(timespec="seconds"), "why": why})
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".last_restart.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - the restart itself must still be sent
        logger.exception("could not write %s", path)


def restarted_within(path: Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime < seconds
    except OSError:
        return False
