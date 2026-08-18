"""A box drawn on the live picture, round the thing that moved.

"I want a box on the view when the VMD detects something, like YOLO does but
without the classifying text."

The box, and no words. Nothing on this console has ever put a noun on what
moved and this does not start: it is an outline and nothing else.

---------------------------------------------------------------------------
Why this is a picture file and not a widget
---------------------------------------------------------------------------

The live pictures are drawn by libVLC into a native window handle, and the rule
this project has paid for twice - stated in `vmd/desktop/live.py` and again in
`vmd/desktop/fullscreen.py` - is that no Qt widget is ever put over one of them.
A widget on top of a native child window is invisible on some machines and
flickering on others, and what it produces at its worst is the failure this
console fears most: a black rectangle with a frame counter still counting.

So the box is not drawn over the video. It is handed to libVLC as a
**subpicture** and libVLC composites it into the video itself, the same way it
draws subtitles - which means it survives hardware decoding, it survives
fullscreen, it scales with the picture, and no window is fighting any other
window for the same pixels.

The mechanism is libVLC's `logo` sub-source: a file on disk, positioned at 0,0,
at the video's own size, holding a transparent image with an outline on it.
Changing the file changes what is drawn.

**This has not been seen working.** It could not be verified on the machine it
was written on: Qt cannot capture a native VLC window, a screen grab of one
rendered through D3D11 comes back black, and a transcode test was inconclusive.
It is therefore behind a switch that is off, and the switch is the only thing
that turns any of this on. If it does not work on the real machine, the cost is
one setting that does nothing and one file nobody looks at.

---------------------------------------------------------------------------
Two things this file is careful about
---------------------------------------------------------------------------

**It never runs on the thread that draws the window.** Building and saving a
full-size transparent PNG was measured at about 80 ms at FHD - the cost is the
two megapixels, not the compression, and no quality setting moves it. 80 ms on
the GUI thread is a window that stops repainting, during an alarm, which is the
one moment it may not. The caller hands this to a worker; see `LiveTab`.

**The file name changes every time.** libVLC is being asked to re-read a file
it has already read, and an image loader that caches by name would draw the
previous box for ever. Alternating between two names makes every update a name
it has not seen since the one before.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# How long a box stays on the picture after the movement that raised it.
#
# Six seconds. Long enough for somebody who looked up at the sound to find it,
# short enough that it is gone before it can be mistaken for something that is
# still there - which is the failure a box has that a sound does not. It is a
# mark saying "this is where it was", not a tracker.
BOX_SECONDS = 6.0

# The outline, in the same two-rectangle form the saved stills use: a dark line
# under a bright one, because a thermal picture is mostly white-hot or mostly
# black and there is no single colour that shows on both.
EDGE = (0, 0, 0, 210)
LINE = (255, 200, 0, 255)
EDGE_WIDTH = 6
LINE_WIDTH = 3


def folder() -> Path:
    """Where this console's overlay files live.

    The system temp folder and not the recordings root: these are scratch, they
    are rewritten every few seconds, and they have no business inside the folder
    that retention deletes from and the offline kit refuses to copy. Named by
    process id so two consoles on one desktop cannot write each other's.
    """
    return Path(tempfile.gettempdir()) / f"vmd-boxes-{os.getpid()}"


def draw(path, width: int, height: int, boxes) -> bool:
    """Write a transparent picture of `width` x `height` with outlines on it.

    Returns whether it wrote one. Never raises: a box is decoration on an alarm
    that has already been made, and a full disk or a folder that has gone must
    cost the outline and nothing else.

    The size is the VIDEO's own size, not the widget's, because the boxes are in
    frame coordinates and libVLC composites this into the frame before any of it
    is scaled to the window.
    """
    if width <= 0 or height <= 0 or not boxes:
        return False
    try:
        from PySide6.QtGui import QColor, QImage, QPainter, QPen

        picture = QImage(int(width), int(height), QImage.Format.Format_ARGB32)
        picture.fill(QColor(0, 0, 0, 0))
        painter = QPainter(picture)
        try:
            for x, y, w, h in boxes:
                for colour, thickness in ((EDGE, EDGE_WIDTH), (LINE, LINE_WIDTH)):
                    pen = QPen(QColor(*colour))
                    pen.setWidth(thickness)
                    painter.setPen(pen)
                    painter.drawRect(int(x), int(y), int(w), int(h))
        finally:
            painter.end()

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return bool(picture.save(str(path), "PNG"))
    except Exception:  # noqa: BLE001 - an outline may never cost an alarm
        logger.exception("the box could not be drawn")
        return False


def blank(path) -> bool:
    """A one-pixel transparent file, for a pane that has nothing to show yet.

    libVLC wants a logo file when the sub-source is switched on, and a pane is
    built long before anything has moved in front of it.
    """
    return draw_blank(path)


def draw_blank(path) -> bool:
    try:
        from PySide6.QtGui import QColor, QImage

        picture = QImage(1, 1, QImage.Format.Format_ARGB32)
        picture.fill(QColor(0, 0, 0, 0))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return bool(picture.save(str(path), "PNG"))
    except Exception:  # noqa: BLE001
        logger.exception("the blank overlay could not be written")
        return False


class Names:
    """Alternating file names for one pane.

    libVLC is being asked to re-read a file it has already read. An image loader
    that caches by name would go on drawing the previous box for ever, so every
    update is written to the name that was not used last time.
    """

    def __init__(self, where, stream: str) -> None:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stream) or "pane"
        self._paths = [Path(where) / f"{safe}-a.png", Path(where) / f"{safe}-b.png"]
        self._next = 0

    def take(self) -> Path:
        path = self._paths[self._next]
        self._next = 1 - self._next
        return path

    def all(self) -> list[Path]:
        return list(self._paths)
