"""Does a transparent Qt widget survive on top of libVLC's video surface?

The Live tab wants the pointer over the picture for steering, which means a
widget above VLC's own output. On Windows that is not guaranteed to composite
cleanly. Ten minutes here decides whether the steering overlay is possible or
whether steering moves to a side strip.

Run:  uv run python spike/overlay_probe.py rtsp://127.0.0.1:8554/thermal

The probe is instrumented because nobody can prove "it looked fine" later. Once
a second it prints frames/paints/covered, and with --grab it saves a QWidget
grab of the window and exits, so the whole verdict is reproducible by rerunning
the file instead of by remembering what the window looked like:

    uv run python spike/overlay_probe.py rtsp://... --seconds 20 --grab out.png

  frames  = libVLC's displayed-pictures counter. Must climb, or the "overlay is
            fine" verdict would only be a claim about a still black rectangle.
  paints  = paintEvent calls on the overlay. Must climb at ~30/s, or the overlay
            is not repainting above the video at all.
  covered = overlay geometry equals the video surface rect and it is visible.
            Painting somewhere off the picture would prove nothing.

Note on --grab: on Windows QWidget.grab() reads back Qt's own painting, so the
VLC surface itself can come back black. That is expected and is NOT flicker.
What the grab proves is that the amber overlay pixels are present on top.
"""

from __future__ import annotations

import sys

import vlc
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

OVERLAY_COLOR = "#EEBB58"


class Overlay(QWidget):
    """A transparent widget that draws a moving box and some text."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.offset = 0
        self.paints = 0  # counted so the 1 Hz report can prove repainting
        timer = QTimer(self)
        timer.timeout.connect(self._advance)
        timer.start(33)

    def _advance(self) -> None:
        self.offset = (self.offset + 4) % max(1, self.width())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.paints += 1
        painter = QPainter(self)
        painter.setPen(QPen(QColor(OVERLAY_COLOR), 2))
        painter.drawRect(self.offset, 40, 160, 120)
        painter.drawText(20, 24, "overlay: if this flickers, say so")


def _displayed_pictures(player: vlc.MediaPlayer) -> int:
    """libVLC's count of pictures actually pushed to the display, or -1."""
    media = player.get_media()
    if media is None:
        return -1
    stats = vlc.MediaStats()
    if not media.get_stats(stats):
        return -1
    return int(stats.displayed_pictures)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    url = ""
    grab_path = ""
    seconds = 0
    i = 0
    while i < len(args):
        if args[i] == "--grab" and i + 1 < len(args):
            grab_path = args[i + 1]
            i += 2
        elif args[i] == "--seconds" and i + 1 < len(args):
            seconds = int(args[i + 1])
            i += 2
        else:
            url = args[i]
            i += 1

    if not url:
        print("give an rtsp:// or file url as the argument")
        return 1

    app = QApplication(sys.argv[:1])
    window = QWidget()
    window.setWindowTitle("VMD overlay probe")  # titled so a screen grab can find it
    window.resize(960, 600)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)

    surface = QWidget()
    surface.setStyleSheet("background: #050607;")
    layout.addWidget(surface, 1)
    layout.addWidget(QLabel("move the mouse over the picture; watch for tearing"))

    window.show()

    instance = vlc.Instance(["--no-audio", "--rtsp-tcp", "--network-caching=300"])
    player = instance.media_player_new()
    player.set_media(instance.media_new(url))
    player.set_hwnd(int(surface.winId()))
    player.play()

    overlay = Overlay(surface)
    overlay.setGeometry(surface.rect())
    overlay.raise_()
    overlay.show()

    def keep_covering() -> None:
        overlay.setGeometry(surface.rect())
        overlay.raise_()

    resize_timer = QTimer()
    resize_timer.timeout.connect(keep_covering)
    resize_timer.start(500)

    # The instrumentation. A probe nobody can verify is not a probe: these three
    # numbers are the whole evidence that the overlay is painting on top of a
    # live picture rather than over a frozen or absent one.
    elapsed = {"s": 0}

    def report() -> None:
        elapsed["s"] += 1
        covered = overlay.geometry() == surface.rect() and overlay.isVisible()
        print(
            f"t={elapsed['s']}s frames={_displayed_pictures(player)} "
            f"paints={overlay.paints} covered={covered}",
            flush=True,
        )
        if grab_path and elapsed["s"] == max(1, min(5, seconds - 1 if seconds else 5)):
            window.grab().save(grab_path)
            print(f"grab saved: {grab_path}", flush=True)
        if seconds and elapsed["s"] >= seconds:
            app.quit()

    report_timer = QTimer()
    report_timer.timeout.connect(report)
    report_timer.start(1000)

    code = app.exec()
    player.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
