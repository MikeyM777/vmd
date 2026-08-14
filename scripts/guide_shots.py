"""The pictures for the printed operator's guide, taken off the real console.

Run it with `uv run python scripts/guide_shots.py`. It writes annotated PNGs and
a manifest into `docs/guide/images/`, and the manifest is what the guide's text
and its PDF assembler read. Nothing here is a mock-up of the console: every
image is a real Qt widget tree, built from `vmd.desktop`, wearing the real
application stylesheet, grabbed with `QWidget.grab()`. If a control moves, this
script draws it in its new place the next time it is run, and if a control is
removed the script stops being able to point at it - which is the whole reason
the shots are taken from the code rather than photographed off somebody's
screen.

**The pictures inside the pictures.** The console's video panes are drawn by
libVLC out of a camera on the other end of a radio link, and there is no camera
here. The test double the rest of this repository uses, `FakeVideoPane`, is not
a widget at all and draws nothing, so a guide built on it would be a guide made
of black rectangles - and a black rectangle teaches an operator nothing about
where the name plate, the zoom bar and the alarm outline sit around a picture,
which is the only thing these shots are for. So `GuidePane` below is a real
widget that satisfies the same `VideoPane` protocol and paints a scene with
QPainter: a flat vertical gradient, a dead-straight horizon, hard-edged
triangles for a treeline, a fence of evenly spaced posts, one plain blob for the
thing that moved, and a regular dot grid laid over all of it.

It is deliberately not photographic, and the grid is there to make sure of it.
The operator must never be able to mistake a drawing in his guide for footage of
the place he is watching: a page that looks like his perimeter is a page he will
compare against his screen, and every difference between the two would then read
as a fault in the console. A picture that is obviously a diagram cannot be
mistaken for one, and it still shows him exactly which strip of the screen is
the picture and which strips around it are controls.

**The annotation** is circles and leader lines and nothing else. No words are
drawn onto any image, because the guide is printed in Hebrew and English and
text baked into a PNG cannot be translated - so every circle is a number, and
the manifest says what each number is pointing at for the writer to put into
prose. The colour is a magenta that appears nowhere in `DESIGN.md`: the console
already spends amber on the active control, red on an alarm and green on a
healthy reading, and an annotation drawn in any of those three would read as
part of the interface it is annotating.
"""

from __future__ import annotations

import datetime
import json
import math
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

# The project is imported as a package, and this script is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QGroupBox, QWidget  # noqa: E402

from vmd.desktop.mask import MaskDialog  # noqa: E402
from vmd.desktop.style import stylesheet  # noqa: E402
from vmd.desktop.window import ConsoleWindow  # noqa: E402
from vmd.detect.events import EventStore  # noqa: E402
from vmd.desktop.disk import DiskReading, DiskWatcher  # noqa: E402
from vmd.settings import Settings, StreamSettings, save_settings  # noqa: E402
from vmd.storage.index import SegmentIndex  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
IMAGES = HERE / "docs" / "guide" / "images"

# Where the settings file, the segment catalogue and the movement database the
# console really opens are put while the shots are taken.
#
# A temporary folder and not one inside the project, because the only things
# this script is allowed to leave behind are the images and the manifest - and a
# recordings tree, a database and somebody's invented password committed to git
# beside them would be three of them.
WORKING = Path(tempfile.mkdtemp(prefix="vmd-guide-shots-"))

# The recordings folder the Settings tab shows. A made-up Windows path rather
# than the temporary folder above: this string is printed in the guide, and a
# path with a random suffix in it is one the operator would try to match against
# his own machine and fail to.
SHOWN_RECORDINGS = Path("C:/VMD/recordings")

# The size every full-window shot is taken at.
#
# Not the size of whatever screen this script happens to run on. The guide is
# printed, the pages have to look like one another, and a console grabbed at
# 1920x1080 beside one grabbed at 1366x768 is two different-looking programs.
# 1600x1000 is a little larger than the laptop this ships to, which is the right
# way round: everything the operator has on his screen is on the page, with the
# type slightly smaller rather than something pushed off the edge.
WINDOW_WIDTH = 1600
WINDOW_HEIGHT = 1000

# How wide the images are assumed to be printed, in centimetres. It is what
# turns a circle in pixels into a circle in millimetres on the page, and it is
# the only reason this script knows anything about paper.
PRINTED_WIDTH_CM = 16.0

# The annotation colour. Magenta, and chosen by elimination: the console uses
# amber for an active control, red for an alarm and green for a healthy reading,
# so a circle in any of those would be read as part of the console rather than
# as a note about it. Nothing in `vmd/desktop/style.py` is anywhere near this
# hue.
CALLOUT = QColor("#FF2E9F")
# Drawn around every circle and under every leader line, so that the annotation
# is legible on a near-black video pane and on a white field of text alike. One
# colour that works on both beats picking a colour per image and getting it
# wrong on the one image nobody checked.
CASING = QColor("#0A0A0C")
NUMBER = QColor("#FFFFFF")


# --------------------------------------------------------------- the video pane


