"""SQLite catalogue of recorded segments."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stream     TEXT    NOT NULL,
    path       TEXT    NOT NULL UNIQUE,
    start      REAL    NOT NULL,
    end        REAL    NOT NULL,
    size_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_start ON segments (stream, start);
"""


@dataclass(frozen=True)
class Segment:
    id: int
    stream: str
    path: str
    start: float  # epoch seconds
    end: float
    size_bytes: int

    @property
    def duration(self) -> float:
        return self.end - self.start


class SegmentIndex:
    """The record of what exists on disk. Never scans the filesystem."""

    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def add(self, stream: str, path: str, start: float, end: float, size_bytes: int) -> int:
        """Register a segment. Adding the same path twice is a no-op."""
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO segments (stream, path, start, end, size_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (stream, path, start, end, size_bytes),
        )
        self._connection.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        existing = self._connection.execute(
            "SELECT id FROM segments WHERE path = ?", (path,)
        ).fetchone()
        return int(existing["id"])

    def all(self, stream: str | None = None) -> list[Segment]:
        if stream is None:
            rows = self._connection.execute(
                "SELECT * FROM segments ORDER BY start, id"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM segments WHERE stream = ? ORDER BY start, id", (stream,)
            ).fetchall()
        return [self._to_segment(row) for row in rows]

    def oldest(self, stream: str | None = None) -> Segment | None:
        segments = self.all(stream)
        return segments[0] if segments else None

    def total_bytes(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM segments"
        ).fetchone()
        return int(row["total"])

    def delete(self, segment_id: int) -> None:
        self._connection.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        self._connection.commit()

    def gaps(
        self, stream: str, window_start: float, window_end: float, min_gap: float = 1.0
    ) -> list[tuple[float, float]]:
        """Periods inside the window with no recorded coverage."""
        segments = [
            s for s in self.all(stream) if s.end > window_start and s.start < window_end
        ]
        gaps: list[tuple[float, float]] = []
        cursor = window_start
        for segment in segments:
            if segment.start - cursor >= min_gap:
                gaps.append((cursor, segment.start))
            cursor = max(cursor, segment.end)
        if window_end - cursor >= min_gap:
            gaps.append((cursor, window_end))
        return gaps

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _to_segment(row: sqlite3.Row) -> Segment:
        return Segment(
            id=int(row["id"]),
            stream=row["stream"],
            path=row["path"],
            start=float(row["start"]),
            end=float(row["end"]),
            size_bytes=int(row["size_bytes"]),
        )
