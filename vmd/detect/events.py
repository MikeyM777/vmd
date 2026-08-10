"""SQLite record of what moved, beside the catalogue of what was recorded.

Written by the detector process, read by the console. Same shape and the same
connection discipline as `vmd/storage/index.py`, deliberately: the two files sit
next to each other, are read by the same window, and are reclaimed together.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stream       TEXT    NOT NULL,
    started      REAL    NOT NULL,
    ended        REAL    NOT NULL,
    x            INTEGER NOT NULL,
    y            INTEGER NOT NULL,
    w            INTEGER NOT NULL,
    h            INTEGER NOT NULL,
    travelled_px REAL    NOT NULL,
    label        TEXT    NOT NULL DEFAULT '',
    confidence   REAL    NOT NULL DEFAULT 0.0,
    clip_path    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_started ON events (started);
CREATE INDEX IF NOT EXISTS events_stream_started ON events (stream, started);
"""


@dataclass(frozen=True)
class Event:
    """One confirmed track, as the operator will see it.

    `label` is "" - never None - when the classifier did not run or could not
    tell, which at 700 m is most of the time. An unnamed event is still an
    event; the classifier has no veto and never gets to make a row disappear.
    """

    id: int
    stream: str
    started: float  # epoch seconds
    ended: float
    box: tuple[int, int, int, int]  # in frame coordinates
    travelled_px: float
    label: str = ""
    confidence: float = 0.0  # 0.0 when unlabelled
    clip_path: str = ""  # "" when no clip was kept


class EventStore:
    """The list of movement events. One instance belongs to one thread.

    Another thread or process that needs to read the list opens its own instance
    against the same file; WAL mode makes concurrent readers safe alongside a
    writer. The detector runs one thread per stream, so each of those threads
    owns its own store over the one file.
    """

    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(db_path))
        self._connection.row_factory = sqlite3.Row
        # WAL so the console can read the list while the detector appends to it;
        # the busy timeout so a reader waits out a brief write lock instead of
        # failing with "database is locked". Two detector threads and the window
        # all touch this file.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def add(
        self,
        stream: str,
        started: float,
        ended: float,
        box: tuple[int, int, int, int],
        travelled_px: float,
        label: str | None = "",
        confidence: float = 0.0,
        clip_path: str | None = "",
        commit: bool = True,
    ) -> int:
        """Record one event and return its id.

        `label` and `clip_path` are coerced from None to "": a caller with
        nothing to say must not be able to leave a null in a column every reader
        would then have to defend against.
        """
        x, y, w, h = (int(v) for v in box)
        cursor = self._connection.execute(
            "INSERT INTO events "
            "(stream, started, ended, x, y, w, h, travelled_px, label, confidence, clip_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stream,
                float(started),
                float(ended),
                x,
                y,
                w,
                h,
                float(travelled_px),
                label or "",
                float(confidence),
                clip_path or "",
            ),
        )
        if commit:
            self._connection.commit()
        return int(cursor.lastrowid)

    def commit(self) -> None:
        self._connection.commit()

    def recent(self, limit: int = 50) -> list[Event]:
        """The newest events first - what the "recent movement" list shows."""
        rows = self._connection.execute(
            "SELECT * FROM events ORDER BY started DESC, id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [self._to_event(row) for row in rows]

    def between(self, start: float, end: float, stream: str | None = None) -> list[Event]:
        """Events overlapping the window, oldest first - the timeline's marks.

        Overlap, not containment: an event that began before the visible window
        and was still running inside it happened inside it, and a mark that
        vanishes when you scroll past its start would be a lie about the
        footage.
        """
        sql = "SELECT * FROM events WHERE ended >= ? AND started <= ?"
        params: list = [float(start), float(end)]
        if stream is not None:
            sql += " AND stream = ?"
            params.append(stream)
        sql += " ORDER BY started, id"
        rows = self._connection.execute(sql, params).fetchall()
        return [self._to_event(row) for row in rows]

    def delete_before(self, cutoff: float, stream: str | None = None) -> int:
        """Drop events whose footage has been reclaimed. Returns how many went.

        Keyed on `ended`, so an event that straddles the cutoff survives: half
        of the footage it points at is still on disk. `stream` exists because
        retention reclaims one stream at a time, and the other stream's footage
        from the same minutes may still be there.
        """
        sql = "DELETE FROM events WHERE ended < ?"
        params: list = [float(cutoff)]
        if stream is not None:
            sql += " AND stream = ?"
            params.append(stream)
        cursor = self._connection.execute(sql, params)
        self._connection.commit()
        return int(cursor.rowcount)

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"])

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=int(row["id"]),
            stream=row["stream"],
            started=float(row["started"]),
            ended=float(row["ended"]),
            box=(int(row["x"]), int(row["y"]), int(row["w"]), int(row["h"])),
            travelled_px=float(row["travelled_px"]),
            # No `or ""` here: the columns are NOT NULL and add() coerces None,
            # so a null cannot exist. A defensive coercion at this layer would
            # be a line no test could ever reach.
            label=row["label"],
            confidence=float(row["confidence"]),
            clip_path=row["clip_path"],
        )