class GuidePane(QWidget):
    """A video pane that draws a picture, for a machine with no camera.

    It answers everything `vmd.desktop.video.VideoPane` asks for, which is what
    lets the real `LiveTab` and the real `PlaybackTab` be built around it without
    knowing anything has been substituted. `show` shadows `QWidget.show` exactly
    as `VlcVideoPane` does - that is the protocol, and Qt shows child widgets
    through C++ rather than through this name, so nothing is broken by it.

    The default `kind` matters: the Playback tab builds its second picture with
    `type(pane)()` and no arguments, and a pane that could not be built that way
    would quietly cost that tab its side-by-side view.
    """

    def __init__(self, kind: str = "visible", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.url: str | None = None
        self.at_seconds = 0.0
        self._state = "stopped"
        self._paused = False
        self._rate = 1.0
        self._position: float | None = None
        self.setMinimumSize(160, 90)

    # -- the protocol -------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def show(self, url: str, at_seconds: float = 0.0) -> None:  # noqa: A003
        self.url = url
        self.at_seconds = float(at_seconds)
        self._position = max(float(at_seconds), 0.0)
        self._paused = False
        # Straight to `playing` rather than through `connecting`. The guide shows
        # a console that is working, and a name plate reading "connecting" in
        # grey is a picture of the two seconds after start-up.
        self._state = "playing"

    def stop(self) -> None:
        self.url = None
        self._position = None
        self._state = "stopped"

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        if float(rate) > 0.0:
            self._rate = float(rate)

    def position_seconds(self) -> float | None:
        return self._position

    def seek_seconds(self, seconds: float) -> bool:
        if self.url is None:
            return False
        self._position = max(float(seconds), 0.0)
        return True

    def release(self) -> None:
        self._state = "stopped"

    # -- the picture --------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        paint_scene(painter, self.rect(), self.kind)
        painter.end()


def paint_scene(painter: QPainter, area: QRect, kind: str) -> None:
    """Draw one synthetic view: a diagram of a perimeter, never a photograph.

    Two palettes, because the console shows two heads of one camera and the
    guide has to make the pair of them recognisable: `thermal` is the white-hot
    grey a thermal sensor produces, and anything else is the cool blue-grey of a
    visible camera at dusk. Everything in both is a straight line, a triangle,
    a rectangle or an ellipse, and the whole thing carries a regular dot grid -
    which is what stops the drawing from ever being taken for real footage of a
    real place.
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    thermal = kind == "thermal"
    width = max(area.width(), 1)
    height = max(area.height(), 1)
    horizon = area.top() + int(height * 0.46)

    sky_top = QColor("#1D2530") if thermal else QColor("#2A3644")
    sky_bottom = QColor("#39434E") if thermal else QColor("#55677C")
    ground_top = QColor("#0C0F13") if thermal else QColor("#20272E")
    ground_bottom = QColor("#171B20") if thermal else QColor("#131820")

    sky = QLinearGradient(0, area.top(), 0, horizon)
    sky.setColorAt(0.0, sky_top)
    sky.setColorAt(1.0, sky_bottom)
    painter.fillRect(QRect(area.left(), area.top(), width, horizon - area.top()), QBrush(sky))

    ground = QLinearGradient(0, horizon, 0, area.bottom())
    ground.setColorAt(0.0, ground_top)
    ground.setColorAt(1.0, ground_bottom)
    painter.fillRect(
        QRect(area.left(), horizon, width, area.bottom() - horizon + 1), QBrush(ground)
    )

    # The horizon itself, drawn as a line rather than left as the seam between
    # two fills: it is the one feature in the picture the operator is asked to
    # recognise, because the treeline is what the masking dialog exists for.
    painter.setPen(QPen(QColor("#7C8794") if thermal else QColor("#8FA0B4"), 1))
    painter.drawLine(area.left(), horizon, area.right(), horizon)

    # A treeline: evenly spaced triangles, which no hillside has ever had.
    trees = QColor("#0A0D10") if thermal else QColor("#18202A")
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(trees)
    step = max(int(width / 22), 8)
    for index in range(0, width + step, step):
        peak = int(height * (0.06 + 0.02 * ((index // step) % 3)))
        x = area.left() + index
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(x - step * 0.6, horizon + 1),
                    QPointF(x, horizon - peak),
                    QPointF(x + step * 0.6, horizon + 1),
                ]
            )
        )

    # A fence across the near ground: one long line and posts at a fixed
    # spacing. This is the perimeter the console is watching, said as a diagram.
    fence_y = area.top() + int(height * 0.74)
    painter.setPen(QPen(QColor("#5E6B78") if thermal else QColor("#6E7F92"), 2))
    painter.drawLine(area.left(), fence_y, area.right(), fence_y)
    post = max(int(width / 12), 12)
    for index in range(post // 2, width, post):
        x = area.left() + index
        painter.drawLine(x, fence_y - int(height * 0.06), x, fence_y + int(height * 0.05))

    # The thing that moved: one blob, plainly a blob. On the thermal head it is
    # white-hot and on the visible one it is a shadow, which is what the two
    # sensors really do with the same person.
    blob = QColor("#F2F4F6") if thermal else QColor("#0B0E12")
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(blob)
    cx = area.left() + int(width * 0.63)
    cy = fence_y - int(height * 0.03)
    body = max(int(height * 0.05), 4)
    painter.drawEllipse(QPointF(cx, cy - body * 1.4), body * 0.55, body * 0.55)
    painter.drawRoundedRect(
        QRect(cx - body // 2, cy - body, body, int(body * 1.9)), body * 0.3, body * 0.3
    )

    # And the grid that says "this is a drawing". Faint, regular, and over
    # everything, including the blob.
    painter.setBrush(Qt.BrushStyle.NoBrush)
    dot = QColor(255, 255, 255, 26)
    painter.setPen(QPen(dot, 1))
    spacing = max(int(width / 40), 10)
    for x in range(area.left() + spacing, area.right(), spacing):
        for y in range(area.top() + spacing, area.bottom(), spacing):
            painter.drawPoint(x, y)


def scene_image(width: int, height: int, kind: str) -> QImage:
    """The same synthetic view as a picture, for the masking dialog.

    The dialog is handed a frame that has already been fetched from the camera,
    so this is the frame - drawn rather than fetched, for the reason the module
    docstring gives.
    """
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    paint_scene(painter, QRect(0, 0, width, height), kind)
    painter.end()
    return image


# ------------------------------------------------------------------- the fakes


class FakeServices:
    """The recorder, the streaming server and the detector, in one of two moods.

    `mood` is switched between shots rather than two objects being built,
    because the window holds the one it was given: the healthy band and the band
    with a fault have to be the same console on the same day, or the guide is
    showing two different installations and calling them one.
    """

    def __init__(self, disk: DiskWatcher) -> None:
        self.disk = disk
        self.mood = "well"

    def apply(self, settings) -> None: ...

    def start(self) -> None: ...

    def tick(self) -> list[str]:
        return []

    def stop(self) -> None: ...

    def local_url(self, name: str) -> str:
        return f"rtsp://127.0.0.1:8554/{name}"

    def state(self) -> dict:
        if self.mood == "well":
            return {
                "recording": True,
                "recording_state": {"reason": "recording"},
                "streaming": "streaming",
                "restarts": {},
                "detection": {
                    "enabled": True,
                    "running": True,
                    "restarts": 0,
                    "reason": "watching thermal",
                },
                "on_camera": [],
            }
        # One fault, and one that leaves the pictures alone: the recorder has
        # stopped while the streaming server and the detector are still up. It
        # is the state the band is hardest to read in - three quiet chips and
        # one that is saying everything - and it is the one the operator has to
        # act on fastest, because nothing is being kept.
        return {
            "recording": False,
            "recording_state": {
                "reason": "NOT recording - ffmpeg stopped and was restarted 3 times"
            },
            "streaming": "streaming",
            "restarts": {"recorder": 3},
            "detection": {
                "enabled": True,
                "running": True,
                "restarts": 0,
                "reason": "watching thermal",
            },
            "on_camera": [],
        }


class FakePtz:
    """A camera that answers, and answers the same way every time.

    It reports a zoom, because the zoom bar under each picture is one of the
    shots and a bar drawn disabled is a picture of a camera that has not
    answered yet.
    """

    def __init__(self) -> None:
        self.positions = {"thermal": 0.42, "visible": 0.68, "playback": 0.5}

    def apply(self, settings) -> None: ...

    def status(self) -> dict:
        return {"available": True, "reason": ""}

    def move(self, pan, tilt, zoom) -> dict:
        return {"ok": True}

    def stop(self) -> dict:
        return {"ok": True}

    def home(self) -> dict:
        return {"ok": True}

    def zoom(self, stream: str, where: float) -> dict:
        self.positions[stream] = float(where)
        return {"ok": True}

    def zoom_hold(self, stream: str, speed: float) -> dict:
        return {"ok": True}

    def zoom_poll(self) -> None: ...

    def zoom_position(self, stream: str) -> float | None:
        return self.positions.get(stream)

    def zoom_ready(self) -> dict:
        return {"ok": True, "checking": False, "absolute": True, "shared": False}


class FakeRadio:
    """The link, in one of two states, for the same reason the services have two.

    The healthy figures are the ones the README reports from the real radio: a
    15 km hop carrying about 10.7 Mb/s of video and spending most of the link's
    airtime doing it.
    """

    GOOD = {
        "connected": True,
        "age_seconds": 1.0,
        "signal_dbm": -63.0,
        "noise_dbm": -95.0,
        "airtime_percent": 44.0,
        "rx_mbps": 10.7,
        "rx_capacity_mbps": 18.0,
        "tx_mbps": 0.6,
        "tx_capacity_mbps": 18.0,
        "ccq": 962.0,
        "distance_m": 15400.0,
        "device": "Ubiquiti PowerBeam 5AC",
        "uptime_s": 268_400.0,
    }
    BUSY = {**GOOD, "airtime_percent": 71.0, "rx_mbps": 15.9, "signal_dbm": -68.0}

    def __init__(self) -> None:
        self.mood = "well"

    def apply(self, settings) -> None: ...

    def status(self) -> dict:
        return dict(self.GOOD if self.mood == "well" else self.BUSY)


# -------------------------------------------------------------- the annotation


@dataclass
class Spot:
    """One numbered circle: where it points, where the circle sits, and why."""

    number: int
    target: QPoint
    centre: QPoint
    what: str


@dataclass
class Shot:
    """One finished image and everything the guide's writer needs about it."""

    slug: str
    title: str
    width: int
    height: int
    callouts: list[dict] = field(default_factory=list)


def circle_radius(width: int) -> int:
    """How big a numbered circle is, so that it is the same size on the page.

    Every image in the guide is printed at the same width, so a crop 980 px wide
    is magnified more than a full window 1600 px wide - and a circle of a fixed
    number of pixels would come out physically smaller on the shot that was
    magnified less. The radius is a share of the width instead, which makes
    every circle about 4 mm across wherever it appears, and then held between
    two stops so a very narrow crop still gets a circle with a readable number
    in it.
    """
    per_cm = width / PRINTED_WIDTH_CM
    return int(max(13, min(24, round(per_cm * 0.2))))


def annotate(pixmap: QPixmap, spots: list[Spot]) -> QImage:
    """Draw the circles and their leader lines onto a grab. Words: none.

    Each circle is drawn twice - a dark casing and then the magenta - and so is
    each leader line. The console is near-black behind the pictures and near-
    white inside its text fields, and an annotation that is legible on only one
    of those is an annotation that will be invisible on the page that matters.
    """
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    radius = circle_radius(image.width())
    font = QFont()
    font.setPixelSize(int(radius * 1.15))
    font.setBold(True)
    painter.setFont(font)

    for spot in spots:
        line = _leader(spot, radius)
        if line is not None:
            start, end = line
            painter.setPen(_round_pen(CASING, 6))
            painter.drawLine(start, end)
            painter.setPen(_round_pen(CALLOUT, 3))
            painter.drawLine(start, end)
            _arrowhead(painter, start, end, radius)

        painter.setPen(QPen(CASING, 3))
        painter.setBrush(CALLOUT)
        painter.drawEllipse(spot.centre, radius, radius)

        painter.setPen(QPen(NUMBER))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        box = QRect(
            spot.centre.x() - radius,
            spot.centre.y() - radius,
            radius * 2,
            radius * 2,
        )
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, str(spot.number))

    painter.end()
    return image


def _round_pen(colour: QColor, width: int) -> QPen:
    """A pen with round ends, which is what stops a leader line looking chipped
    where the dark casing under it is a little wider than the line on top."""
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _leader(spot: Spot, radius: int) -> tuple[QPointF, QPointF] | None:
    """The line from the circle to the thing, or None when it is sitting on it.

    It starts at the edge of the circle rather than at its centre, and stops a
    few pixels short of the target, so that the arrowhead points at the control
    instead of covering it.
    """
    dx = spot.target.x() - spot.centre.x()
    dy = spot.target.y() - spot.centre.y()
    distance = math.hypot(dx, dy)
    if distance <= radius * 1.4:
        return None
    ux, uy = dx / distance, dy / distance
    start = QPointF(spot.centre.x() + ux * (radius + 2), spot.centre.y() + uy * (radius + 2))
    end = QPointF(spot.target.x() - ux * 5, spot.target.y() - uy * 5)
    return start, end


