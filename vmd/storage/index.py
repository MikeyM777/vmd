"""SQLite catalogue of recorded segments."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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
    """The record of what exists on disk. Never scans the filesystem.

    One instance belongs to one thread. Another thread or process that needs to read
    the catalogue must open its own instance against the same file; WAL mode makes
    concurrent readers safe alongside the single writer.
    """

    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        # WAL lets a reader and the writer work at the same time; the busy timeout
        # makes a reader wait for a brief write lock instead of failing immediately
        # with "database is locked". Both matter once the web UI reads this file.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def add(
        self, stream: str, path: str, start: float, end: float, size_bytes: int,
        commit: bool = True,
    ) -> int:
        """Register a segment. Offering the same path again with the same
        contents is a no-op; offering it with different contents corrects it.

        It used to be INSERT OR IGNORE, which kept the first row whatever the
        file had since become - and a file can become something else. ffmpeg
        names segments from the wall clock and its segment muxer truncates a
        name it is given again, so a clock set backwards on a machine with no
        NTP overwrites footage that is already indexed. The row then described
        contents the file no longer had: Playback offered an hour of different
        footage, retention judged it by the wrong timestamp, and the coverage
        bar drew hours that were no longer there. A row that no longer describes
        its file is worse than no row at all.

        `commit=False` defers the commit so a caller inserting many rows can pay for
        one fsync instead of one per row; it must call commit() afterwards.
        """
        existing = self._connection.execute(
            'SELECT id, start, "end" AS finish, size_bytes FROM segments WHERE path = ?',
            (path,),
        ).fetchone()
        if existing is None:
            cursor = self._connection.execute(
                "INSERT INTO segments (stream, path, start, end, size_bytes) "
                "VALUES (?, ?, ?, ?, ?)",
                (stream, path, start, end, size_bytes),
            )
            if commit:
                self._connection.commit()
            return int(cursor.lastrowid)

        if (
            float(existing["start"]),
            float(existing["finish"]),
            int(existing["size_bytes"]),
        ) != (start, end, size_bytes):
            self._connection.execute(
                'UPDATE segments SET stream = ?, start = ?, "end" = ?, size_bytes = ? '
                "WHERE id = ?",
                (stream, start, end, size_bytes, int(existing["id"])),
            )
            logger.warning(
                "%s is no longer the recording the catalogue had under that "
                "name, so the row has been corrected to what is on disk now",
                path,
            )
            if commit:
                self._connection.commit()
        return int(existing["id"])

    def commit(self) -> None:
        """Flush any deferred inserts."""
        self._connection.commit()

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
