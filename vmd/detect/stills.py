"""The picture of what moved, with the box drawn on it.

"After a couple of seconds it beeped and no box was on the screen."

He is right that there was no box, and the reason it was not on the live picture
is not that nobody thought of it. The live pictures are drawn by libVLC into a
native window handle, and this project's hardest-won rule - stated in
`vmd/desktop/live.py` and again in `vmd/desktop/fullscreen.py` - is that nothing
is ever put over one of them. A Qt widget on top of a native child window is
either invisible or flickering, depending on the machine, and the failure it
produces is the worst this console has: a black rectangle with a frame counter
still counting beside it.

There is also a second reason, and it is the better one. A box drawn on the live
picture would be drawn where the thing was when the detector saw it, on a
picture that is now some frames further on - so it would be beside the thing
rather than on it, and would be furthest out at exactly the moment something is
moving fast. A still of the frame the detector actually confirmed on has the box
in the right place by construction, because it is the same frame.

So: when a track is confirmed, the frame it was confirmed on is written out with
the box already drawn, and the console shows that. It is a complete picture with
the mark on it, which also means it is something the operator can send to
somebody - which he cannot do with a rectangle on a screen.

Where they go, and why the console does not have to be told
-----------------------------------------------------------

    <recordings>/movement/<stream>/<the moment it started, in milliseconds>.jpg

Worked out from the event rather than recorded in it, which is what keeps this
out of the database. Every event already carries the stream and the moment it
started, both of which the console reads out of events.db; `still_for` turns
those into the same path this file wrote, on both sides, from one function. A
still that failed to write is a file that is not there, which the console
already has to handle - the folder is inside the recordings root, and retention
deletes the oldest recordings on a disk that is filling.

Bounded, always. This process runs for months and a windy night is forty events
an hour, so the folder keeps its newest `KEEP_PER_STREAM` and no more.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# How many stills are kept per stream. Fifty at about 100 KB is 5 MB per camera:
# small enough to be irrelevant on a 950 GB drive, deep enough that an operator
# coming back to the console after a couple of hours can still see what the
# alarms he missed were about.
KEEP_PER_STREAM = 50

# The longest edge a still is saved at. The camera is 4K and nothing here needs
# to be: this is looked at in a strip a few hundred pixels wide, and a 4K JPEG
# would cost a megabyte and about a tenth of a second to encode on the thread
# that is meant to be watching the perimeter.
LONGEST_EDGE = 960

# JPEG quality. High enough that a person at 700 m - about thirteen pixels - is
# not smeared into the background by the compression, which is the one thing
# this picture exists to show.
QUALITY = 85


def folder_for(root, stream: str) -> Path:
    """Where one stream's stills live."""
    return Path(root) / "movement" / _safe(stream)


def still_for(root, stream: str, started: float) -> Path:
    """The still for one event: the same path on the side that writes it and
    the side that reads it, worked out from what the event already carries."""
    return folder_for(root, stream) / f"{int(float(started) * 1000)}.jpg"


def _safe(name: str) -> str:
    """A stream name as a folder name.

    Stream names are typed by the operator and are used as go2rtc stream ids, so
    they are already tame - but they have never had to be a path before, and a
    name with a slash in it would put this folder somewhere else entirely.
    """
    keep = [character if character.isalnum() or character in "-_" else "-" for character in name]
    return "".join(keep).strip("-") or "stream"


def save(frame, box, root, stream: str, started: float) -> Path | None:
    """Write the frame with the box on it. Returns where, or None if it did not.

    Never raises. A still is a convenience and the event is the product: a full
    disk, a folder that has gone, a frame OpenCV will not encode - each of those
    costs the picture and must cost nothing else. The caller is
    `StreamDetector._record`, which has already written the row by the time this
    runs.
    """
    try:
        import cv2
    except Exception:  # noqa: BLE001 - a detector without OpenCV cannot be here
        return None

    try:
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        height, width = frame.shape[:2]
        x, y, w, h = (int(value) for value in box)

        # Drawn before the resize, in the frame's own coordinates, because that
        # is what the box is in. Resizing first would need the box scaled, which
        # is an arithmetic error waiting to happen in the one picture whose
        # whole job is to say where.
        marked = frame.copy()
        # Two rectangles, dark under bright. A single coloured outline
        # disappears against a scene the same colour - and a thermal picture is
        # mostly white-hot or mostly black, so there is no one colour that is
        # visible on both.
        cv2.rectangle(marked, (x - 1, y - 1), (x + w + 1, y + h + 1), (0, 0, 0), 5)
        cv2.rectangle(marked, (x, y), (x + w, y + h), (0, 220, 255), 2)

        longest = max(width, height)
        if longest > LONGEST_EDGE:
            scale = LONGEST_EDGE / float(longest)
            marked = cv2.resize(
                marked,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )

        path = still_for(root, stream, started)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), marked, [int(cv2.IMWRITE_JPEG_QUALITY), QUALITY]):
            logger.warning("the picture of what moved could not be written: %s", path)
            return None
        prune(root, stream)
        return path
    except Exception:  # noqa: BLE001 - a picture may never cost an alarm
        logger.exception("the picture of what moved could not be saved")
        return None


def prune(root, stream: str, keep: int = KEEP_PER_STREAM) -> int:
    """Delete all but the newest `keep`. Returns how many went.

    By name and not by modification time. The name is the moment the movement
    started, which is what "newest" means here, and a file's timestamp on this
    machine is whatever the clock said when it was written - on a laptop whose
    clock is set by hand and can go backwards by an hour.
    """
    folder = folder_for(root, stream)
    try:
        stills = sorted(
            (path for path in folder.glob("*.jpg") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
    except OSError:
        return 0
    gone = 0
    for path in stills[: max(0, len(stills) - keep)]:
        try:
            path.unlink()
            gone += 1
        except OSError:
            # Held open by something, or already gone. Not worth a line: this
            # runs on every event and the folder is bounded by the next one.
            continue
    return gone