def _arrowhead(painter: QPainter, start: QPointF, end: QPointF, radius: int) -> None:
    """A small filled head at the far end, so the line has a direction."""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    distance = math.hypot(dx, dy) or 1.0
    ux, uy = dx / distance, dy / distance
    size = max(radius * 0.55, 7.0)
    left = QPointF(end.x() - ux * size - uy * size * 0.45, end.y() - uy * size + ux * size * 0.45)
    right = QPointF(end.x() - ux * size + uy * size * 0.45, end.y() - uy * size - ux * size * 0.45)
    head = QPolygonF([end, left, right])
    painter.setPen(QPen(CASING, 2))
    painter.setBrush(CALLOUT)
    painter.drawPolygon(head)
    painter.setBrush(Qt.BrushStyle.NoBrush)


# ------------------------------------------------------------- taking the shots


class Album:
    """Every shot taken so far, and the one way of taking another.

    A shot is a host widget, an optional rectangle of it, and a list of things
    to point at. The rectangle is what makes a close crop possible without a
    second grab: `QWidget.grab(rect)` renders the widget and hands back that
    part of it, so a crop is the same pixels the whole window would have had.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.shots: list[Shot] = []

    def take(
        self,
        slug: str,
        title: str,
        host: QWidget,
        marks: list[tuple[QPoint, QPoint, str]],
        rect: QRect | None = None,
    ) -> None:
        if rect is not None:
            # Cropped to the widget. A rectangle that runs off it is handed back
            # as a null pixmap, and a null pixmap becomes an image that fails to
            # save with nothing on screen saying why. Asking for a few pixels of
            # margin round something that is already against an edge is ordinary
            # and is simply given what there is; asking for a rectangle that is
            # nowhere near the widget is a mistake and stops the run.
            rect = rect.intersected(host.rect())
            if rect.isEmpty():
                raise ValueError(
                    f"the {slug} shot asks for a part of a widget that is not there"
                )
        pixmap = host.grab(rect) if rect is not None else host.grab()
        origin = rect.topLeft() if rect is not None else QPoint(0, 0)
        spots = [
            Spot(index + 1, target - origin, centre - origin, what)
            for index, (target, centre, what) in enumerate(marks)
        ]
        radius = circle_radius(pixmap.width())
        for spot in spots:
            # A circle drawn half off the page is a callout the reader cannot
            # count, and a target outside the image is an arrow pointing at
            # something that is not in the picture. Both are worse than a
            # missing number, and neither is visible in a thumbnail.
            for name, place in (("circle", spot.centre), ("target", spot.target)):
                edge = radius if name == "circle" else 0
                if not (
                    edge <= place.x() <= pixmap.width() - edge
                    and edge <= place.y() <= pixmap.height() - edge
                ):
                    raise ValueError(
                        f"the {slug} shot puts callout {spot.number}'s {name} at "
                        f"({place.x()}, {place.y()}), outside a "
                        f"{pixmap.width()}x{pixmap.height()} image"
                    )
        image = annotate(pixmap, spots)
        path = self.folder / f"{slug}.png"
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"the {slug} shot could not be written to {path}")
        self.shots.append(
            Shot(
                slug=slug,
                title=title,
                width=image.width(),
                height=image.height(),
                callouts=[{"n": spot.number, "what": spot.what} for spot in spots],
            )
        )
        print(f"  {slug}.png  {image.width()}x{image.height()}  "
              f"{path.stat().st_size // 1024} KB  {len(spots)} callouts")

    def write_manifest(self) -> Path:
        path = self.folder / "shots.json"
        payload = {
            "shots": [
                {
                    "slug": shot.slug,
                    "file": f"{shot.slug}.png",
                    "width": shot.width,
                    "height": shot.height,
                    "title": shot.title,
                    "callouts": shot.callouts,
                }
                for shot in self.shots
            ]
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


def spot_in(widget: QWidget, host: QWidget, fx: float = 0.5, fy: float = 0.5) -> QPoint:
    """A place inside one widget, in another widget's own coordinates.

    Through the screen rather than through `mapTo`, because half of what is
    pointed at here lives inside a scroll area and is not a direct descendant of
    the thing being grabbed - and a coordinate worked out along the wrong parent
    chain is an arrow pointing confidently at the wrong control, which is worse
    than no arrow at all.
    """
    local = QPoint(int(round(widget.width() * fx)), int(round(widget.height() * fy)))
    return host.mapFromGlobal(widget.mapToGlobal(local))


# How far outside a panel a numbered circle sits, and how much page is kept
# beside the panel so that the circle has somewhere to be. The second has to be
# comfortably more than the first plus a radius, or the crop cuts the circles in
# half - which is a defect nobody notices in a thumbnail.
CIRCLE_MARGIN = 64
PANEL_MARGIN = 124


def panel_rect(host: QWidget, panel: QWidget, above: int = 20, below: int = 20):
    """The rectangle to crop for one settings panel, with its margins.

    The Settings form stops at 980 px and is centred, so on a 1600 px window
    there are about three hundred pixels of empty page either side of every
    panel. That is where the numbered circles go, which is why the crop is the
    panel plus a margin rather than the panel on its own.
    """
    top_left = spot_in(panel, host, 0.0, 0.0)
    bottom_right = spot_in(panel, host, 1.0, 1.0)
    return QRect(
        top_left.x() - PANEL_MARGIN,
        top_left.y() - above,
        bottom_right.x() - top_left.x() + 2 * PANEL_MARGIN,
        bottom_right.y() - top_left.y() + above + below,
    )


def beside(
    widget: QWidget,
    host: QWidget,
    panel: QWidget,
    what: str,
    side: str = "left",
    edge: bool = True,
    fx: float = 0.02,
    fy: float = 0.5,
):
    """A circle in the margin beside a panel, pointing at something inside it.

    Which margin is chosen by hand rather than worked out, because two controls
    on the same row - the two buttons under the Playback question, the slider
    and the box beside it - would otherwise be given the same place and drawn on
    top of one another.

    `edge` is what keeps the leader lines off the words. These are forms, and a
    form's labels are right-aligned against their fields - so an arrow drawn to
    the left-hand end of a field is drawn straight through the label naming it.
    Pointing at the edge of the panel on that row instead means the line stops
    before the first word of it, which is where a reader's eye starts anyway.
    Turn it off for a control that does not fill the row it is on, and the arrow
    goes to the control itself.
    """
    if edge:
        inside = 8
        x = (
            spot_in(panel, host, 0.0, 0.0).x() + inside
            if side == "left"
            else spot_in(panel, host, 1.0, 0.0).x() - inside
        )
        target = QPoint(x, spot_in(widget, host, 0.5, fy).y())
    else:
        target = spot_in(widget, host, fx, fy)
    if side == "left":
        x = spot_in(panel, host, 0.0, 0.0).x() - CIRCLE_MARGIN
    else:
        x = spot_in(panel, host, 1.0, 0.0).x() + CIRCLE_MARGIN
    return (target, QPoint(x, target.y()), what)


def past_the_tabs(window, fraction: float) -> QPoint:
    """A point in the empty part of the tab bar, across the window.

    The three tabs take about two hundred pixels and the bar is the width of the
    window, so everything to the right of them is a clear strip forty pixels
    high - the only room on the whole console for a circle about the band above
    it. Measured across the tab widget rather than across the QTabBar itself,
    which is only as wide as the tabs in it.
    """
    return QPoint(
        spot_in(window.tabs, window, fraction, 0.0).x(),
        spot_in(window.tabs.tabBar(), window, 0.5, 0.5).y(),
    )


def show_panel(app: QApplication, tab: QWidget, panel: QWidget) -> QWidget:
    """Scroll a settings panel into view, and hand it back.

    The form is about 1700 px tall inside a scroll area, so a panel below the
    fold has coordinates past the bottom of the tab and cannot be grabbed out of
    it at all. Everything on this tab is measured after this has been called.
    """
    tab._scroll.ensureWidgetVisible(panel, 0, 30)
    settle(app, 0.25)
    return panel


def settle(app: QApplication, seconds: float = 0.35) -> None:
    """Let Qt lay everything out, and let the workers answer.

    Two of the panels on the Live tab are read on daemon threads - the movement
    list and the recordings folder - so a shot taken the instant after the
    window is built is a shot of a console that has not been told anything yet.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def box_named(tab: QWidget, title: str) -> QGroupBox:
    """The panel on the Settings tab with this heading on it.

    Found by its heading rather than held as an attribute, because the Settings
    tab does not offer one and this script is not allowed to add it. A heading
    that is renamed stops this script rather than silently photographing the
    wrong panel.
    """
    for box in tab.findChildren(QGroupBox):
        if box.title() == title:
            return box
    raise LookupError(f"there is no {title!r} panel on the Settings tab")


# -------------------------------------------------------------- the fake world


