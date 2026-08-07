"""Ground-truth labelling tool.

Mark the spans of time where a person or a vehicle is genuinely present in a
clip. The output JSON is what turns "the alarms felt about right" into a
measurable recall / false-alarm number.

    uv run python spike/label_tool.py footage/walk_3mbps.mp4

Keys
    Space           play / pause
    Right / Left    step one frame
    . / ,           jump one second
    Shift+. / ,     jump ten seconds
    P               start a PERSON span here; press again to close it
    V               start a VEHICLE span here; press again to close it
    Esc             cancel the span currently being marked
    Delete          delete the selected span in the list
    Ctrl+S          save

Labels are saved next to the video as <video>.labels.json:

    {
      "video": "walk_3mbps.mp4",
      "fps": 29.98, "duration": 107.2, "frames": 3214,
      "spans": [{"label": "person", "start": 17.4, "end": 23.9}, ...]
    }

A span means "a real person/vehicle is visible in frame during this time".
Anything the system alarms on outside every span is a false alarm; any span
with no alarm inside it is a miss.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

PERSON = "person"
VEHICLE = "vehicle"
COLOURS = {PERSON: "#2ecc71", VEHICLE: "#3498db"}


@dataclass
class Span:
    label: str
    start: float
    end: float


class Timeline(QWidget):
    """Horizontal bar showing every labelled span plus the playhead."""

    def __init__(self, on_seek) -> None:
        super().__init__()
        self.setMinimumHeight(54)
        self.spans: list[Span] = []
        self.duration = 1.0
        self.position = 0.0
        self.pending: tuple[str, float] | None = None
        self._on_seek = on_seek

    def mousePressEvent(self, event) -> None:
        if self.width():
            self._on_seek(self.duration * event.position().x() / self.width())

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1b1b1b"))
        width, height = self.width(), self.height()
        if self.duration <= 0:
            return

        rows = {PERSON: (6, 18), VEHICLE: (28, 18)}
        for label, (top, tall) in rows.items():
            painter.fillRect(0, top, width, tall, QColor("#262626"))
            painter.setPen(QColor("#666"))
            painter.drawText(4, top + tall - 5, label[0].upper())

        for span in self.spans:
            top, tall = rows[span.label]
            x1 = int(width * span.start / self.duration)
            x2 = int(width * span.end / self.duration)
            painter.fillRect(x1, top, max(x2 - x1, 2), tall, QColor(COLOURS[span.label]))

        if self.pending:
            label, start = self.pending
            top, tall = rows[label]
            x1 = int(width * start / self.duration)
            x2 = int(width * self.position / self.duration)
            painter.fillRect(
                min(x1, x2), top, max(abs(x2 - x1), 2), tall, QColor(COLOURS[label]).lighter(160)
            )

        x = int(width * self.position / self.duration)
        painter.fillRect(x - 1, 0, 2, height, QColor("#e74c3c"))


class LabelWindow(QMainWindow):
    def __init__(self, video_path: Path) -> None:
        super().__init__()
        self.video_path = video_path
        self.capture = cv2.VideoCapture(str(video_path))
        if not self.capture.isOpened():
            raise RuntimeError(f"could not open video: {video_path}")
        self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.frame_count / self.fps
        self.frame_index = 0
        self.spans: list[Span] = []
        self.pending: tuple[str, float] | None = None

        self.setWindowTitle(f"VMD labeller — {video_path.name}")
        self.video = QLabel(alignment=Qt.AlignCenter)
        self.video.setMinimumSize(520, 700)
        self.video.setStyleSheet("background: #000;")

        self.info = QLabel()
        self.hint = QLabel(
            "Space play/pause · ←→ frame · , . second · P person span · V vehicle span "
            "· Esc cancel · Del remove · Ctrl+S save"
        )
        self.hint.setStyleSheet("color: #888;")

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(self.frame_count - 1, 0))
        self.slider.sliderMoved.connect(self.seek_frame)

        self.timeline = Timeline(self.seek_time)
        self.timeline.duration = self.duration

        self.span_list = QListWidget()
        self.span_list.setMaximumWidth(260)

        save_button = QPushButton("Save labels")
        save_button.clicked.connect(self.save)
        load_button = QPushButton("Load labels")
        load_button.clicked.connect(self.load_dialog)
        delete_button = QPushButton("Delete selected")
        delete_button.clicked.connect(self.delete_selected)

        side = QVBoxLayout()
        side.addWidget(QLabel("Labelled spans"))
        side.addWidget(self.span_list, 1)
        side.addWidget(delete_button)
        side.addWidget(load_button)
        side.addWidget(save_button)

        centre = QVBoxLayout()
        centre.addWidget(self.video, 1)
        centre.addWidget(self.slider)
        centre.addWidget(self.timeline)
        centre.addWidget(self.info)
        centre.addWidget(self.hint)

        layout = QHBoxLayout()
        layout.addLayout(centre, 1)
        layout.addLayout(side)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)

        self._shortcut("Space", self.toggle_play)
        self._shortcut("Right", lambda: self.step(1))
        self._shortcut("Left", lambda: self.step(-1))
        self._shortcut(".", lambda: self.step(int(self.fps)))
        self._shortcut(",", lambda: self.step(-int(self.fps)))
        self._shortcut("Shift+.", lambda: self.step(int(self.fps * 10)))
        self._shortcut("Shift+,", lambda: self.step(-int(self.fps * 10)))
        self._shortcut("P", lambda: self.mark(PERSON))
        self._shortcut("V", lambda: self.mark(VEHICLE))
        self._shortcut("Esc", self.cancel_pending)
        self._shortcut("Delete", self.delete_selected)
        self._shortcut("Ctrl+S", self.save)

        existing = self.labels_path()
        if existing.exists():
            self.load(existing)
        self.show_frame(0)

    # ---------------------------------------------------------------- helpers

    def _shortcut(self, keys: str, handler) -> None:
        QShortcut(QKeySequence(keys), self, activated=handler)

    def labels_path(self) -> Path:
        return self.video_path.with_suffix(self.video_path.suffix + ".labels.json")

    @property
    def time(self) -> float:
        return self.frame_index / self.fps

    # ------------------------------------------------------------- navigation

    def show_frame(self, index: int) -> None:
        index = max(0, min(index, max(self.frame_count - 1, 0)))
        if index != self.frame_index + 1 or index == 0:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = self.capture.read()
        if not ok:
            self.pause()
            return
        self.frame_index = index

        height, width, _ = image.shape
        rgb = image[:, :, ::-1].copy()
        pixmap = QPixmap.fromImage(QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888))
        self.video.setPixmap(
            pixmap.scaled(self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

        self.slider.setValue(index)
        self.timeline.position = self.time
        self.timeline.pending = self.pending
        self.timeline.update()

        pending_text = ""
        if self.pending:
            pending_text = f"   ● marking {self.pending[0]} from {self.pending[1]:.2f}s"
        self.info.setText(
            f"frame {index}/{self.frame_count - 1}   {self.time:6.2f}s / {self.duration:.2f}s"
            f"   spans: {len(self.spans)}{pending_text}"
        )

    def seek_frame(self, index: int) -> None:
        self.show_frame(int(index))

    def seek_time(self, seconds: float) -> None:
        self.show_frame(int(seconds * self.fps))

    def step(self, delta: int) -> None:
        self.pause()
        self.show_frame(self.frame_index + delta)

    def advance(self) -> None:
        if self.frame_index >= self.frame_count - 1:
            self.pause()
            return
        self.show_frame(self.frame_index + 1)

    def toggle_play(self) -> None:
        if self.timer.isActive():
            self.pause()
        else:
            self.timer.start(int(1000 / self.fps))

    def pause(self) -> None:
        self.timer.stop()

    # ---------------------------------------------------------------- marking

    def mark(self, label: str) -> None:
        if self.pending and self.pending[0] == label:
            start = self.pending[1]
            end = self.time
            self.pending = None
            if end <= start:
                start, end = end, start
            if end - start < 1e-3:
                self.show_frame(self.frame_index)
                return
            self.spans.append(Span(label, round(start, 2), round(end, 2)))
            self.spans.sort(key=lambda s: (s.start, s.label))
            self.refresh_list()
        else:
            self.pending = (label, self.time)
        self.show_frame(self.frame_index)

    def cancel_pending(self) -> None:
        self.pending = None
        self.show_frame(self.frame_index)

    def delete_selected(self) -> None:
        row = self.span_list.currentRow()
        if 0 <= row < len(self.spans):
            del self.spans[row]
            self.refresh_list()
            self.show_frame(self.frame_index)

    def refresh_list(self) -> None:
        self.span_list.clear()
        for span in self.spans:
            item = QListWidgetItem(
                f"{span.label:7s} {span.start:7.2f} → {span.end:7.2f}  ({span.end - span.start:.1f}s)"
            )
            self.span_list.addItem(item)
        self.timeline.spans = self.spans
        self.timeline.update()

    # ------------------------------------------------------------------- i/o

    def save(self) -> None:
        payload = {
            "video": self.video_path.name,
            "fps": round(self.fps, 3),
            "duration": round(self.duration, 2),
            "frames": self.frame_count,
            "spans": [asdict(span) for span in self.spans],
        }
        path = self.labels_path()
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.info.setText(f"saved {len(self.spans)} spans to {path.name}")

    def load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.spans = [Span(**span) for span in data.get("spans", [])]
        self.refresh_list()

    def load_dialog(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Load labels", str(self.video_path.parent), "JSON (*.json)"
        )
        if name:
            try:
                self.load(Path(name))
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Could not load", str(exc))

    def closeEvent(self, event) -> None:
        self.capture.release()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        name, _ = QFileDialog.getOpenFileName(None, "Open video", "", "Video (*.mp4 *.mov *.avi *.mkv)")
        if not name:
            return 1
        path = Path(name)
    if not path.exists():
        print(f"video not found: {path}")
        return 1
    window = LabelWindow(path)
    window.resize(1180, 940)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
