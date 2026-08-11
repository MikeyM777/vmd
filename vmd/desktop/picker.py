"""A picture from the camera, to draw the sky line and the ignored patches on.

Both of those settings are native-frame pixel coordinates, and both were typed
blind: a spin box, no picture, and nothing on screen saying how tall the frame
is. Nobody can read "340" off a treeline. The design calls these regions
operator-painted; until this file they were operator-guessed, and the cost is
one-sided - a wrong sky line throws away real movement below it and never says
so, and a patch in the wrong place does the same over a rectangle.

Three things carry the weight here.

* The picture is fetched off the window thread, through the same QRunnable and
  QThreadPool seam the camera tools already use. It crosses a radio link and
  takes seconds; on the window thread that is a console that looks dead. While
  it is in flight, and if it fails, the picture area itself says so - that is
  the large space the operator is looking at, and it used to be #050607 and
  silent while the explanation sat in a 12 px label off to one side.
* It is asked of the local streaming server first and of the camera second. The
  camera is already being pulled across a >15 km, ~5 Mb/s link and re-served on
  this machine; a second full-rate connection to it is the contention this whole
  architecture exists to avoid. The recorder and the detector do it this way and
  this now does too.
* What is drawn is converted to the frame's own dots, never the preview's. The
  preview is scaled to fit whatever the dialog is, the setting is absolute, and
  confusing the two misplaces every patch without a word of complaint. That
  conversion is four small functions with no widget in them, so it can be
  checked at a preview smaller than the frame, larger than it, and letterboxed.
* No picture is never a dead end. The camera is off, or the link is down, or
  go2rtc has not been started - and the operator still has to be able to
  configure the machine, so the failure is one plain sentence and the numeric
  boxes in the form are left exactly as they were.

A blank or stale preview would be worse than none, because the operator would
trust the line they dragged onto it. So bytes that are not a picture are refused
in the same words as no bytes at all.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.live import WrappedNote
from vmd.desktop.style import PALETTE, SIZE_BAND, SIZE_SMALL, SPACE_SNUG, SPACE_STEP
from vmd.streaming.endpoint import is_live, local_source, read_endpoint

logger = logging.getLogger(__name__)

# Where the console records the streaming server it is running. The same
# constant and the same name as `vmd/detect_main.py` and `vmd/record_main.py`,
# and read the same way: relative to the working folder, which the launchers set
# to the folder the settings live in.
DEFAULT_ENDPOINT_PATH = Path("streaming.json")

# What the two places a picture can come from are called, on screen. Named
# rather than spelled out at each use: the operator reads these words, and "the
# streaming server on this machine" and "the camera" are different situations
# they can act differently on.
LOCAL = "the streaming server on this machine"
CAMERA = "the camera"

# How long a grab gets to produce one frame, in total.
#
# It was 20 s, chosen against a loopback grab. This one may cross a >15 km,
# ~5 Mb/s point-to-point link that go2rtc is already saturating with the live
# picture, and a still that needed 25 s came back as "the camera did not send a
# picture" - which is the exact complaint this whole file came from, with the
# operator left looking at a black rectangle. The wait is long because the link
# is slow; it is bounded because the console must never sit on a thread forever,
# and it happens off the window thread so nothing freezes while it runs.
GRAB_SECONDS = 45

# How long ffmpeg waits for the stream itself to say anything, in microseconds.
# Inside GRAB_SECONDS on purpose: this is the "nothing is arriving" limit and
# the one above is the "this has gone on long enough" limit. A contended link
# can genuinely go several seconds without delivering a packet.
STREAM_TIMEOUT_US = 10_000_000

# How long the dialog waits, at most, for a fetch to finish once it has been
# closed. Bounded separately from the grab above: this one is on the window
# thread, and a console frozen while the operator closes a dialog is exactly the
# failure this whole file exists to avoid.
CLOSE_WAIT_MS = 3000

# A press and a release this far apart, or less, in the preview's own dots, is a
# click rather than a drag. Nobody releases the mouse on the dot they pressed.
CLICK_SLOP = 4

# How many frames to decode before keeping one.
#
# It was ONE, and one frame off a live RTSP stream is routinely black, green or
# half-decoded: the decoder has not necessarily had a complete keyframe when the
# first picture comes out of it. ffmpeg exits 0 and writes a valid JPEG, so
# every guard below passed and the operator was handed a black rectangle to draw
# a sky line on - which is worse than the honest refusal, because there is
# nothing to act on.
#
# Twenty, with `-update 1` so each frame is written over the last and what
# survives is the twentieth. Twenty is past the first keyframe on any sane GOP -
# this camera is asked for one a second and sends 15 to 25 frames a second - and
# at those rates it is between one and one and a half seconds of stream. That
# matters: this crosses the radio link the live picture is already using, and
# the budget for a still is about a second, not several.
FRAMES_TO_DECODE = 20

# How much variation a picture must have before it is a picture.
#
# The unit is one standard deviation of brightness, measured on a grid of
# samples across the frame, out of 255.
#
# The number is deliberately low, and it is low because of the thermal head.
# A heat camera looking at a cold, flat perimeter at 700 m genuinely produces a
# low-contrast picture, and refusing a real thermal frame would be far worse
# than showing a dim one: it would take away the only way to place a sky line on
# the view that most needs one. So this is not a "looks dull" threshold. It is a
# "there is nothing here at all" threshold: a blank or half-decoded frame comes
# out under 1 even after JPEG, because every sample is the same value, while any
# real frame - including a flat thermal one, which still carries sensor noise
# across every pixel - is several times this.
BLANK_STANDARD_DEVIATION = 2.0

# How many samples that measurement is taken from. Read on a grid across the
# full-size frame rather than from a scaled-down copy: scaling averages, and
# averaging is precisely what would erase the noise that tells a real flat
# thermal picture apart from a blank one.
BLANK_SAMPLES = 64


class FrameUnavailable(RuntimeError):
    """No picture, in one sentence the operator can act on.

    Never carries the address it tried: that address has the password
    percent-encoded into it, and this text goes on screen.
    """


class ToolMissing(FrameUnavailable):
    """ffmpeg is not on this machine, so no address is worth trying.

    Its own kind because it is the one failure that cannot come out differently
    at the second place asked: asking the camera after the local server, with
    nothing to ask it with, would only replace a sentence naming the real
    problem with one about two quiet cameras.
    """


# --------------------------------------------------------------- the geometry
#
# Widget-free on purpose. Everything below is checkable at any preview size
# without a window, which is what makes "the settings are absolute" a fact
# rather than an intention.


def scale_of(view: QSize, frame: QSize) -> float:
    """How many preview dots one frame dot is drawn as, fitted and undistorted."""
    if frame.width() <= 0 or frame.height() <= 0:
        return 1.0
    return min(view.width() / frame.width(), view.height() / frame.height())


def origin_of(view: QSize, frame: QSize) -> QPointF:
    """Where the picture starts inside the preview.

    A preview is rarely the frame's shape, so the picture is centred and the
    rest is dead space. Forgetting this offset shifts every patch by the depth
    of the bars, which looks plausible and is wrong.
    """
    scale = scale_of(view, frame)
    return QPointF(
        (view.width() - frame.width() * scale) / 2.0,
        (view.height() - frame.height() * scale) / 2.0,
    )


def to_frame(point: QPoint | QPointF, view: QSize, frame: QSize) -> tuple[int, int]:
    """A place on the preview, as a dot of the real picture.

    Clamped into the picture: a click on the dead space either side is still a
    click, and a negative row of dots is not a setting anything can use.
    """
    scale = scale_of(view, frame)
    if scale <= 0:
        return (0, 0)
    origin = origin_of(view, frame)
    x = int((point.x() - origin.x()) / scale)
    y = int((point.y() - origin.y()) / scale)
    return (
        max(0, min(frame.width() - 1, x)),
        max(0, min(frame.height() - 1, y)),
    )


def to_view(x: float, y: float, view: QSize, frame: QSize) -> QPointF:
    """A dot of the real picture, as a place on the preview."""
    scale = scale_of(view, frame)
    origin = origin_of(view, frame)
    return QPointF(origin.x() + x * scale, origin.y() + y * scale)


def _to_edge(point: QPoint | QPointF, view: QSize, frame: QSize) -> tuple[int, int]:
    """A place on the preview, as an edge between dots rather than a dot.

    The corner of a box is not a dot the way the sky line is a row of dots: a
    box drawn to the far right of the picture has to be able to reach the far
    right of the picture, which the last dot's own index never does.
    """
    scale = scale_of(view, frame)
    if scale <= 0:
        return (0, 0)
    origin = origin_of(view, frame)
    x = round((point.x() - origin.x()) / scale)
    y = round((point.y() - origin.y()) / scale)
    return (
        max(0, min(frame.width(), int(x))),
        max(0, min(frame.height(), int(y))),
    )


def region_between(
    start: QPoint | QPointF, end: QPoint | QPointF, view: QSize, frame: QSize
) -> tuple[int, int, int, int]:
    """The box between two places on the preview, in the real picture's dots.

    Whichever way round it was drawn, and never running off the edge of the
    picture: half of people drag a rectangle upwards and to the left, and a
    patch with a negative width is not a patch.
    """
    x1, y1 = _to_edge(start, view, frame)
    x2, y2 = _to_edge(end, view, frame)
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return (left, top, right - left, bottom - top)


# ------------------------------------------------------------------ the picture


class FramePicker(QWidget):
    """One frame, with the sky line and the patches drawn over it.

    Click puts the line; drag draws a patch. Both are reported in the frame's
    own dots, so what leaves this widget is what goes in the settings file.

    When there is no frame it says why, here, in the middle of itself. That is
    not decoration: the waiting sentence used to live in a 12 px muted label in
    the column beside this widget and the failure at the bottom of the dialog,
    while this - the large area the operator is actually looking at - was filled
    with `--well`, which is #050607. The report that came back was "black, no
    text", and that was a fair description of what this dialog showed.
    """

    horizon_picked = Signal(int)
    region_drawn = Signal(object)

    # How often the waiting line is redrawn while a fetch is in flight. It says
    # how long it has been trying, because a static sentence could equally mean
    # "finished, and there was nothing" - and this fetch legitimately takes
    # seconds over the radio link.
    TICK_MS = 1000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._horizon: int | None = None
        self._regions: list[tuple[int, int, int, int]] = []
        self._selected = -1
        self._press: QPointF | None = None
        self._drag: QPointF | None = None
        # Not started, in flight, failed, shown. These were one thing -
        # `has_frame()` is False - and they are four different things to an
        # operator standing in front of them.
        self._state = "empty"
        self._words = ""
        self._seconds = 0
        self._waiting = QTimer(self)
        self._waiting.setInterval(self.TICK_MS)
        self._waiting.timeout.connect(self._tick)
        self.setMinimumSize(360, 240)
        self.setMouseTracking(False)

    # -- what it is showing -------------------------------------------------

    def has_frame(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def state(self) -> str:
        """`empty`, `waiting`, `failed` or `shown`."""
        return self._state

    def state_words(self) -> str:
        """What is written across the picture area right now."""
        return self._words

    def wait_for_picture(self) -> None:
        """A fetch has started. Say so, where the picture will be."""
        self._state = "waiting"
        self._seconds = 0
        self._say_waiting()
        self._waiting.start()

    def show_problem(self, words: str) -> None:
        """No picture, and the reason - in the middle of the empty area."""
        self._waiting.stop()
        self._state = "failed"
        self._words = words
        self.update()

    def _tick(self) -> None:
        self._seconds += self.TICK_MS // 1000
        self._say_waiting()

    def _say_waiting(self) -> None:
        counted = f" ({self._seconds} s so far)" if self._seconds else ""
        self._words = (
            f"Getting a picture from the camera...{counted}\n"
            f"It comes over the radio link, so it can take up to "
            f"{GRAB_SECONDS} seconds."
        )
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Nothing counts up behind a dialog nobody is looking at."""
        self._waiting.stop()
        super().hideEvent(event)

    def frame_size(self) -> QSize:
        return self._image.size() if self.has_frame() else QSize(0, 0)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self._waiting.stop()
        self._state = "shown"
        self._words = ""
        self.update()

    def horizon(self) -> int | None:
        return self._horizon

    def set_horizon(self, value: int | None) -> None:
        self._horizon = None if value is None else int(value)
        self.update()

    def regions(self) -> list[tuple[int, int, int, int]]:
        return list(self._regions)

    def set_regions(self, regions) -> None:
        self._regions = [tuple(int(n) for n in region) for region in regions]
        self.update()

    def select_region(self, index: int) -> None:
        self._selected = index
        self.update()

    # -- drawing on it ------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.has_frame() or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = QPointF(event.position())
        self._drag = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press is None:
            return
        self._drag = QPointF(event.position())
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press is None or not self.has_frame():
            return
        start, self._press = self._press, None
        self._drag = None
        end = QPointF(event.position())
        frame = self.frame_size()
        moved = max(abs(end.x() - start.x()), abs(end.y() - start.y()))
        if moved <= CLICK_SLOP:
            _x, y = to_frame(end, self.size(), frame)
            self.set_horizon(y)
            self.horizon_picked.emit(y)
            return
        region = region_between(start, end, self.size(), frame)
        if region[2] <= 0 or region[3] <= 0:
            return
        self._regions.append(region)
        self.update()
        self.region_drawn.emit(region)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PALETTE["well"]))
        if not self.has_frame():
            self._paint_state(painter)
            painter.end()
            return
        frame = self.frame_size()
        scale = scale_of(self.size(), frame)
        top_left = to_view(0, 0, self.size(), frame)
        picture = QRect(
            int(top_left.x()),
            int(top_left.y()),
            int(frame.width() * scale),
            int(frame.height() * scale),
        )
        painter.drawImage(picture, self._image)

        if self._horizon is not None:
            y = to_view(0, self._horizon, self.size(), frame).y()
            sky = QRect(picture.left(), picture.top(), picture.width(), int(y) - picture.top())
            painter.fillRect(sky, QColor(0, 0, 0, 110))
            painter.setPen(QPen(QColor(PALETTE["accent"]), 2))
            painter.drawLine(picture.left(), int(y), picture.right(), int(y))

        for index, (x, y, w, h) in enumerate(self._regions):
            corner = to_view(x, y, self.size(), frame)
            box = QRect(
                int(corner.x()), int(corner.y()), max(1, int(w * scale)), max(1, int(h * scale))
            )
            chosen = index == self._selected
            painter.fillRect(box, QColor(255, 83, 75, 90 if chosen else 50))
            painter.setPen(QPen(QColor(PALETTE["alarm"]), 3 if chosen else 1))
            painter.drawRect(box)

        if self._press is not None and self._drag is not None:
            painter.setPen(QPen(QColor(PALETTE["ink"]), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRect(self._press.toPoint(), self._drag.toPoint()))
        painter.end()

    def _paint_state(self, painter: QPainter) -> None:
        """Whatever there is to say, in the middle of the empty area.

        At the band size rather than the caption size: this is the only thing on
        screen at the moment it is drawn, and the operator reading it is the one
        who reported that this dialog was black and said nothing.
        """
        if not self._words:
            return
        painter.setPen(
            QColor(PALETTE["warn"] if self._state == "failed" else PALETTE["muted"])
        )
        font = painter.font()
        font.setPixelSize(SIZE_BAND)
        painter.setFont(font)
        # Inset, so a long sentence wraps inside the area rather than being cut
        # off at its edges.
        room = self.rect().adjusted(SPACE_STEP * 2, SPACE_STEP, -SPACE_STEP * 2, -SPACE_STEP)
        painter.drawText(
            room,
            int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
            self._words,
        )


# ------------------------------------------------------------- fetching a frame


class _FrameSignals(QObject):
    """The fetch talking back to the dialog it cannot touch directly."""

    done = Signal(object)


class _FrameJob(QRunnable):
    """One fetch, on a pool thread. The same shape as the camera tools' jobs.

    Whatever goes wrong comes back as a sentence rather than an exception on a
    thread nobody is watching: an unhandled one there would take the console
    with it and say nothing.
    """

    def __init__(self, work, signals: _FrameSignals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

    def run(self) -> None:
        try:
            result = self._work()
        except FrameUnavailable as exc:
            result = str(exc)
        except Exception as exc:  # noqa: BLE001 - a failed fetch must not end the console
            logger.exception("the picture could not be fetched")
            result = str(exc) or "The picture could not be fetched."
        self._signals.done.emit(result)


def _ffmpeg() -> str:
    from vmd.storage.recorder import find_tool

    return find_tool("ffmpeg")


def _camera_address(settings, stream: str) -> str:
    """The camera's own address for this view, with the login already in it.

    Built the same way the streaming server's is, so that a stream set to the
    ffmpeg reader - stored wrapped, with no host of its own until it is
    unwrapped - resolves to the same place go2rtc would send it.
    """
    from vmd.streaming.go2rtc import build_config, probe_target

    chosen = next((s for s in settings.camera.streams if s.name == stream), None)
    if chosen is None or not chosen.url:
        raise FrameUnavailable(
            f'"{stream}" has no address yet, so there is no picture to draw on. '
            f"Type the address in above and press Save first."
        )
    config = build_config(settings, 1984, 8554)
    return probe_target(config["streams"].get(stream, chosen.url))


def _sources_for(settings, stream: str, endpoint_path=None) -> list[tuple[str, str]]:
    """Where to ask for a frame, in the order to ask: local first, camera last.

    The link is a >15 km point-to-point at about 5 Mb/s and go2rtc is already
    pulling this exact stream across it. A second full-rate connection to the
    camera is the contention this whole architecture exists to avoid - it is
    what produced the original 20-40 second latency and the dropouts - and it is
    a slower way to get the same picture. The recorder and the detector both
    read from the local server and fall back to the camera; this used to be the
    odd one out, so it now does what they do, in `vmd/streaming/endpoint.py`'s
    own words rather than in a second arrangement of its own.

    The fallback is not decorative. go2rtc may be down, may be up and not yet
    connected to the camera, or may be a claim in a file left behind by a
    process that has gone - and a stale `streaming.json` pointing this at a dead
    port, reported as "the camera did not send a picture", is the confusion that
    cost the owner most of a morning. So the port is checked before it is
    trusted, and anything short of a picture falls through to the camera.
    """
    camera = _camera_address(settings, stream)
    sources: list[tuple[str, str]] = []
    endpoint = read_endpoint(endpoint_path or DEFAULT_ENDPOINT_PATH)
    if endpoint and is_live(endpoint):
        local = local_source(endpoint, stream)
        if local:
            sources.append((LOCAL, local))
    return sources + [(CAMERA, camera)]


def _without_secrets(text: str, settings) -> str:
    """The same words with every password taken back out of them.

    ffmpeg quotes the address it was given back inside its own errors, and that
    address carries the password percent-encoded into it - `p@ss/word` travels
    as `p%40ss%2Fword`, which a search for the typed form does not match at all.
    """
    for secret in (settings.camera.password, settings.radio.password):
        if not secret:
            continue
        for form in (secret, quote(secret, safe="")):
            text = text.replace(form, "****")
    return text


def blankness(image: QImage) -> float:
    """How much brightness varies across this picture, in levels out of 255.

    Sampled on a grid of the full-size frame rather than from a scaled copy,
    because scaling averages and averaging is exactly what would erase the
    sensor noise that tells a real, flat thermal picture apart from a blank one.
    """
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        return 0.0
    across = min(BLANK_SAMPLES, width)
    down = min(BLANK_SAMPLES, height)
    values: list[int] = []
    for row in range(down):
        y = row * height // down
        for column in range(across):
            x = column * width // across
            values.append(QColor(image.pixel(x, y)).lightness())
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def is_blank(image: QImage) -> bool:
    """Whether this is a picture at all, rather than a rectangle of one colour."""
    return blankness(image) < BLANK_STANDARD_DEVIATION


def grab_frame(settings, stream: str, seconds: int = GRAB_SECONDS, endpoint_path=None) -> bytes:
    """One frame from the named view, as the bytes of a picture.

    Asked of the local streaming server first and of the camera only if that
    has nothing - see `_sources_for` for why that way round, and why the
    fallback has to be real.

    ffmpeg rather than anything richer, because it is already on this machine -
    the offline install puts it beside go2rtc - and it is what everything else
    here reads a stream with.
    """
    sources = _sources_for(settings, stream, endpoint_path)
    failures: list[tuple[str, str]] = []
    for where, address in sources:
        try:
            return _grab_from(where, address, settings, stream, seconds)
        except ToolMissing:
            # Nothing to fetch a picture WITH. The next address would fail in
            # the same words and bury the ones that matter.
            raise
        except FrameUnavailable as exc:
            # Whichever way it failed, the next place is still worth asking:
            # go2rtc being up and not yet connected to the camera looks exactly
            # like the camera being off, from here.
            logger.info("no frame from %s for %s: %s", where, stream, exc)
            failures.append((where, str(exc)))
    raise FrameUnavailable(_no_picture(failures, seconds))


def _no_picture(failures: list[tuple[str, str]], seconds: int) -> str:
    """One sentence for a picture that did not arrive, naming what was asked.

    "The camera did not answer" and "nothing on this machine answered either"
    are different situations and an operator can act differently on them: the
    first sends them to the camera and the link, the second says the machine in
    front of them is part of it.
    """
    if len(failures) == 1:
        # One place asked - which, since the camera is always last, means the
        # camera alone - and its own sentence is the whole truth.
        return failures[0][1]
    asked = " or ".join(where for where, _said in failures)
    return (
        f"No picture came from {asked}, so there is nothing to draw on. Type "
        f"the numbers in instead, or press \"Test the camera\" to find out why "
        f"it is quiet."
    )


def _grab_from(where: str, address: str, settings, stream: str, seconds: int) -> bytes:
    """One frame from one address, or a sentence saying why not.

    `where` is what that address is called on screen. It is carried this far
    down because the sentences are the operator's, and "the camera sent no
    picture" is the wrong sentence about a streaming server on this laptop.

    Twenty frames, and the twentieth is the one kept. Taking the first decoded
    frame off a live stream is what produced the black rectangle the operator
    was being asked to draw on: the decoder has not necessarily had a whole
    keyframe by then, ffmpeg exits 0, and a valid JPEG of nothing passes every
    guard below. `-update 1` writes each frame over the same file, so the cost
    of the nineteen that are thrown away is the second of stream, not disk.
    """
    with tempfile.TemporaryDirectory(prefix="vmd-frame-") as folder:
        target = Path(folder) / "frame.jpg"
        try:
            run = subprocess.run(
                [
                    _ffmpeg(), "-hide_banner", "-loglevel", "error",
                    "-rtsp_transport", "tcp", "-timeout", str(STREAM_TIMEOUT_US),
                    "-i", address,
                    "-frames:v", str(FRAMES_TO_DECODE), "-update", "1",
                    "-f", "image2", "-y", str(target),
                ],
                capture_output=True, text=True, timeout=seconds, check=False,
            )
        except FileNotFoundError:
            raise ToolMissing(
                "The part of VMD that fetches pictures is not installed on this "
                "machine, so there is no picture to draw on. Type the numbers in "
                "instead."
            ) from None
        except subprocess.TimeoutExpired:
            raise FrameUnavailable(
                f"{where[0].upper()}{where[1:]} sent no picture within {seconds} "
                f"seconds. It may be switched off, or the radio link may be "
                f"down. Type the numbers in instead."
            ) from None
        except OSError as exc:
            raise FrameUnavailable(
                f"The picture could not be fetched: {_without_secrets(str(exc), settings)}"
            ) from None
        if not target.exists() or target.stat().st_size == 0:
            # What it actually said goes to the log, not to the operator. It is
            # "[tcp @ 000001bf] Connection to tcp://10.0.0.2:554 failed: Error
            # number -138 occurred", which tells a non-technical operator
            # nothing and tells whoever reads the Logs tab everything. The
            # button that turns that into sentences already exists on this tab.
            complaint = (run.stderr or "").strip().splitlines()
            if complaint:
                logger.info(
                    "no frame from %s: %s", stream, _without_secrets(complaint[0], settings)
                )
            raise FrameUnavailable(
                f"{where[0].upper()}{where[1:]} did not send a picture, so there "
                f"is nothing to draw on. Type the numbers in instead, or press "
                f"\"Test the camera\" to find out why it is quiet."
            )
        # What it said even when it worked. A grab that "succeeded" into a black
        # frame used to leave no trace anywhere at all, which is why that fault
        # took several rounds to find: ffmpeg's own complaints about a stream it
        # only half decoded are the first thing anyone would want to read, and
        # they were being thrown away on the one path where nothing else was
        # written down either.
        said = (run.stderr or "").strip().splitlines()
        if said:
            logger.info(
                "ffmpeg while fetching a frame from %s: %s",
                stream,
                _without_secrets("; ".join(said[:3]), settings),
            )
        return target.read_bytes()


# ------------------------------------------------------------------ the dialog


class PickerDialog(QDialog):
    """The picture, what has been drawn on it, and a way to undraw it.

    The delete path is the one that matters most today: an operator with a patch
    in the wrong place has no way out of it at all short of editing the settings
    file by hand, which nobody on this machine is ever going to do.
    """

    def __init__(
        self,
        stream: str,
        horizon: int | None,
        regions,
        grab,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Draw on the picture from {stream}")
        self._grab = grab
        self._closed = False
        self._signals = _FrameSignals()
        self._signals.done.connect(self._fetched)
        # This dialog's own, so that closing it waits for its own fetch and for
        # nothing else. One thread: there is one picture to fetch.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_STEP, SPACE_STEP, SPACE_STEP, SPACE_STEP)
        outer.setSpacing(SPACE_STEP)

        self.instructions = WrappedNote(
            "Click the picture to put the sky line where the ground stops. "
            "Drag a box over anything you want ignored - a tree that sways, a "
            "flag, a road."
        )
        outer.addWidget(self.instructions)

        self.picker = FramePicker()
        self.picker.set_horizon(horizon)
        self.picker.set_regions(regions)
        self.picker.horizon_picked.connect(self._horizon_picked)
        self.picker.region_drawn.connect(self._region_drawn)

        middle = QHBoxLayout()
        middle.setSpacing(SPACE_STEP)
        middle.addWidget(self.picker, 1)

        side = QVBoxLayout()
        side.setSpacing(SPACE_SNUG)
        self.horizon_label = WrappedNote("")
        self.clear_horizon_button = QPushButton("Take the sky line off")
        self.clear_horizon_button.clicked.connect(self._clear_horizon)
        self.regions_label = QLabel("Patches that are ignored:")
        self.regions_list = QListWidget()
        self.regions_list.currentRowChanged.connect(self.picker.select_region)
        self.remove_button = QPushButton("Delete the selected patch")
        self.remove_button.clicked.connect(self._remove_region)
        self.size_label = WrappedNote("")
        self.size_label.setStyleSheet(
            f"color: {PALETTE['muted']}; font-size: {SIZE_SMALL}px;"
        )
        for widget in (
            self.horizon_label,
            self.clear_horizon_button,
            self.regions_label,
            self.regions_list,
            self.remove_button,
            self.size_label,
        ):
            side.addWidget(widget)
        side.addStretch(1)
        middle.addLayout(side)
        # The slack goes to the picture, and to nothing else. Without this the
        # instructions above it - a note that asks for the height its text needs
        # - take the room the dialog grew by and float in the middle of it.
        outer.addLayout(middle, 1)

        # The failure is drawn in the picture area, in the middle of the space
        # the operator is already looking at. There used to be a second copy of
        # it in a label along the bottom of the dialog, and this console has a
        # rule about that which it learned from the status bar: two places
        # saying the same thing is one place too many, and the one that was
        # there was the one nobody read.

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.use_button = QPushButton("Use what I drew")
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.use_button)
        outer.addLayout(buttons)

        self._show_regions()
        self._show_horizon()
        self._start()

    # -- what the operator chose -------------------------------------------

    def horizon(self) -> int | None:
        return self.picker.horizon()

    def regions(self) -> list[tuple[int, int, int, int]]:
        return self.picker.regions()

    def horizon_text(self) -> str:
        return self.horizon_label.text()

    def size_text(self) -> str:
        return self.size_label.text()

    def problem_text(self) -> str:
        """The sentence saying why there is no picture, or nothing.

        Read off the picture area, because that is where it is written now.
        """
        return self.picker.state_words() if self.picker.state() == "failed" else ""

    def busy(self) -> bool:
        """Whether a fetch is still running. False once this dialog is closed."""
        return self._pool.activeThreadCount() > 0

    def words_on_screen(self) -> list[str]:
        """Every word this dialog puts in front of the operator."""
        return [
            self.windowTitle(),
            self.instructions.text(),
            self.horizon_label.text(),
            self.clear_horizon_button.text(),
            self.regions_label.text(),
            self.remove_button.text(),
            self.size_label.text(),
            self.cancel_button.text(),
            self.use_button.text(),
        ] + [self.regions_list.item(i).text() for i in range(self.regions_list.count())]

    # -- fetching -----------------------------------------------------------

    def _start(self) -> None:
        self.size_label.setText("Getting a picture from the camera...")
        # And in the middle of the picture area, which is where the operator is
        # looking. The label above is 12 px of muted text in the column beside
        # it; on its own it was invisible, and the dialog read as black and
        # silent for as long as the fetch took.
        self.picker.wait_for_picture()
        self._pool.start(_FrameJob(self._grab, self._signals))

    def _fetched(self, result) -> None:
        """A picture, or the sentence saying why there is not one.

        A result that arrives after the dialog has gone is dropped: the operator
        has closed it, and quietly filling in a preview behind them is the one
        thing worse than no preview.
        """
        if self._closed:
            return
        if not isinstance(result, (bytes, bytearray)):
            self._failed(str(result))
            return
        image = QImage()
        if not image.loadFromData(bytes(result)) or image.isNull():
            # Anything but a picture, including an empty file and an HTML error
            # page. Showing a blank rectangle would be worse than showing
            # nothing, because a line dragged onto it would be believed.
            self._failed(
                "What came back from the camera was not a picture, so there is "
                "nothing to draw on. Type the numbers in instead."
            )
            return
        if is_blank(image):
            # A valid JPEG of nothing. It happens when a stream is still coming
            # up, and it is the worst kind of failure this dialog has, because
            # nothing about it looks like a failure: the operator drags a sky
            # line onto a black rectangle and it is saved as a real setting that
            # quietly throws away everything above it.
            self._failed(
                "The picture that came back is blank, so there is nothing to "
                "draw on. The camera may still be starting up - try again in a "
                "moment, or type the numbers in instead."
            )
            return
        self.picker.set_frame(image)
        self.size_label.setText(
            f"This picture is {image.width()} dots across and {image.height()} "
            f"dots down. The sky line is counted from the top."
        )
        self._show_horizon()

    def _failed(self, text: str) -> None:
        # In the middle of the empty area, which is where the operator is
        # looking. The line beside it says something different and is kept: that
        # the numbers they already had are untouched.
        self.picker.show_problem(text)
        self.size_label.setText(
            "There is no picture, so the boxes in the settings are the way to "
            "set these. Nothing you had is changed."
        )

    # -- the drawing --------------------------------------------------------

    def _horizon_picked(self, y: int) -> None:
        self._show_horizon()

    def _region_drawn(self, region) -> None:
        self._show_regions()

    def _clear_horizon(self) -> None:
        self.picker.set_horizon(None)
        self._show_horizon()

    def _remove_region(self) -> None:
        row = self.regions_list.currentRow()
        if row < 0:
            return
        regions = self.picker.regions()
        del regions[row]
        self.picker.set_regions(regions)
        self.picker.select_region(-1)
        self._show_regions()

    def _show_horizon(self) -> None:
        value = self.picker.horizon()
        if value is None:
            self.horizon_label.setText("No sky line. Click the picture to put one.")
        else:
            self.horizon_label.setText(f"Sky line: {value} dots from the top.")

    def _show_regions(self) -> None:
        self.regions_list.clear()
        for x, y, w, h in self.picker.regions():
            self.regions_list.addItem(f"{w} x {h} dots, at {x} across and {y} down")

    # -- closing ------------------------------------------------------------

    def done(self, result: int) -> None:  # noqa: D102 - Qt's own
        """Close, and take the fetch with it.

        Every wait here is bounded on its own: the fetch itself gives up on the
        camera after GRAB_SECONDS, and this waits at most CLOSE_WAIT_MS for it.
        A fetch that outlives even that emits into signals nothing is connected
        to any more, which is why it is disconnected before the wait rather than
        after it.
        """
        self._closed = True
        try:
            self._signals.done.disconnect(self._fetched)
        except (RuntimeError, TypeError):  # already disconnected
            pass
        if not self._pool.waitForDone(CLOSE_WAIT_MS):
            logger.warning("the picture fetch is still running; it will be ignored")
        super().done(result)