def written_settings(path: Path) -> Settings:
    """A console set up the way a commissioned one is, written to a real file.

    The Settings tab reads the file rather than being handed an object, so this
    has to be on disk before the window is built. Everything in it is plausible
    and nothing in it is real: the addresses are the documentation range, and
    the passwords are invented.
    """
    settings = Settings()
    settings.title = "ירושלים"
    settings.screen = 1
    settings.show_playback = False
    settings.wall_view = ""
    settings.camera.host = "192.0.2.20"
    settings.camera.username = "admin"
    settings.camera.password = "Perimeter2026"
    settings.camera.streams = [
        StreamSettings(
            name="thermal",
            url="rtsp://192.0.2.20:554/ch1",
            enabled=True,
            detect=True,
            sensitivity="normal",
            thermal=True,
        ),
        StreamSettings(
            name="visible",
            url="rtsp://192.0.2.20:554/ch2",
            enabled=True,
            detect=False,
        ),
    ]
    settings.radio.host = "192.0.2.10"
    settings.radio.username = "ubnt"
    settings.radio.password = "LinkNorth2026"
    settings.radio.enabled = True
    # The folder the guide shows, not the temporary one this script works in.
    settings.storage.root = SHOWN_RECORDINGS
    settings.storage.budget_gb = 400.0
    settings.storage.retention_days = 30
    settings.detection.enabled = True
    settings.detection.alarm_sound = True
    settings.bitrate.mode = "auto"
    save_settings(settings, path)
    return settings


def healthy_disk(settings: Settings) -> DiskWatcher:
    """A drive with months of room on it, read once and never again.

    The reading is handed in rather than measured, so the storage panel says the
    same thing on every machine this script is ever run on. A guide whose disk
    figures depend on the laptop that built it is a guide that has to be rebuilt
    to be believed.
    """
    reading = DiskReading(
        at=time.time(),
        free_bytes=int(512 * 1024**3),
        used_bytes=int(268 * 1024**3),
        bytes_per_second=980_000.0,
        rate_is_estimate=False,
        newest_write=time.time(),
        writing=True,
        write_problem=None,
        problem=None,
    )
    watcher = DiskWatcher(settings, executor=lambda work: work(), read=lambda s, n: reading)
    watcher.poll()
    return watcher


def written_movements(path: Path) -> None:
    """Two movements on the thermal head, an hour and a few minutes ago.

    Two rather than one, because the movement line in the side column counts
    them and the singular and the plural are different sentences - and the guide
    should show the one the operator will see most nights.
    """
    store = EventStore(path)
    try:
        now = time.time()
        store.add(
            stream="thermal",
            started=now - 3600,
            ended=now - 3588,
            box=(410, 300, 22, 44),
            travelled_px=61.0,
        )
        store.add(
            stream="thermal",
            started=now - 260,
            ended=now - 244,
            box=(690, 318, 19, 40),
            travelled_px=48.0,
        )
    finally:
        store.close()


def written_segments(path: Path, recordings: Path) -> None:
    """A day of five-minute recordings on both heads, with one gap in it.

    The gap is on purpose. The timeline's whole job is showing what was recorded
    and what was not, and a bar that is one unbroken block says nothing about
    the difference - which is what the operator opens this tab to find out.
    """
    index = SegmentIndex(path)
    try:
        today = datetime.date.today()
        midnight = datetime.datetime(today.year, today.month, today.day).timestamp()
        now = time.time()
        for name in ("thermal", "visible"):
            start = midnight
            while start + 300 <= now:
                # Nothing between 04:10 and 05:20, which is what a link that
                # dropped in the night leaves behind.
                hour = (start - midnight) / 3600.0
                if not 4.15 < hour < 5.35:
                    index.add(
                        stream=name,
                        path=str(recordings / name / f"{int(start)}.mp4"),
                        start=start,
                        end=start + 300,
                        size_bytes=38_000_000,
                        commit=False,
                    )
                start += 300
        # One commit for the lot. Every insert above was told not to commit,
        # which for a day of five-minute segments on two cameras is five hundred
        # rows and five hundred flushes to the disk.
        index.commit()
    finally:
        index.close()


# ------------------------------------------------------------------- the shots


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    for stale in IMAGES.glob("*.png"):
        stale.unlink()
    (IMAGES / "shots.json").unlink(missing_ok=True)
    recordings = WORKING / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(stylesheet())

    settings_path = WORKING / "settings.json"
    settings = written_settings(settings_path)
    events_path = recordings / "events.db"
    written_movements(events_path)
    index_path = WORKING / "segments.db"
    written_segments(index_path, recordings)

    services = FakeServices(disk=healthy_disk(settings))
    radio = FakeRadio()
    window = ConsoleWindow(
        settings_path=settings_path,
        services=services,
        ptz=FakePtz(),
        radio=radio,
        index_path=index_path,
        # The Playback tab asks for a pane called "playback" and shows the
        # thermal head's recordings in it first, so that one is drawn thermal
        # too - a picture in the guide that does not match the camera named
        # above it is a page the operator will stop trusting.
        make_pane=lambda name: GuidePane(
            "thermal" if name in ("thermal", "playback") else "visible"
        ),
        events_path=events_path,
    )
    # Both timers off. The heartbeat would restart panes and redraw the band
    # between the moment a shot is set up and the moment it is grabbed, and the
    # blink would catch the recording dot on its dim beat - which on a printed
    # page is a dot that is simply the wrong colour with nothing to explain it.
    window._timer.stop()
    window._blink.stop()
    window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.show()
    settle(app, 0.4)
    # Twice, because the first reading of the movement list only establishes
    # what was already there - which is the console's rule against announcing a
    # night that was over before it opened.
    window.heartbeat()
    settle(app, 0.4)
    window.heartbeat()
    settle(app, 0.3)
    # And the timers the heartbeat started again.
    window._timer.stop()
    window._blink.stop()
    window.band.show_recording(True, True)
    app.processEvents()

    live = window.live
    album = Album(IMAGES)
    print(f"writing to {IMAGES}")

    shoot_live(app, album, window, live)
    shoot_band(app, album, window, services, radio)
    shoot_alarm(app, album, window, live)
    shoot_fullscreen(app, album, window, live)
    shoot_settings(app, album, window)
    shoot_logs(app, album, window)
    shoot_playback(app, album, window)
    shoot_mask(app, album)

    manifest = album.write_manifest()
    total = sum(p.stat().st_size for p in IMAGES.glob("*.png"))
    print(f"\n{len(album.shots)} shots, {total / 1024:.0f} KB of PNG")
    print(f"manifest: {manifest}")

    window.close()
    app.processEvents()
    # Best effort: sqlite leaves journal files about, and a temporary folder
    # that will not go away is not a reason to fail a run that produced every
    # image it was asked for.
    shutil.rmtree(WORKING, ignore_errors=True)
    return 0


# --------------------------------------------------------------------- the Live tab


