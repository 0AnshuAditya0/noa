"""SQLite store. Per-user isolation: every query filters by user_id."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "data" / "noa.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER NOT NULL,
                path_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                UNIQUE (user_id, path_id, topic_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                active_path TEXT,
                experience_level TEXT,
                weekly_hours TEXT,
                goal TEXT
            )
            """
        )


def get_user(user_id: int) -> dict[str, Any] | None:
    """Return user row as dict, or None if not registered."""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def is_registered(user_id: int) -> bool:
    return get_user(user_id) is not None


def create_user(
    user_id: int,
    username: str,
    active_path: str | None = None,
    experience_level: str | None = None,
    weekly_hours: str | None = None,
    goal: str | None = None,
) -> bool:
    """Insert a new user. Returns True if created, False if already registered."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO users
                (user_id, username, registered_at, active_path, experience_level, weekly_hours, goal)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, _now(), active_path, experience_level, weekly_hours, goal),
        )
        return cur.rowcount == 1


def update_user(user_id: int, **fields: Any) -> bool:
    """Update allowed user fields. Returns True if a row was changed."""
    allowed = {"username", "active_path", "experience_level", "weekly_hours", "goal"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    init_db()
    cols = ", ".join(f"{k} = ?" for k in updates)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE users SET {cols} WHERE user_id = ?",
            (*updates.values(), user_id),
        )
        return cur.rowcount > 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_done(user_id: int, path_id: str, topic_id: str) -> bool:
    """Insert one row. Returns True if new, False if already done."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO progress (user_id, path_id, topic_id, completed_at) VALUES (?, ?, ?, ?)",
            (user_id, path_id, topic_id, _now()),
        )
        return cur.rowcount == 1


def mark_many(user_id: int, path_id: str, topic_ids: list[str]) -> tuple[int, int]:
    """Returns (newly_added, already_done)."""
    new = 0
    for tid in topic_ids:
        if mark_done(user_id, path_id, tid):
            new += 1
    return new, len(topic_ids) - new


def unmark(user_id: int, path_id: str, topic_id: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM progress WHERE user_id = ? AND path_id = ? AND topic_id = ?",
            (user_id, path_id, topic_id),
        )
        return cur.rowcount > 0


def unmark_many(user_id: int, path_id: str, topic_ids: list[str]) -> int:
    removed = 0
    for tid in topic_ids:
        if unmark(user_id, path_id, tid):
            removed += 1
    return removed


def is_done(user_id: int, path_id: str, topic_id: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM progress WHERE user_id = ? AND path_id = ? AND topic_id = ? LIMIT 1",
            (user_id, path_id, topic_id),
        )
        return cur.fetchone() is not None


def get_done_ids(user_id: int, path_id: str) -> set[str]:
    """All completed leaf topic_ids for one user + one path."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT topic_id FROM progress WHERE user_id = ? AND path_id = ?",
            (user_id, path_id),
        )
        return {row[0] for row in cur.fetchall()}
