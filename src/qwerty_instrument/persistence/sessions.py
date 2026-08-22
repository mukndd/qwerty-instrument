"""Lightweight local practice-session logging (SQLite, no cloud backend).

Written from the UI/control thread only, after a practice session ends --
never from the audio callback (spec section 39: no synchronous disk I/O
on the real-time path).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_iso TEXT NOT NULL,
    song TEXT NOT NULL,
    section TEXT NOT NULL,
    mode TEXT NOT NULL,
    speed_percent REAL NOT NULL,
    duration_seconds REAL NOT NULL,
    pitch_accuracy_percent REAL,
    timing_accuracy_percent REAL,
    completion_percent REAL,
    mean_timing_error_ms REAL,
    max_combo INTEGER,
    mistakes_json TEXT
);
"""


@dataclass
class SessionRecord:
    date_iso: str
    song: str
    section: str
    mode: str
    speed_percent: float
    duration_seconds: float
    pitch_accuracy_percent: float | None = None
    timing_accuracy_percent: float | None = None
    completion_percent: float | None = None
    mean_timing_error_ms: float | None = None
    max_combo: int | None = None
    mistakes_json: str | None = None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(SCHEMA)
    return conn


def record_session(db_path: Path, record: SessionRecord) -> int:
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            """INSERT INTO sessions
               (date_iso, song, section, mode, speed_percent, duration_seconds,
                pitch_accuracy_percent, timing_accuracy_percent, completion_percent,
                mean_timing_error_ms, max_combo, mistakes_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.date_iso, record.song, record.section, record.mode, record.speed_percent,
                record.duration_seconds, record.pitch_accuracy_percent, record.timing_accuracy_percent,
                record.completion_percent, record.mean_timing_error_ms, record.max_combo, record.mistakes_json,
            ),
        )
        conn.commit()
        return cur.lastrowid


def best_accuracy(db_path: Path, song: str, section: str) -> float | None:
    if not db_path.exists():
        return None
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT MAX(pitch_accuracy_percent) FROM sessions WHERE song=? AND section=?", (song, section)
        ).fetchone()
        return row[0] if row else None


def fastest_successful_speed(db_path: Path, song: str, section: str, min_accuracy_percent: float = 90.0) -> float | None:
    if not db_path.exists():
        return None
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT MAX(speed_percent) FROM sessions WHERE song=? AND section=? AND pitch_accuracy_percent >= ?",
            (song, section, min_accuracy_percent),
        ).fetchone()
        return row[0] if row else None


def practice_history(db_path: Path, song: str | None = None, limit: int = 50) -> list[dict]:
    if not db_path.exists():
        return []
    with closing(_connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if song:
            rows = conn.execute("SELECT * FROM sessions WHERE song=? ORDER BY id DESC LIMIT ?", (song, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