def shoot_live(app: QApplication, album: Album, window, live) -> None:
    """The Live tab: the whole of it, then the three parts of it worth a page.

    Where the circles go is decided by where there is nothing: the video panes
    are near-black and mostly empty, the row above them has a wide gap between
    the view buttons and the way into fullscreen, and the tab bar is empty
    everywhere to the right of the three tabs. Nothing here is drawn over a word
    the operator has to read.
    """
    window.tabs.setCurrentWidget(live)
    settle(app, 0.2)

    bar = window.tabs.tabBar()
    thermal_plate = live._labels["thermal"]
    thermal_bar = live.zoom_bar("thermal")
    link = live._link_panel
    storage = live._storage_panel

    album.take(
        "live-tab",
        "The Live tab: the whole window",
        window,
        [
            (
                spot_in(window.band, window, 0.31, 1.0) + QPoint(0, -2),
                past_the_tabs(window, 0.40),
                "the band across the very top, which says whether the system as a "
                "whole is well: whether it is recording, whether pictures are "
                "arriving, whether anything is watching them, and how the radio "
                "link is",
            ),
            (
                spot_in(bar, window, 0.15, 0.06),
                past_the_tabs(window, 0.58),
                "the row of tabs that chooses which page is shown; the page you are "
                "on is the one with the amber bar under its name",
            ),
            (
                spot_in(live._title, window, 0.5, 1.0),
                spot_in(live._title, window, 0.5, 1.0) + QPoint(0, 80),
                "the name of what this camera watches, written in Hebrew above the "
                "pictures",
            ),
            (
                spot_in(live.views._buttons[1], window, 0.5, 1.0),
                spot_in(live.views._buttons[1], window, 0.5, 1.0) + QPoint(210, 90),
                "the buttons that choose which picture fills the wall: all of them "
                "side by side, or one of them on its own",
            ),
            (
                spot_in(live._fullscreen_button, window, 0.5, 1.0),
                spot_in(live._fullscreen_button, window, 0.5, 1.0) + QPoint(-60, 74),
                "the button that puts the pictures on the whole screen",
            ),
            (
                spot_in(thermal_plate, window, 0.26, 1.0),
                spot_in(thermal_plate, window, 0.26, 1.0) + QPoint(0, 112),
                "the plate above each picture, giving the name of that camera view "
                "and what it is doing right now",
            ),
            (
                spot_in(live._panes["thermal"], window, 0.5, 0.33),
                spot_in(live._panes["thermal"], window, 0.5, 0.33),
                "one of the two live pictures; dragging near its edge steers the "
                "camera, and so do the arrow keys",
            ),
            (
                spot_in(thermal_bar, window, 0.5, 0.0),
                spot_in(thermal_bar, window, 0.5, 0.0) + QPoint(0, -66),
                "the zoom bar belonging to that picture; there is one for each lens",
            ),
            (
                spot_in(link, window, 0.02, 0.16),
                spot_in(link, window, 0.02, 0.16) + QPoint(-130, 0),
                "the Link panel, which says in one word how the radio link between "
                "the camera and this laptop is doing",
            ),
            (
                spot_in(storage, window, 0.02, 0.3),
                spot_in(storage, window, 0.02, 0.3) + QPoint(-130, 0),
                "the Storage panel: how much of the allowed space the recordings "
                "have used, and how long before the oldest starts being deleted",
            ),
            (
                spot_in(live._movement_line, window, 0.0, 0.5),
                spot_in(live._movement_line, window, 0.0, 0.5) + QPoint(-150, 40),
                "the Movement line, which counts what has moved today and says when "
                "the last one was",
            ),
        ],
    )

    # The column of readings on its own, cropped with a slice of the picture
    # beside it. The slice is not decoration: the column is 370 px wide and
    # every pixel of it is a sentence, so the room for the circles has to come
    # from somewhere, and the near-black picture next to it is the only place on
    # this tab with nothing in it.
    side = live._side
    side_left = spot_in(side, window, 0.0, 0.0)
    side_right = spot_in(side, window, 1.0, 0.0)
    bottom = spot_in(live._movement_line, window, 0.0, 1.0).y() + 40
    album.take(
        "live-side-column",
        "The column of readings beside the pictures",
        window,
        [
            (
                spot_in(live._moving, window, 0.0, 0.5) + QPoint(-6, 0),
                QPoint(side_left.x() - 62, spot_in(live._moving, window, 0.0, 0.5).y()),
                "what the camera head is doing right now: idle, or the speed it is "
                "being driven at",
            ),
            (
                spot_in(live._keys_note, window, 0.0, 0.5) + QPoint(-6, 0),
                QPoint(side_left.x() - 62, spot_in(live._keys_note, window, 0.0, 0.5).y()),
                "the keys that steer the camera, written out",
            ),
            (
                spot_in(link, window, 0.0, 0.14) + QPoint(-6, 0),
                QPoint(side_left.x() - 62, spot_in(link, window, 0.0, 0.14).y()),
                "the one word for the state of the radio link: GOOD, FAIR, BUSY, "
                "FULL, WEAK or NO LINK",
            ),
            (
                spot_in(link, window, 0.0, 0.55) + QPoint(-6, 0),
                QPoint(side_left.x() - 62, spot_in(link, window, 0.0, 0.55).y()),
                "the two bars under it: how strong the signal is, and how much of "
                "the link is being used, each with the point where it stops being "
                "healthy marked on the track",
            ),
            (
                spot_in(storage, window, 0.0, 0.3) + QPoint(-6, 0),
                QPoint(side_left.x() - 62, spot_in(storage, window, 0.0, 0.3).y()),
                "how much space the recordings have used out of what they are "
                "allowed, and how long there is before the oldest is deleted",
            ),
            (
                spot_in(live._movement_line, window, 0.0, 0.5) + QPoint(-10, 0),
                QPoint(
                    side_left.x() - 62,
                    spot_in(live._movement_line, window, 0.0, 0.5).y(),
                ),
                "the movements counted today; pressing this line opens the footage "
                "of the newest one, when the Playback tab is switched on",
            ),
        ],
        rect=QRect(
            side_left.x() - 230,
            side_left.y() - 10,
            side_right.x() - side_left.x() + 240,
            bottom - side_left.y() + 10,
        ),
    )

    # The name row on its own. On the whole-window shot the Hebrew name, the
    # three view buttons and the way into fullscreen are four controls inside
    # one 30 px strip, which is four circles on top of one another.
    title_left = spot_in(live._title, window, 0.0, 0.0)
    row_top = spot_in(live.views, window, 0.0, 0.0).y()
    album.take(
        "view-chooser",
        "Choosing which picture is shown",
        window,
        [
            (
                spot_in(live._title, window, 0.5, 1.0),
                spot_in(live._title, window, 0.5, 1.0) + QPoint(0, 74),
                "the name of what this camera watches, in Hebrew",
            ),
            (
                spot_in(live.views._buttons[0], window, 0.5, 1.0),
                spot_in(live.views._buttons[0], window, 0.5, 1.0) + QPoint(0, 74),
                "the button that shows every camera view side by side; it is the one "
                "that is on here, marked by the amber bar beneath it",
            ),
            (
                spot_in(live.views._buttons[1], window, 0.5, 1.0),
                spot_in(live.views._buttons[1], window, 0.5, 1.0) + QPoint(0, 74),
                "a button that gives one camera view the whole wall on its own; "
                "there is one for each view the camera has",
            ),
            (
                spot_in(live._fullscreen_button, window, 0.5, 1.0),
                spot_in(live._fullscreen_button, window, 0.5, 1.0) + QPoint(0, 74),
                "the button into fullscreen, where only the pictures are left",
            ),
        ],
        rect=QRect(
            title_left.x() - 12,
            row_top - 14,
            spot_in(live._fullscreen_button, window, 1.0, 0.0).x() - title_left.x() + 24,
            210,
        ),
    )

    # The zoom bar, cropped with the bottom of the picture above it, so that it
    # is recognisable as the thing under a picture rather than a floating slider.
    bar_left = spot_in(thermal_bar, window, 0.0, 0.0)
    bar_right = spot_in(thermal_bar, window, 1.0, 1.0)
    album.take(
        "zoom-bar",
        "The zoom bar under a picture",
        window,
        [
            (
                spot_in(thermal_bar._out, window, 0.5, 0.0),
                spot_in(thermal_bar._out, window, 0.5, 0.0) + QPoint(0, -52),
                "the minus button, which zooms this lens out to a wider view",
            ),
            (
                spot_in(thermal_bar._slider, window, 0.42, 0.0),
                spot_in(thermal_bar._slider, window, 0.42, 0.0) + QPoint(0, -52),
                "the handle, which sits where the camera says the lens actually is; "
                "dragging it sends the lens somewhere",
            ),
            (
                spot_in(thermal_bar._in, window, 0.5, 0.0),
                spot_in(thermal_bar._in, window, 0.5, 0.0) + QPoint(0, -52),
                "the plus button, which zooms this lens in closer",
            ),
            (
                spot_in(thermal_bar._caption, window, 0.6, 0.0),
                spot_in(thermal_bar._caption, window, 0.6, 0.0) + QPoint(-20, -52),
                "the reading: the word zoom and how far in the lens is, as the "
                "camera itself reported it and never as a guess",
            ),
            (
                spot_in(live._panes["thermal"], window, 0.62, 1.0) + QPoint(0, -56),
                spot_in(live._panes["thermal"], window, 0.62, 1.0) + QPoint(0, -56),
                "the bottom of the picture this bar belongs to; the bar sits inside "
                "that picture's own frame, and the other picture has one of its own",
            ),
        ],
        rect=QRect(
            bar_left.x() - 8,
            bar_left.y() - 96,
            bar_right.x() - bar_left.x() + 16,
            bar_right.y() - bar_left.y() + 108,
        ),
    )


# --------------------------------------------------------------- the status band


def shoot_band(app: QApplication, album: Album, window, services, radio) -> None:
    """The band in both of its states, cropped with the tab bar under it.

    The circles sit in the tab bar, to the right of the three tabs, because the
    band itself is one line of type: an image 24 px tall printed 16 cm wide is a
    strip two millimetres high with nowhere to put a number.
    """
    band = window.band
    bar = window.tabs.tabBar()
    rect = QRect(0, 0, window.width(), spot_in(bar, window, 0.0, 1.0).y() + 24)

    def marks() -> list:
        return [
            (
                spot_in(band._chips[0], window, 0.10, 1.0) + QPoint(0, 2),
                past_the_tabs(window, 0.36),
                "the recording dot: a red circle that pulses while footage is "
                "reaching the disk, and a still red bar when it is not",
            ),
            (
                spot_in(band._chips[0], window, 0.75, 1.0) + QPoint(0, 2),
                past_the_tabs(window, 0.56),
                "each part of the system in turn - recording, pictures, movement "
                "watching, the radio link - named on its own while there is "
                "nothing wrong with it, and spelled out in full the moment there "
                "is",
            ),
            (
                spot_in(band._chips[3], window, 0.5, 1.0) + QPoint(0, 2),
                past_the_tabs(window, 0.76),
                "the radio link between the camera and this laptop; the figures "
                "behind it are in the Link panel on the Live tab",
            ),
        ]

    services.mood = "well"
    radio.mood = "well"
    window.band.show_parts(window.status_parts())
    window.band.show_recording(True, True)
    settle(app, 0.15)
    album.take(
        "status-band-healthy",
        "The status band with nothing wrong",
        window,
        marks(),
        rect=rect,
    )

    services.mood = "trouble"
    radio.mood = "trouble"
    window.band.show_parts(window.status_parts())
    window.band.show_recording(False, True)
    settle(app, 0.15)
    album.take(
        "status-band-trouble",
        "The status band with something wrong",
        window,
        marks(),
        rect=rect,
    )

    services.mood = "well"
    radio.mood = "well"
    window.band.show_parts(window.status_parts())
    window.band.show_recording(True, True)
    settle(app, 0.15)


