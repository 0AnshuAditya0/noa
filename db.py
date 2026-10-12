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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_activity (
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                PRIMARY KEY (user_id, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS linked_accounts (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                handle TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                token TEXT,
                linked_at TEXT NOT NULL,
                UNIQUE (user_id, platform)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS solves (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                problem_id TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                solved_at TEXT NOT NULL,
                UNIQUE (user_id, platform, problem_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_stats (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                total_solved INTEGER NOT NULL DEFAULT 0,
                easy INTEGER NOT NULL DEFAULT 0,
                medium INTEGER NOT NULL DEFAULT 0,
                hard INTEGER NOT NULL DEFAULT 0,
                rating INTEGER,
                max_rating INTEGER,
                last_synced TEXT,
                PRIMARY KEY (user_id, platform)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_solves_user_date ON solves (user_id, solved_at)"
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


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def log_activity(user_id: int, date: str | None = None) -> None:
    """Record an active day (UTC). Idempotent per user+date."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_activity (user_id, date) VALUES (?, ?)",
            (user_id, date or _today_utc()),
        )


def get_activity_dates(user_id: int) -> list[str]:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT date FROM user_activity WHERE user_id = ? ORDER BY date",
            (user_id,),
        )
        return [row[0] for row in cur.fetchall()]


def get_streaks(user_id: int) -> tuple[int, int]:
    """Returns (current_streak, longest_streak) in days, UTC-based."""
    from datetime import date as _date

    days = sorted(set(get_activity_dates(user_id)))
    if not days:
        return 0, 0
    dayset = set(days)
    # Longest run anywhere.
    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        d_prev = _date.fromisoformat(prev)
        d_cur = _date.fromisoformat(cur)
        if (d_cur - d_prev).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    # Current streak: counts back from today (or yesterday if today idle).
    today = _date.fromisoformat(_today_utc())
    cursor = today if today.isoformat() in dayset else today - __import__("datetime").timedelta(days=1)
    current = 0
    while cursor.isoformat() in dayset:
        current += 1
        cursor -= __import__("datetime").timedelta(days=1)
    return current, longest


def list_users() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM users")
        return [dict(row) for row in cur.fetchall()]


def board_counts(path_id: str, since_iso: str | None = None) -> dict[int, tuple[int, str | None]]:
    """Per-user (count, last_reached_at) for one path, optionally since a completed_at cutoff."""
    init_db()
    with _connect() as conn:
        if since_iso:
            cur = conn.execute(
                """
                SELECT user_id, COUNT(*), MAX(completed_at) FROM progress
                WHERE path_id = ? AND completed_at >= ? GROUP BY user_id
                """,
                (path_id, since_iso),
            )
        else:
            cur = conn.execute(
                "SELECT user_id, COUNT(*), MAX(completed_at) FROM progress WHERE path_id = ? GROUP BY user_id",
                (path_id,),
            )
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def week_cutoff_iso(days: int = 7) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def mark_done(user_id: int, path_id: str, topic_id: str) -> bool:
    """Insert one row. Returns True if new, False if already done."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO progress (user_id, path_id, topic_id, completed_at) VALUES (?, ?, ?, ?)",
            (user_id, path_id, topic_id, _now()),
        )
        is_new = cur.rowcount == 1
        if is_new:
            conn.execute(
                "INSERT OR IGNORE INTO user_activity (user_id, date) VALUES (?, ?)",
                (user_id, _today_utc()),
            )
        return is_new


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


# ---- Linked coding-platform accounts, solves log, cached stats ----
# Additive only: existing progress/users/activity code above is untouched.


def get_link(user_id: int, platform: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM linked_accounts WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_links(user_id: int) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM linked_accounts WHERE user_id = ? ORDER BY platform", (user_id,))
        return [dict(row) for row in cur.fetchall()]


def list_verified_links() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM linked_accounts WHERE verified = 1")
        return [dict(row) for row in cur.fetchall()]


def handle_verified_by(platform: str, handle: str, case_insensitive: bool = True) -> int | None:
    """Return the Discord user_id that verified this handle, else None."""
    init_db()
    with _connect() as conn:
        if case_insensitive:
            cur = conn.execute(
                "SELECT user_id FROM linked_accounts WHERE platform = ? AND verified = 1 AND handle = ? COLLATE NOCASE LIMIT 1",
                (platform, handle),
            )
        else:
            cur = conn.execute(
                "SELECT user_id FROM linked_accounts WHERE platform = ? AND verified = 1 AND handle = ? LIMIT 1",
                (platform, handle),
            )
        row = cur.fetchone()
        return row[0] if row else None


def create_pending_link(user_id: int, platform: str, handle: str, token: str) -> str:
    """Stage an unverified link. Returns 'ok', or 'taken' if another user verified it."""
    owner = handle_verified_by(platform, handle)
    if owner is not None and owner != user_id:
        return "taken"
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO linked_accounts
                (user_id, platform, handle, verified, token, linked_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (user_id, platform, handle, token, _now()),
        )
    return "ok"


def set_verified(user_id: int, platform: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE linked_accounts SET verified = 1, token = NULL WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        )
        return cur.rowcount > 0


def unlink_account(user_id: int, platform: str) -> bool:
    """Remove the link plus its cached solves/stats."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM linked_accounts WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        )
        removed = cur.rowcount > 0
        conn.execute("DELETE FROM solves WHERE user_id = ? AND platform = ?", (user_id, platform))
        conn.execute("DELETE FROM platform_stats WHERE user_id = ? AND platform = ?", (user_id, platform))
        return removed


def upsert_solves(user_id: int, platform: str, solves: list[dict[str, Any]]) -> int:
    """Insert solves, ignoring dupes. Returns the count of newly added rows."""
    if not solves:
        return 0
    init_db()
    new = 0
    with _connect() as conn:
        for s in solves:
            cur = conn.execute(
                "INSERT OR IGNORE INTO solves (user_id, platform, problem_id, difficulty, solved_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, platform, s["problem_id"], s["difficulty"], s["solved_at"]),
            )
            new += cur.rowcount
    return new


def get_solves(user_id: int, platform: str, since: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if since:
            cur = conn.execute(
                "SELECT * FROM solves WHERE user_id = ? AND platform = ? AND solved_at >= ? ORDER BY solved_at",
                (user_id, platform, since),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM solves WHERE user_id = ? AND platform = ? ORDER BY solved_at",
                (user_id, platform),
            )
        return [dict(row) for row in cur.fetchall()]


def rebuild_platform_stats(
    user_id: int,
    platform: str,
    rating: int | None = None,
    max_rating: int | None = None,
) -> dict[str, Any]:
    """Recompute counts from the solves log and cache one row. Returns the row."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT difficulty, COUNT(*) FROM solves WHERE user_id = ? AND platform = ? GROUP BY difficulty",
            (user_id, platform),
        )
        counts = {row[0]: row[1] for row in cur.fetchall()}
        easy = counts.get("easy", 0)
        medium = counts.get("medium", 0)
        hard = counts.get("hard", 0)
        total = easy + medium + hard
        now = _now()
        conn.execute(
            """
            INSERT OR REPLACE INTO platform_stats
                (user_id, platform, total_solved, easy, medium, hard, rating, max_rating, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, platform, total, easy, medium, hard, rating, max_rating, now),
        )
        return {
            "user_id": user_id,
            "platform": platform,
            "total_solved": total,
            "easy": easy,
            "medium": medium,
            "hard": hard,
            "rating": rating,
            "max_rating": max_rating,
            "last_synced": now,
        }


def get_platform_stats(user_id: int, platform: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM platform_stats WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ---- Verified-platform aggregates (coding totals / streaks / boards) ----
# All totals join solves to verified linked_accounts, so unverified rows
# never feed any number. Aggregates run in SQL; Python only folds the
# resulting date lists into streak runs.


def platform_totals(user_id: int, since: str | None = None) -> dict[str, int]:
    """Total/easy/medium/hard across VERIFIED platforms only (SQL aggregate)."""
    init_db()
    with _connect() as conn:
        if since:
            cur = conn.execute(
                """
                SELECT COUNT(*), SUM(s.difficulty = 'easy'),
                       SUM(s.difficulty = 'medium'), SUM(s.difficulty = 'hard')
                FROM solves s
                JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
                WHERE s.user_id = ? AND l.verified = 1 AND s.solved_at >= ?
                """,
                (user_id, since),
            )
        else:
            cur = conn.execute(
                """
                SELECT COUNT(*), SUM(s.difficulty = 'easy'),
                       SUM(s.difficulty = 'medium'), SUM(s.difficulty = 'hard')
                FROM solves s
                JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
                WHERE s.user_id = ? AND l.verified = 1
                """,
                (user_id,),
            )
        row = cur.fetchone()
        return {"total": row[0] or 0, "easy": row[1] or 0, "medium": row[2] or 0, "hard": row[3] or 0}


def solve_dates(user_id: int) -> list[str]:
    """Distinct UTC solve dates for VERIFIED platforms (SQL aggregate)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT substr(s.solved_at, 1, 10)
            FROM solves s
            JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
            WHERE s.user_id = ? AND l.verified = 1
            ORDER BY 1
            """,
            (user_id,),
        )
        return [row[0] for row in cur.fetchall()]


def solve_dates_batch(user_ids: list[int]) -> dict[int, list[str]]:
    """One query for many users: {user_id: [UTC date strings]}."""
    if not user_ids:
        return {}
    init_db()
    out: dict[int, list[str]] = {uid: [] for uid in user_ids}
    with _connect() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        cur = conn.execute(
            f"""
            SELECT s.user_id, substr(s.solved_at, 1, 10)
            FROM solves s
            JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
            WHERE s.user_id IN ({placeholders}) AND l.verified = 1
            GROUP BY s.user_id, substr(s.solved_at, 1, 10)
            ORDER BY s.user_id, 2
            """,
            tuple(user_ids),
        )
        for uid, day in cur.fetchall():
            out[uid].append(day)
    return out


def streak_from_dates(days: list[str], today: str | None = None) -> tuple[int, int]:
    """(current, longest) consecutive-day run. Pure function over date strings."""
    from datetime import date as _date
    from datetime import timedelta

    uniq = sorted(set(days))
    if not uniq:
        return 0, 0
    longest = run = 1
    for prev, cur in zip(uniq, uniq[1:]):
        if (_date.fromisoformat(cur) - _date.fromisoformat(prev)).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    anchor = _date.fromisoformat(today or _today_utc())
    cursor = anchor if anchor.isoformat() in set(uniq) else anchor - timedelta(days=1)
    dayset = set(uniq)
    current = 0
    while cursor.isoformat() in dayset:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def coding_streak(user_id: int) -> tuple[int, int]:
    """(current, longest) coding streak from verified solves."""
    return streak_from_dates(solve_dates(user_id))


def problems_board(since: str | None = None) -> list[dict[str, Any]]:
    """One ordered aggregate: users WITH a verified account, ranked by total
    solved desc, best rating desc (NULLs last), registered_at asc."""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if since:
            cur = conn.execute(
                """
                SELECT u.user_id, u.registered_at,
                       COUNT(s.problem_id) AS total,
                       (SELECT MAX(ps.rating) FROM platform_stats ps WHERE ps.user_id = u.user_id) AS best
                FROM users u
                JOIN linked_accounts l ON l.user_id = u.user_id AND l.verified = 1
                LEFT JOIN solves s ON s.user_id = u.user_id AND s.platform = l.platform
                    AND s.solved_at >= ?
                GROUP BY u.user_id
                ORDER BY total DESC, best DESC, u.registered_at ASC
                """,
                (since,),
            )
        else:
            cur = conn.execute(
                """
                SELECT u.user_id, u.registered_at,
                       COUNT(s.problem_id) AS total,
                       (SELECT MAX(ps.rating) FROM platform_stats ps WHERE ps.user_id = u.user_id) AS best
                FROM users u
                JOIN linked_accounts l ON l.user_id = u.user_id AND l.verified = 1
                LEFT JOIN solves s ON s.user_id = u.user_id AND s.platform = l.platform
                GROUP BY u.user_id
                ORDER BY total DESC, best DESC, u.registered_at ASC
                """
            )
        return [dict(row) for row in cur.fetchall()]


def verified_platforms_batch(user_ids: list[int], since: str | None = None) -> dict[int, list[str]]:
    """{user_id: [platforms with a counted solve in scope]}. One GROUP_CONCAT query."""
    if not user_ids:
        return {}
    init_db()
    out: dict[int, list[str]] = {uid: [] for uid in user_ids}
    with _connect() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        if since:
            cur = conn.execute(
                f"""
                SELECT s.user_id, GROUP_CONCAT(DISTINCT s.platform)
                FROM solves s
                JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
                WHERE s.user_id IN ({placeholders}) AND l.verified = 1 AND s.solved_at >= ?
                GROUP BY s.user_id
                """,
                (*user_ids, since),
            )
        else:
            cur = conn.execute(
                f"""
                SELECT s.user_id, GROUP_CONCAT(DISTINCT s.platform)
                FROM solves s
                JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
                WHERE s.user_id IN ({placeholders}) AND l.verified = 1
                GROUP BY s.user_id
                """,
                tuple(user_ids),
            )
        for uid, plats in cur.fetchall():
            out[uid] = sorted(plats.split(",")) if plats else []
    return out