# --------------------------------------------------------------------- the alarm


def shoot_alarm(app: QApplication, album: Album, window, live) -> None:
    """The alarm strip up, on the whole window and then close.

    Raised by setting the strip rather than by feeding the tab a movement: the
    real path makes a sound, and this script runs on somebody's desk.
    """
    when = datetime.datetime.now().strftime("%H:%M:%S")
    live._alarm_label.setText(f"Movement on thermal at {when}")
    live._alarm.setVisible(True)
    live._outline("thermal")
    settle(app, 0.2)

    album.take(
        "live-alarm",
        "The Live tab with an alarm up",
        window,
        [
            (
                spot_in(live._alarm, window, 0.30, 0.0),
                spot_in(live._alarm, window, 0.30, 0.0) + QPoint(0, -70),
                "the red strip that appears under the pictures when something has "
                "moved, saying which camera saw it and at what time",
            ),
            (
                spot_in(live._frames["thermal"], window, 0.5, 0.0) + QPoint(0, 2),
                spot_in(live._frames["thermal"], window, 0.5, 0.0) + QPoint(0, 92),
                "the red outline drawn round the picture the movement was seen on",
            ),
            (
                spot_in(live.acknowledge_button, window, 0.5, 0.0),
                spot_in(live.acknowledge_button, window, 0.5, 0.0) + QPoint(-40, -74),
                "the button that clears the strip and the red outline once the "
                "movement has been seen",
            ),
            (
                spot_in(live._movement_line, window, 0.0, 0.5) + QPoint(-10, 0),
                spot_in(live._movement_line, window, 0.0, 0.5) + QPoint(-150, 40),
                "the same movement counted in the column beside the pictures, where "
                "it stays after the strip has been cleared",
            ),
        ],
    )

    strip_left = spot_in(live._alarm, window, 0.0, 0.0)
    strip_right = spot_in(live._alarm, window, 1.0, 1.0)
    album.take(
        "alarm-strip",
        "The alarm strip, close up",
        window,
        [
            (
                spot_in(live._alarm, window, 0.015, 0.0),
                spot_in(live._alarm, window, 0.015, 0.0) + QPoint(20, -104),
                "the filled bar drawn beside the words, so that the alarm is not "
                "carried by the colour alone",
            ),
            (
                spot_in(live._alarm_label, window, 0.25, 0.0),
                spot_in(live._alarm_label, window, 0.25, 0.0) + QPoint(0, -104),
                "which camera view the movement was seen on, and the time it "
                "happened",
            ),
            (
                spot_in(live.acknowledge_button, window, 0.0, 0.0),
                spot_in(live.acknowledge_button, window, 0.0, 0.0) + QPoint(-40, -104),
                "the button that takes the strip down again",
            ),
        ],
        # The circles go above the strip: it is the last thing on the tab, and
        # there is nothing below it to put them in.
        rect=QRect(
            strip_left.x() - 10,
            strip_left.y() - 148,
            strip_right.x() - strip_left.x() + 20,
            strip_right.y() - strip_left.y() + 152,
        ),
    )

    live.acknowledge()
    settle(app, 0.15)


# ---------------------------------------------------------------- the fullscreen


def shoot_fullscreen(app: QApplication, album: Album, window, live) -> None:
    """The mode with everything but the pictures taken away.

    The mode is entered for real - `window.fullscreen.enter()` - and then the
    window is brought back to the size every other shot is taken at. The mode
    itself is untouched by that: it hides the band, the tab bar and the side
    column and asks the window to fill the screen, and only the last of those
    three is being overruled here. What is on the page is exactly what is on the
    screen in fullscreen, at the width the rest of the guide is printed at
    rather than at whatever monitor this script happened to run on.
    """
    window.fullscreen.enter()
    app.processEvents()
    window.showNormal()
    window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    settle(app, 0.3)

    album.take(
        "fullscreen",
        "Fullscreen: only the pictures",
        window,
        [
            (
                spot_in(live._title, window, 0.5, 1.0),
                spot_in(live._title, window, 0.5, 1.0) + QPoint(0, 66),
                "the name of what this camera watches, still on the screen in "
                "fullscreen: it is what says which of the two consoles this is",
            ),
            (
                spot_in(live._fullscreen_button, window, 0.5, 1.0),
                spot_in(live._fullscreen_button, window, 0.5, 1.0) + QPoint(-60, 70),
                "the way back out, which names its own key; Esc and F11 do the same",
            ),
            (
                spot_in(live.views._buttons[1], window, 0.5, 1.0),
                spot_in(live.views._buttons[1], window, 0.5, 1.0) + QPoint(230, 76),
                "the view buttons, which are kept in fullscreen",
            ),
            (
                spot_in(live.zoom_bar("visible"), window, 0.5, 0.0),
                spot_in(live.zoom_bar("visible"), window, 0.5, 0.0) + QPoint(0, -62),
                "the zoom bars, which are kept in fullscreen too",
            ),
        ],
    )

    window.fullscreen.leave()
    window.showNormal()
    window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    settle(app, 0.3)


# ------------------------------------------------------------- the Settings tab


def shoot_settings(app: QApplication, album: Album, window) -> None:
    """Every panel on the Settings tab, one shot each.

    Each one is a crop of the tab rather than a grab of the panel on its own,
    and the reason is the empty margin either side of it: the form stops at 980
    px and is centred, so there are three hundred pixels of page next to every
    panel with nothing in them - which is exactly where a numbered circle
    belongs. A circle drawn inside one of these panels would be sitting on the
    address the operator is being told to type.
    """
    tab = window.settings_tab
    # The tab asks the camera what lenses it has the first time it is shown, on
    # a worker. There is no camera here, and the answer arriving after this
    # script has closed the window is a signal delivered to a deleted object.
    tab._asked_about_lenses = True
    window.tabs.setCurrentWidget(tab)
    settle(app, 0.3)

    # The drive, said the same way on every machine. `load()` measured the real
    # drive this script is running on, which would put a different number in the
    # guide every time it is rebuilt.
    tab.disk_usage = lambda where: SimpleNamespace(
        total=int(1000 * 1024**3), used=int(430 * 1024**3), free=int(570 * 1024**3)
    )
    tab._footage_bytes = int(268 * 1024**3)
    tab._fit_the_slider_to_the_drive()
    tab._say_what_the_age_rule_does()
    settle(app, 0.2)

    camera = show_panel(app, tab, box_named(tab, "Camera"))
    album.take(
        "settings-camera",
        "Settings: the camera itself",
        tab,
        [
            beside(
                tab._title, tab, camera,
                "the name of what this camera watches, in your own words; it is "
                "written above the pictures and on the window itself",
            ),
            beside(
                tab._screen, tab, camera,
                "which monitor this console opens on, so that the two consoles do "
                "not land on top of each other after a restart",
            ),
            beside(tab._host, tab, camera, "the camera's address on the network"),
            beside(tab._username, tab, camera, "the username the camera expects"),
            beside(
                tab._password, tab, camera,
                "the camera's password, shown as it was typed and never hidden "
                "behind dots, so that a mistyped one can be seen and corrected",
            ),
        ],
        rect=panel_rect(tab, camera),
    )

    streams = show_panel(app, tab, box_named(tab, "Streams"))
    rows = tab.stream_rows()
    album.take(
        "settings-streams",
        "Settings: the camera's views",
        tab,
        [
            beside(
                rows[0].name_field, tab, streams,
                "the name of one camera view; it is the name this view goes by "
                "everywhere else in the console",
            ),
            beside(
                rows[0].url_field, tab, streams,
                "the address the camera serves that view on",
            ),
            beside(
                rows[0].detect_field, tab, streams,
                "the tick that watches this view for movement; it is ticked here, "
                "so this view is being watched",
                fx=0.06,
            ),
            beside(
                rows[1].detect_field, tab, streams,
                "the same tick on the other view, unticked: no view is watched for "
                "movement until it is asked for",
                side="right",
            ),
            beside(
                rows[0].mask_button, tab, streams,
                "the button that shows a picture from this view so that parts of it "
                "can be drawn round and ignored",
                fx=0.06,
            ),
            beside(
                rows[0].advanced_button, tab, streams,
                "a fold holding the one setting that makes a view more or less "
                "touchy; it is shut until it is needed",
                fx=0.06,
            ),
            beside(
                tab.swap_button, tab, streams,
                "the button for when the zoom bar under one picture moves the other "
                "picture; it exchanges the two lenses",
                side="right", edge=False, fx=1.0,
            ),
        ],
        rect=panel_rect(tab, streams),
    )

    detection = show_panel(app, tab, box_named(tab, "Movement detection"))
    album.take(
        "settings-detection",
        "Settings: movement detection and the alarm sound",
        tab,
        [
            beside(
                tab._detection_enabled, tab, detection,
                "the master switch: with it off nothing is watched for movement "
                "whatever the camera views say, and recording carries on either way",
                fx=0.03,
            ),
            beside(
                tab._alarm_sound, tab, detection,
                "the switch for the sound an alarm makes as well as the red strip; "
                "it never sounds more than once every twelve seconds, and it can be "
                "turned off if somebody sleeps in the room",
                fx=0.03,
            ),
        ],
        rect=panel_rect(tab, detection),
    )

    # The Playback panel, with the pane the tick raises. Raised by pressing the
    # tick itself, which is the only way the operator can ever see it.
    tab._show_playback.click()
    settle(app, 0.25)
    playback = show_panel(app, tab, box_named(tab, "Playback"))
    album.take(
        "settings-playback",
        "Settings: switching the Playback tab on, and the question it asks",
        tab,
        [
            beside(
                tab._show_playback, tab, playback,
                "the tick that adds the Playback tab to the console; the tab is off "
                "normally, and this tick stays down until the question below it has "
                "been answered",
                fx=0.03,
            ),
            beside(
                tab._ask_playback, tab, playback,
                "the \"Are you sure\" pane the tick raises, which says what "
                "switching the tab on brings back with it",
                side="right", edge=False, fx=1.0, fy=0.12,
            ),
            beside(
                tab.playback_yes, tab, playback,
                "the button that agrees, which is what actually switches the tab on",
                fx=0.06,
            ),
            beside(
                tab.playback_no, tab, playback,
                "the button that leaves it off and takes the question away",
                side="right", edge=False, fx=1.0,
            ),
        ],
        rect=panel_rect(tab, playback),
    )

    storage = show_panel(app, tab, box_named(tab, "Storage"))
    album.take(
        "settings-storage",
        "Settings: where the recordings go and how much room they may take",
        tab,
        [
            beside(tab._root, tab, storage, "the folder the recordings are written to"),
            beside(
                tab.scan_button, tab, storage,
                "the button that looks at the drive and fills in a size and an age "
                "rule to match it; it changes the two boxes below and nothing else, "
                "and nothing is written until Save",
                side="right", edge=False, fx=1.0,
            ),
            beside(
                tab.budget_slider, tab, storage,
                "how much of the drive the recordings are allowed to fill; the "
                "slider stops at the size of the drive",
            ),
            beside(
                tab._budget, tab, storage,
                "the same size as a number, which can be typed instead of dragged",
                side="right",
            ),
            beside(
                tab.budget_days_note, tab, storage,
                "what that size means in days of footage",
            ),
            beside(
                tab._days, tab, storage,
                "the age rule: anything older than this many days is deleted "
                "whether there is room for it or not; empty means nothing is "
                "deleted because of its age",
            ),
            beside(
                tab.retention_note, tab, storage,
                "the line saying which of the two rules is the one actually "
                "deleting footage today",
                side="right",
            ),
        ],
        rect=panel_rect(tab, storage),
    )

    radio = show_panel(app, tab, box_named(tab, "Radio"))
    album.take(
        "settings-radio",
        "Settings: the radio link",
        tab,
        [
            beside(
                tab._radio_host, tab, radio,
                "the radio's address; without it the Link panel on the Live tab has "
                "nothing to read",
            ),
            beside(tab._radio_user, tab, radio, "the radio's username"),
            beside(
                tab._radio_password, tab, radio,
                "the radio's password, shown as typed like every other password here",
            ),
            beside(
                tab.link_auto_field, tab, radio,
                "the switch that lets the console ask the camera for a smaller "
                "picture by itself while the link is busy, and for a better one "
                "again once it has been quiet",
                fx=0.03,
            ),
        ],
        rect=panel_rect(tab, radio),
    )

    # The camera tools, opened. Shut by default and last on the page, so this is
    # a fold the operator only ever sees when he has gone looking for it.
    tab.tools_button.setChecked(True)
    settle(app, 0.3)
    tools = show_panel(app, tab, tab.tools_box)
    tools_rect = panel_rect(tab, tools)
    tools_rect.setTop(min(spot_in(tab.tools_button, tab, 0.0, 0.0).y() - 18, tools_rect.top()))
    album.take(
        "settings-camera-tools",
        "Settings: the tools for checking the camera",
        tab,
        [
            beside(
                tab.tools_button, tab, tools,
                "the fold holding the camera tools; it is shut until it is pressed, "
                "and nothing inside it changes a setting",
                fx=0.03,
            ),
            beside(
                tab.test_button, tab, tools,
                "the button that asks whether the camera is answering at all",
                fx=0.06,
            ),
            beside(
                tab.find_button, tab, tools,
                "the button that hunts for the camera's address when nobody knows it",
                side="right",
            ),
            beside(
                tab.lens_button, tab, tools,
                "the button that asks which lens is behind which picture",
                fx=0.06,
            ),
            beside(
                tab.fit_button, tab, tools,
                "the button that asks the camera for a smaller picture, once, when "
                "the link cannot carry the one it is sending",
                side="right",
            ),
            beside(
                tab.report_button, tab, tools,
                "the button that writes everything it found into a file that can be "
                "sent to somebody",
                fx=0.06,
            ),
            beside(
                tab._output, tab, tools,
                "the box the answers appear in",
                side="right", fy=0.4,
            ),
        ],
        rect=tools_rect,
    )
    tab.tools_button.setChecked(False)
    settle(app, 0.2)

    # And the row the whole page exists for. The refusal is put on it first,
    # because the sentence is three lines long and the row grows to hold it -
    # measuring before it is there would cut it off the bottom of the crop.
    tab._set_message(
        "Not saved: the address rtsp:/192.0.2.20/ch1 is not a stream address - it "
        "must start with rtsp:// and the part after the address is the path the "
        "camera answers on."
    )
    settle(app, 0.3)
    # All the way down rather than "make this visible": this row is the last
    # thing on the page, so the only position that has the whole of it on screen
    # is the bottom of the scroll. Asked for a margin under it, Qt gives what it
    # can, which here is the button with its last few pixels off the viewport.
    bottom_stop = tab._scroll.verticalScrollBar()
    bottom_stop.setValue(bottom_stop.maximum())
    settle(app, 0.3)
    message_left = spot_in(tab._message, tab, 0.0, 0.0)
    save_right = spot_in(tab.save_button, tab, 1.0, 1.0)
    top = min(message_left.y(), spot_in(tab.save_button, tab, 0.0, 0.0).y())
    bottom = max(save_right.y(), spot_in(tab._message, tab, 1.0, 1.0).y())
    album.take(
        "settings-save",
        "Settings: saving, and what it says when it will not",
        tab,
        [
            (
                QPoint(message_left.x() - 6, spot_in(tab._message, tab, 0.5, 0.25).y()),
                QPoint(message_left.x() - 66, spot_in(tab._message, tab, 0.5, 0.25).y()),
                "the line that appears when a setting was refused, saying which one "
                "and why; nothing at all was saved while this is showing",
            ),
            (
                spot_in(tab.save_button, tab, 1.0, 0.5) + QPoint(-4, 0),
                QPoint(save_right.x() + 66, spot_in(tab.save_button, tab, 0.5, 0.5).y()),
                "the button that writes these settings to the file; nothing typed on "
                "this page takes effect until it is pressed, and Ctrl+S does the same",
            ),
        ],
        rect=QRect(
            message_left.x() - 132,
            top - 18,
            save_right.x() - message_left.x() + 264,
            bottom - top + 46,
        ),
    )
    tab._set_message("")
    settle(app, 0.15)


# ------------------------------------------------------------------ the Logs tab


def shoot_logs(app: QApplication, album: Album, window) -> None:
    """The Logs tab, with the kind of lines it really carries.

    The circles are in a row across the empty part of the table below the last
    line, and their leaders fan up to the controls. It is the one arrangement
    this tab allows: the controls are four adjacent buttons and a header, all in
    the top eighty pixels, and a circle beside any of them would be a circle on
    top of the next one.
    """
    logs = window.logs
    # Lines the operator would really be reading: the console's own, and the
    # streaming server's, which is where the reason for a missing picture is.
    now = time.time()
    lines = [
        ("INFO", "vmd.desktop.services", "go2rtc started on 127.0.0.1:1984"),
        ("INFO", "recorder", "recording thermal to recordings\\thermal"),
        ("INFO", "detector", "watching thermal"),
        ("WARNING", "vmd.desktop.live", "visible failed; restarting it"),
        (
            "ERROR",
            "go2rtc",
            "[streams] error: rtsp://192.0.2.20:554/ch2: 401 Unauthorized - the "
            "camera refused the username and password in Settings",
        ),
        (
            "INFO",
            "vmd.ptz.autobitrate",
            "the link is at 71% airtime; asking the camera for 2600 kb/s",
        ),
        ("INFO", "recorder", "segment written: 1755172800.mp4, 38.0 MB"),
        ("WARNING", "vmd.desktop.disk", "the recordings folder is 67% of the size it is allowed"),
    ]
    for index, (level, source, text) in enumerate(lines):
        logs._buffer.records.append(
            {
                "seq": 10_000 + index,
                "time": now - (len(lines) - index) * 37,
                "level": level,
                "source": source,
                "text": text,
            }
        )
    window.tabs.setCurrentWidget(logs)
    logs.refresh()
    settle(app, 0.3)

    # The row the circles stand on: below the last line, in the empty half of
    # the table. Measured off the table rather than typed, so that adding a line
    # above moves the row down with it.
    table = logs.table
    floor = spot_in(table, window, 0.0, 0.0).y() + 42 + 30 * (len(lines) + 3)
    error_row = spot_in(table, window, 0.0, 0.0).y() + 42 + 30 * 4 + 14

    album.take(
        "logs-tab",
        "The Logs tab",
        window,
        [
            (
                spot_in(logs.all_button, window, 0.5, 1.0),
                QPoint(spot_in(logs.all_button, window, 0.5, 1.0).x() + 60, floor),
                "the button showing every line; it is the one that is on, marked by "
                "the amber bar under it",
            ),
            (
                spot_in(logs.warnings_button, window, 0.5, 1.0),
                QPoint(spot_in(logs.warnings_button, window, 0.5, 1.0).x() + 180, floor),
                "the button that hides everything except the warnings and the errors",
            ),
            (
                spot_in(logs.copy_button, window, 0.5, 1.0),
                QPoint(spot_in(logs.copy_button, window, 0.5, 1.0).x() + 300, floor),
                "the button that copies the lines being shown, so that they can be "
                "pasted somewhere and sent to somebody",
            ),
            (
                QPoint(spot_in(table, window, 0.205, 0.0).x(), error_row),
                QPoint(spot_in(table, window, 0.44, 0.0).x(), floor),
                "a line in red, which is an error; this one is the camera refusing "
                "the username and password that are in Settings",
            ),
            (
                spot_in(table, window, 0.22, 0.0) + QPoint(0, 12),
                QPoint(spot_in(table, window, 0.58, 0.0).x(), floor),
                "the four columns: when the line was written, how serious it is, "
                "which part of the system said it, and what it said",
            ),
            (
                spot_in(logs.follow_checkbox, window, 1.0, 0.5) + QPoint(-6, 0),
                QPoint(spot_in(logs.follow_checkbox, window, 1.0, 0.5).x() - 60, floor),
                "the tick that keeps the table on the newest line as lines arrive; "
                "untick it to stay where you have scrolled to while you read",
            ),
        ],
    )


# -------------------------------------------------------------- the Playback tab


def shoot_playback(app: QApplication, album: Album, window) -> None:
    """The Playback tab, which has to be switched on before it exists at all.

    Switched on here through the window's own method, which is what a Save on
    the Settings tab calls - so this is the tab as it really arrives, second on
    the bar between Live and Settings.
    """
    window.show_playback_tab(True)
    tab = window.playback
    window.tabs.setCurrentWidget(tab)
    settle(app, 0.4)
    # Two hours ago, which is a moment there is footage for. A fraction of the
    # day would be a fraction of a day that has not happened yet whenever this
    # script is run in the morning, and the tab would answer - correctly - that
    # there is no recording at a time that has not arrived.
    when = time.time() - 2 * 3600
    tab.click_at((when - tab.day_start) / (tab.day_end - tab.day_start))
    settle(app, 0.3)
    # The follow timer would move the clock between now and the grab.
    tab._follow.stop()
    app.processEvents()
    # Where a movement mark really is on the bar, asked of the tab rather than
    # worked out here: the marks are drawn from the same list, so a circle put
    # at a fraction of the day computed twice would be a circle pointing next to
    # one instead of at it.
    mark = tab.event_marks[0][0] if tab.event_marks else 0.35

    album.take(
        "playback-tab",
        "The Playback tab, once it has been switched on",
        window,
        [
            (
                spot_in(tab.date_selector, window, 0.5, 1.0),
                spot_in(tab.date_selector, window, 0.5, 1.0) + QPoint(0, 66),
                "the day being looked at; the buttons either side of it step one day "
                "back and one day forward",
            ),
            (
                spot_in(tab.stream_selector, window, 0.5, 1.0),
                spot_in(tab.stream_selector, window, 0.5, 1.0) + QPoint(0, 66),
                "which camera view is being looked back through",
            ),
            (
                spot_in(tab.readout, window, 0.5, 1.0),
                spot_in(tab.readout, window, 0.5, 1.0) + QPoint(0, 66),
                "the clock: the moment the picture below it is showing",
            ),
            (
                spot_in(tab.zoom_buttons["Whole day"], window, 0.5, 1.0),
                spot_in(tab.zoom_buttons["Whole day"], window, 0.5, 1.0) + QPoint(0, 68),
                "how much of the day the bar at the bottom covers: the whole of it, "
                "an hour, or ten minutes",
            ),
            (
                spot_in(tab.bar, window, 0.06, 0.35),
                spot_in(tab.bar, window, 0.06, 0.35) + QPoint(0, -76),
                "the bar for the day: the filled green parts are what was recorded, "
                "and the gaps are the times nothing was",
            ),
            (
                spot_in(tab.bar, window, mark, 0.10),
                spot_in(tab.bar, window, mark, 0.10) + QPoint(0, -62),
                "the marks on the bar where something moved; pressing one plays the "
                "footage of it",
            ),
            (
                spot_in(tab.transport.play_button, window, 0.5, 0.0),
                spot_in(tab.transport.play_button, window, 0.5, 0.0) + QPoint(-10, -64),
                "play and pause",
            ),
            (
                spot_in(tab.transport.speed_selector, window, 0.5, 0.0),
                spot_in(tab.transport.speed_selector, window, 0.5, 0.0) + QPoint(0, -64),
                "how fast the footage is played back",
            ),
            (
                spot_in(tab.mark_start, window, 0.5, 0.0),
                spot_in(tab.mark_start, window, 0.5, 0.0) + QPoint(0, -64),
                "the start of a piece of footage to keep; Mark end beside it is the "
                "other end of it",
            ),
            (
                spot_in(tab.save_clip, window, 0.5, 0.0),
                spot_in(tab.save_clip, window, 0.5, 0.0) + QPoint(0, -64),
                "the button that writes the marked piece to a folder you choose",
            ),
        ],
    )


# ------------------------------------------------------------- the masking dialog


def shoot_mask(app: QApplication, album: Album) -> None:
    """Where the parts of a picture that must be ignored are drawn.

    Given the same synthetic frame the panes draw and two areas already marked
    out, because an empty canvas would not show what a marked area looks like -
    which is the one thing this dialog has to teach.
    """
    frame = scene_image(1280, 720, "thermal")
    # A ragged band over the treeline, which is what the freehand tool exists
    # for, and a straight box over a strip of ground.
    treeline = [
        (60, 300), (170, 276), (250, 296), (352, 266), (455, 292), (560, 268),
        (668, 294), (775, 270), (884, 296), (990, 272), (1100, 298), (1215, 276),
        (1215, 214), (60, 214),
    ]
    road = [(120, 560), (620, 560), (620, 636), (120, 636)]
    dialog = MaskDialog(frame, [treeline, road])
    dialog.resize(1180, 800)
    dialog.show()
    settle(app, 0.4)

    album.take(
        "mask-dialog",
        "Marking the parts of a picture to ignore",
        dialog,
        [
            (
                spot_in(dialog.instructions, dialog, 0.67, 0.5),
                spot_in(dialog.instructions, dialog, 0.67, 0.5) + QPoint(250, 66),
                "the instruction: drag around anything that should be ignored, and "
                "click a marked area to take it off again",
            ),
            (
                spot_in(dialog.canvas, dialog, 0.5, 0.30),
                spot_in(dialog.canvas, dialog, 0.5, 0.30) + QPoint(0, 78),
                "an area already marked out, shaded in red over the picture; "
                "movement inside it is never reported",
            ),
            (
                spot_in(dialog.canvas, dialog, 0.29, 0.755),
                spot_in(dialog.canvas, dialog, 0.29, 0.755) + QPoint(-150, 0),
                "a second marked area, this one dragged as a box rather than drawn "
                "round",
            ),
            (
                spot_in(dialog.freehand_button, dialog, 0.5, 0.0),
                spot_in(dialog.freehand_button, dialog, 0.5, 0.0) + QPoint(0, -58),
                "the tool that draws round a shape freehand; it is the one that is "
                "on, marked by the amber bar beneath it",
            ),
            (
                spot_in(dialog.box_button, dialog, 0.5, 0.0),
                spot_in(dialog.box_button, dialog, 0.5, 0.0) + QPoint(60, -58),
                "the tool that drags a rectangle instead",
            ),
            (
                spot_in(dialog.undo_button, dialog, 0.5, 0.0),
                spot_in(dialog.undo_button, dialog, 0.5, 0.0) + QPoint(-20, -58),
                "the button that takes off the area drawn last",
            ),
            (
                spot_in(dialog.clear_button, dialog, 0.5, 0.0),
                spot_in(dialog.clear_button, dialog, 0.5, 0.0) + QPoint(30, -58),
                "the button that takes off every marked area at once",
            ),
            (
                spot_in(dialog.cancel_button, dialog, 0.5, 0.0),
                spot_in(dialog.freehand_button, dialog, 0.5, 0.0) + QPoint(560, -58),
                "the button that leaves the marked areas exactly as they were",
            ),
            (
                spot_in(dialog.use_button, dialog, 0.0, 0.4) + QPoint(4, 0),
                spot_in(dialog.freehand_button, dialog, 0.5, 0.0) + QPoint(380, -58),
                "the button that keeps what was drawn; Save on the Settings tab is "
                "what writes it to the file",
            ),
        ],
    )
    dialog.close()
    app.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
