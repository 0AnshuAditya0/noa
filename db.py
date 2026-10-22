"""SQLite store. Per-user isolation: every query filters by user_id."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "noa.db"


def get_db_path() -> Path:
    """Database file: NOA_DB_PATH or the default. Read live so test mode can swap it."""
    override = os.getenv("NOA_DB_PATH")
    return Path(override).expanduser() if override else DEFAULT_DB_PATH


# Kept for backwards compatibility; new code should call get_db_path().
DB_PATH = get_db_path()

_TEST_ENV_VAR = "NOA_TESTING"


def is_test_mode() -> bool:
    return os.getenv(_TEST_ENV_VAR, "").strip().lower() in ("1", "true", "yes")


@contextmanager
def temp_db() -> Iterator[Path]:
    """Point all db access at a throwaway database. Test scripts must use this
    (or set NOA_DB_PATH) so they can never touch the real database."""
    with tempfile.TemporaryDirectory(prefix="noa-test-") as tmpdir:
        old_path, old_flag = os.getenv("NOA_DB_PATH"), os.getenv(_TEST_ENV_VAR)
        test_file = str(Path(tmpdir) / "test.db")
        os.environ["NOA_DB_PATH"] = test_file
        os.environ[_TEST_ENV_VAR] = "1"
        try:
            init_db()
            yield Path(test_file)
        finally:
            if old_path is None:
                os.environ.pop("NOA_DB_PATH", None)
            else:
                os.environ["NOA_DB_PATH"] = old_path
            if old_flag is None:
                os.environ.pop(_TEST_ENV_VAR, None)
            else:
                os.environ[_TEST_ENV_VAR] = old_flag


def _connect() -> "_ManagedConnection":
    return _ManagedConnection(get_db_path())


class _ManagedConnection:
    """Context manager yielding a SQLite connection. Commits (or rolls back)
    on exit AND closes the handle, so no file locks leak on Windows."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL;")

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS platform_daily (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                UNIQUE (user_id, platform, date)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_user_date ON platform_daily (user_id, date)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stats_snapshots (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                date TEXT NOT NULL,
                total_solved INTEGER NOT NULL DEFAULT 0,
                UNIQUE (user_id, platform, date)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_user_date ON stats_snapshots (user_id, date)")
        scols = [row[1] for row in conn.execute("PRAGMA table_info(stats_snapshots)")]
        for _col in ("easy", "medium", "hard"):
            if _col not in scols:
                conn.execute(f"ALTER TABLE stats_snapshots ADD COLUMN {_col} INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                announce_channel_id INTEGER,
                last_announced_week TEXT,
                announce_failures INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dsa_learned (
                user_id INTEGER NOT NULL,
                entry_id TEXT NOT NULL,
                learned_at TEXT NOT NULL,
                UNIQUE (user_id, entry_id)
            )
            """
        )
        cols = [row[1] for row in conn.execute("PRAGMA table_info(platform_stats)")]
        if "sync_ok" not in cols:
            conn.execute("ALTER TABLE platform_stats ADD COLUMN sync_ok INTEGER NOT NULL DEFAULT 1")
        if "last_error" not in cols:
            conn.execute("ALTER TABLE platform_stats ADD COLUMN last_error TEXT")
        ucols = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
        if "bio" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN bio TEXT")
        if "consent_version" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN consent_version TEXT")
        if "consented_at" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN consented_at TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_meta (
                key TEXT PRIMARY KEY,
                value TEXT
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
    allowed = {"username", "active_path", "experience_level", "weekly_hours", "goal", "bio"}
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
    """Remove the link plus its cached solves/stats/daily rows."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM linked_accounts WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        )
        removed = cur.rowcount > 0
        conn.execute("DELETE FROM solves WHERE user_id = ? AND platform = ?", (user_id, platform))
        conn.execute("DELETE FROM platform_stats WHERE user_id = ? AND platform = ?", (user_id, platform))
        conn.execute("DELETE FROM platform_daily WHERE user_id = ? AND platform = ?", (user_id, platform))
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
        return write_platform_stats(
            user_id, platform,
            total=counts.get("easy", 0) + counts.get("medium", 0) + counts.get("hard", 0),
            easy=counts.get("easy", 0),
            medium=counts.get("medium", 0),
            hard=counts.get("hard", 0),
            rating=rating,
            max_rating=max_rating,
        )


def write_platform_stats(
    user_id: int,
    platform: str,
    total: int,
    easy: int,
    medium: int,
    hard: int,
    rating: int | None = None,
    max_rating: int | None = None,
) -> dict[str, Any]:
    """Cache one authoritative stats row (e.g. from a profile payload). Marks sync healthy."""
    init_db()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO platform_stats
                (user_id, platform, total_solved, easy, medium, hard, rating, max_rating,
                 last_synced, sync_ok, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
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


def mark_sync_failed(user_id: int, platform: str, error: str) -> None:
    """Flag the cached row as stale. Totals/rating/last_synced are left untouched."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE platform_stats SET sync_ok = 0, last_error = ? WHERE user_id = ? AND platform = ?",
            (error[:300], user_id, platform),
        )


def upsert_daily(user_id: int, platform: str, rows: list[tuple[str, int]]) -> int:
    """Write per-day solve counts (REPLACE: counts only grow). Returns rows written."""
    if not rows:
        return 0
    init_db()
    with _connect() as conn:
        for day, count in rows:
            conn.execute(
                "INSERT OR REPLACE INTO platform_daily (user_id, platform, date, count) VALUES (?, ?, ?, ?)",
                (user_id, platform, day, count),
            )
    return len(rows)


def week_start_date(days: int = 7) -> str:
    """UTC date string of the oldest day in the trailing window (inclusive)."""
    from datetime import timedelta

    return (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()


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
# Totals come from cached platform_stats, streaks and week counts from
# platform_daily. The solves table is a raw log only — it feeds nothing.
# All queries join to verified linked_accounts, so unverified rows never
# feed any number. Aggregates run in SQL; Python only folds date lists.


def platform_totals(user_id: int) -> dict[str, int]:
    """Total/easy/medium/hard summed across VERIFIED platforms (SQL aggregate)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT COALESCE(SUM(ps.total_solved), 0), COALESCE(SUM(ps.easy), 0),
                   COALESCE(SUM(ps.medium), 0), COALESCE(SUM(ps.hard), 0)
            FROM platform_stats ps
            JOIN linked_accounts l ON l.user_id = ps.user_id AND l.platform = ps.platform
            WHERE ps.user_id = ? AND l.verified = 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        return {"total": row[0] or 0, "easy": row[1] or 0, "medium": row[2] or 0, "hard": row[3] or 0}


def solve_dates(user_id: int) -> list[str]:
    """Distinct UTC active dates for VERIFIED platforms, from platform_daily."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT d.date
            FROM platform_daily d
            JOIN linked_accounts l ON l.user_id = d.user_id AND l.platform = d.platform
            WHERE d.user_id = ? AND l.verified = 1 AND d.count > 0
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
            SELECT d.user_id, d.date
            FROM platform_daily d
            JOIN linked_accounts l ON l.user_id = d.user_id AND l.platform = d.platform
            WHERE d.user_id IN ({placeholders}) AND l.verified = 1 AND d.count > 0
            GROUP BY d.user_id, d.date
            ORDER BY d.user_id, 2
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
    """Users WITH a verified account plus their solved totals and best rating.

    Totals come from platform_daily (weeks) or platform_stats (all time).
    Callers order by points; the SQL keeps a sensible default order."""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if since:
            cur = conn.execute(
                """
                SELECT u.user_id, u.registered_at,
                       COALESCE(SUM(d.count), 0) AS total,
                       (SELECT MAX(ps.rating) FROM platform_stats ps WHERE ps.user_id = u.user_id) AS best
                FROM users u
                JOIN linked_accounts l ON l.user_id = u.user_id AND l.verified = 1
                LEFT JOIN platform_daily d ON d.user_id = u.user_id AND d.platform = l.platform
                    AND d.date >= ?
                GROUP BY u.user_id
                ORDER BY total DESC, best DESC, u.registered_at ASC
                """,
                (since,),
            )
        else:
            cur = conn.execute(
                """
                SELECT u.user_id, u.registered_at,
                       COALESCE(SUM(ps.total_solved), 0) AS total,
                       (SELECT MAX(ps2.rating) FROM platform_stats ps2 WHERE ps2.user_id = u.user_id) AS best
                FROM users u
                JOIN linked_accounts l ON l.user_id = u.user_id AND l.verified = 1
                LEFT JOIN platform_stats ps ON ps.user_id = u.user_id AND ps.platform = l.platform
                GROUP BY u.user_id
                ORDER BY total DESC, best DESC, u.registered_at ASC
                """
            )
        return [dict(row) for row in cur.fetchall()]


def verified_platforms_batch(user_ids: list[int], since: str | None = None) -> dict[int, list[str]]:
    """{user_id: [platforms counted in scope]}. Stats for all-time, daily for weeks."""
    if not user_ids:
        return {}
    init_db()
    out: dict[int, list[str]] = {uid: [] for uid in user_ids}
    with _connect() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        if since:
            cur = conn.execute(
                f"""
                SELECT d.user_id, GROUP_CONCAT(DISTINCT d.platform)
                FROM platform_daily d
                JOIN linked_accounts l ON l.user_id = d.user_id AND l.platform = d.platform
                WHERE d.user_id IN ({placeholders}) AND l.verified = 1
                    AND d.date >= ? AND d.count > 0
                GROUP BY d.user_id
                """,
                (*user_ids, since),
            )
        else:
            cur = conn.execute(
                f"""
                SELECT ps.user_id, GROUP_CONCAT(DISTINCT ps.platform)
                FROM platform_stats ps
                JOIN linked_accounts l ON l.user_id = ps.user_id AND l.platform = ps.platform
                WHERE ps.user_id IN ({placeholders}) AND l.verified = 1
                GROUP BY ps.user_id
                """,
                tuple(user_ids),
            )
        for uid, plats in cur.fetchall():
            out[uid] = sorted(plats.split(",")) if plats else []
    return out


# ---- DSA library learned marks. Additive only; nothing above changes. ----


def mark_learned(user_id: int, entry_id: str) -> bool:
    """Mark an entry learned. Returns True if new, False if already marked."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO dsa_learned (user_id, entry_id, learned_at) VALUES (?, ?, ?)",
            (user_id, entry_id, _now()),
        )
        return cur.rowcount == 1


def unmark_learned(user_id: int, entry_id: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM dsa_learned WHERE user_id = ? AND entry_id = ?",
            (user_id, entry_id),
        )
        return cur.rowcount > 0


def toggle_learned(user_id: int, entry_id: str) -> bool:
    """Flip the mark. Returns True when the entry is now learned."""
    if is_learned(user_id, entry_id):
        unmark_learned(user_id, entry_id)
        return False
    mark_learned(user_id, entry_id)
    return True


def is_learned(user_id: int, entry_id: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM dsa_learned WHERE user_id = ? AND entry_id = ? LIMIT 1",
            (user_id, entry_id),
        )
        return cur.fetchone() is not None


def learned_ids(user_id: int) -> set[str]:
    init_db()
    with _connect() as conn:
        cur = conn.execute("SELECT entry_id FROM dsa_learned WHERE user_id = ?", (user_id,))
        return {row[0] for row in cur.fetchall()}


# ---- Privacy + admin. Additive only. ----

USER_TABLES = (
    "users",
    "progress",
    "user_activity",
    "linked_accounts",
    "solves",
    "platform_stats",
    "platform_daily",
    "stats_snapshots",
    "dsa_learned",
)


def delete_user_data(user_id: int) -> dict[str, int]:
    """Delete every row belonging to a user, all tables, one transaction."""
    init_db()
    counts: dict[str, int] = {}
    with _connect() as conn:
        for table in USER_TABLES:
            cur = conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            counts[table] = cur.rowcount
    return counts


def admin_stats() -> dict[str, Any]:
    """Instance overview for the owner: users, links, sync health. Aggregates only."""
    init_db()
    with _connect() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        links = conn.execute(
            "SELECT platform, verified, COUNT(*) FROM linked_accounts GROUP BY platform, verified"
        ).fetchall()
        latest = conn.execute(
            "SELECT platform, MAX(last_synced) FROM platform_stats GROUP BY platform"
        ).fetchall()
        failing = conn.execute(
            "SELECT user_id, platform, last_synced, last_error FROM platform_stats WHERE sync_ok = 0"
        ).fetchall()
    return {
        "users": users,
        "links": [{"platform": p, "verified": bool(v), "count": c} for p, v, c in links],
        "latest_sync": {p: ts for p, ts in latest},
        "failing": [
            {"user_id": u, "platform": p, "last_synced": ts, "last_error": e} for u, p, ts, e in failing
        ],
    }


# ---- Daily stats snapshots + competition scoring. Additive only. ----
# Snapshots are keyed by LOCAL date (BOT_TZ) and kept forever. Scores for a
# window [start, now] come from snapshot deltas, so platforms with partial
# solve history count fairly.


def upsert_snapshot(
    user_id: int,
    platform: str,
    date: str,
    total_solved: int,
    easy: int | None = None,
    medium: int | None = None,
    hard: int | None = None,
) -> None:
    """Write the day's latest totals (REPLACE keeps one row per day)."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stats_snapshots "
            "(user_id, platform, date, total_solved, easy, medium, hard) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, platform, date, total_solved, easy, medium, hard),
        )


def get_snapshots(user_ids: list[int]) -> list[dict[str, Any]]:
    """All snapshot rows for verified links of these users, in one query."""
    if not user_ids:
        return []
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in user_ids)
        cur = conn.execute(
            f"""
            SELECT s.user_id, s.platform, s.date, s.total_solved, s.easy, s.medium, s.hard
            FROM stats_snapshots s
            JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
            WHERE s.user_id IN ({placeholders}) AND l.verified = 1
            ORDER BY s.user_id, s.platform, s.date
            """,
            tuple(user_ids),
        )
        return [dict(row) for row in cur.fetchall()]


def lifetime_points_batch(user_ids: list[int]) -> dict[int, int]:
    """Lifetime points per user: weighted platform_stats buckets over VERIFIED
    platforms only. Buckets short of the total count the remainder as medium.
    Roadmap progress and dsa_learned never add points."""
    if not user_ids:
        return {}
    from points import HARD_POINTS, MEDIUM_POINTS, EASY_POINTS

    init_db()
    with _connect() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        cur = conn.execute(
            f"""
            SELECT ps.user_id,
                   COALESCE(SUM(
                       ps.easy * {EASY_POINTS}
                       + ps.medium * {MEDIUM_POINTS}
                       + ps.hard * {HARD_POINTS}
                       + MAX(0, ps.total_solved - (ps.easy + ps.medium + ps.hard)) * {MEDIUM_POINTS}
                   ), 0) AS points
            FROM platform_stats ps
            JOIN linked_accounts l ON l.user_id = ps.user_id AND l.platform = ps.platform
            WHERE ps.user_id IN ({placeholders}) AND l.verified = 1
            GROUP BY ps.user_id
            """,
            tuple(user_ids),
        )
        out = {row[0]: row[1] for row in cur.fetchall()}
    for uid in user_ids:
        out.setdefault(uid, 0)
    return out


def lifetime_points(user_id: int) -> int:
    """Lifetime points for one user (verified platforms only)."""
    return lifetime_points_batch([user_id]).get(user_id, 0)


def period_points_batch(user_ids: list[int], start_date: str) -> dict[int, int]:
    """Points earned in a window starting at a local date string.

    Per platform: weighted bucket deltas between the latest snapshot and the
    last snapshot strictly before the window start (else the earliest one),
    each bucket clamped at 0. If the baseline row has NULL buckets (old rows),
    the platform's total delta counts as medium. Mid-week joiners only count
    from their first snapshot.
    """
    from points import HARD_POINTS, MEDIUM_POINTS, EASY_POINTS

    def _bucketed(latest: tuple, base: tuple) -> int:
        _, _, le, me, he = latest
        _, _, be, bm, bh = base
        if None in (le, me, he, be, bm, bh):
            return max(0, latest[1] - base[1]) * MEDIUM_POINTS
        return (
            max(0, le - be) * EASY_POINTS
            + max(0, me - bm) * MEDIUM_POINTS
            + max(0, he - bh) * HARD_POINTS
        )

    rows = get_snapshots(user_ids)
    by_pair: dict[tuple[int, str], list[tuple[str, int, int | None, int | None, int | None]]] = {}
    for row in rows:
        by_pair.setdefault((row["user_id"], row["platform"]), []).append(
            (row["date"], row["total_solved"], row.get("easy"), row.get("medium"), row.get("hard"))
        )
    out: dict[int, int] = {}
    for (uid, _), points in by_pair.items():
        points.sort()
        latest = points[-1]
        base = points[0]
        for point in points:
            if point[0] < start_date:
                base = point
        out[uid] = out.get(uid, 0) + _bucketed(latest, base)
    for uid in user_ids:
        out.setdefault(uid, 0)
    return out


def score_from_snapshots(rows: list[dict[str, Any]], start_date: str) -> dict[int, int]:
    """Fold snapshot rows into {user_id: score}. Per platform: latest total
    minus the total at the last snapshot strictly before start (else the
    earliest snapshot); each contribution clamped at 0. Strictly-before keeps
    same-day upserts from zeroing the window they open."""
    by_pair: dict[tuple[int, str], list[tuple[str, int]]] = {}
    for row in rows:
        by_pair.setdefault((row["user_id"], row["platform"]), []).append((row["date"], row["total_solved"]))
    scores: dict[int, int] = {}
    for (uid, _), points in by_pair.items():
        points.sort()
        latest = points[-1][1]
        base = points[0][1]
        for day, total in points:
            if day < start_date:
                base = total
        scores[uid] = scores.get(uid, 0) + max(0, latest - base)
    return scores


def window_scores(user_ids: list[int], start_date: str) -> dict[int, int]:
    """{user_id: score} for windows starting at a local date string. One query + fold."""
    rows = get_snapshots(user_ids)
    scores = score_from_snapshots(rows, start_date)
    for uid in user_ids:
        scores.setdefault(uid, 0)
    return scores


def solve_difficulty_counts(user_ids: list[int], start_utc_iso: str) -> dict[int, dict[str, int]]:
    """{user_id: {easy, medium, hard}} solves in window (verified only). One GROUP BY."""
    out: dict[int, dict[str, int]] = {uid: {"easy": 0, "medium": 0, "hard": 0} for uid in user_ids}
    if not user_ids:
        return out
    init_db()
    with _connect() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        cur = conn.execute(
            f"""
            SELECT s.user_id, s.difficulty, COUNT(*)
            FROM solves s
            JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
            WHERE s.user_id IN ({placeholders}) AND l.verified = 1 AND s.solved_at >= ?
            GROUP BY s.user_id, s.difficulty
            """,
            (*user_ids, start_utc_iso),
        )
        for uid, diff, count in cur.fetchall():
            if diff in out[uid]:
                out[uid][diff] = count
    return out


def user_rank_info(user_ids: list[int]) -> dict[int, tuple[int | None, str]]:
    """{user_id: (best_rating, registered_at)} in one query for tie-breaks."""
    if not user_ids:
        return {}
    init_db()
    with _connect() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        cur = conn.execute(
            f"""
            SELECT u.user_id, u.registered_at,
                   (SELECT MAX(ps.rating) FROM platform_stats ps WHERE ps.user_id = u.user_id)
            FROM users u WHERE u.user_id IN ({placeholders})
            """,
            tuple(user_ids),
        )
        return {row[0]: (row[2], row[1]) for row in cur.fetchall()}


def daily_sums(user_id: int, start_date: str, days: int = 7) -> dict[str, int]:
    """{UTC date: solves} for trailing local days, verified platforms only. One GROUP BY."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT d.date, SUM(d.count)
            FROM platform_daily d
            JOIN linked_accounts l ON l.user_id = d.user_id AND l.platform = d.platform
            WHERE d.user_id = ? AND l.verified = 1 AND d.date >= ?
            GROUP BY d.date
            """,
            (user_id, start_date),
        )
        out = {row[0]: row[1] for row in cur.fetchall()}
    return out


def snapshot_platforms(user_id: int) -> list[str]:
    """Verified platforms with at least one snapshot row (i.e. counted in totals)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT s.platform
            FROM stats_snapshots s
            JOIN linked_accounts l ON l.user_id = s.user_id AND l.platform = s.platform
            WHERE s.user_id = ? AND l.verified = 1
            ORDER BY 1
            """,
            (user_id,),
        )
        return [row[0] for row in cur.fetchall()]


# ---- Guild settings (announce channel + weekly podium state). Additive only.
# No user data lives here, so delete_user_data intentionally ignores it.


def get_guild_settings(guild_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_guild_settings() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM guild_settings WHERE announce_channel_id IS NOT NULL")
        return [dict(row) for row in cur.fetchall()]


def set_announce_channel(guild_id: int, channel_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO guild_settings (guild_id, announce_channel_id, last_announced_week, announce_failures)
            VALUES (?, ?, NULL, 0)
            ON CONFLICT(guild_id) DO UPDATE SET announce_channel_id = excluded.announce_channel_id,
                                                announce_failures = 0
            """,
            (guild_id, channel_id),
        )


def clear_announce_channel(guild_id: int) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE guild_settings SET announce_channel_id = NULL, announce_failures = 0 "
            "WHERE guild_id = ? AND announce_channel_id IS NOT NULL",
            (guild_id,),
        )
        return cur.rowcount > 0


def mark_week_announced(guild_id: int, week_label: str) -> None:
    """Record a posted week. Only called after a successful send."""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO guild_settings (guild_id, announce_channel_id, last_announced_week, announce_failures)
            VALUES (?, NULL, ?, 0)
            ON CONFLICT(guild_id) DO UPDATE SET last_announced_week = excluded.last_announced_week,
                                                announce_failures = 0
            """,
            (guild_id, week_label),
        )


def record_announce_failure(guild_id: int) -> int:
    """Count a failed post. Returns the consecutive failure count."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE guild_settings SET announce_failures = announce_failures + 1 WHERE guild_id = ?",
            (guild_id,),
        )
        cur = conn.execute("SELECT announce_failures FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = cur.fetchone()
        return row[0] if row else 0


def set_consent(user_id: int, version: str, username: str = "") -> None:
    """Record privacy consent, creating a minimal user row if needed."""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, registered_at, consent_version, consented_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET consent_version = excluded.consent_version,
                                               consented_at = excluded.consented_at
            """,
            (user_id, username, _now(), version, _now()),
        )


def get_consent(user_id: int) -> tuple[str | None, str | None]:
    """Return (consent_version, consented_at) for a user, or (None, None)."""
    user = get_user(user_id)
    if user is None:
        return None, None
    return user.get("consent_version"), user.get("consented_at")


def needs_consent(user_id: int, policy_version: str) -> bool:
    """True when the user never consented or consented to an older policy."""
    version, _ = get_consent(user_id)
    return version != policy_version


def export_user_data(user_id: int) -> dict[str, Any]:
    """Everything stored for one user, keyed by table. Never anyone else's rows."""
    init_db()
    out: dict[str, Any] = {}
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            if "user_id" not in cols:
                continue
            out[table] = [
                dict(row)
                for row in conn.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,))
            ]
    return out


def get_meta(key: str) -> str | None:
    init_db()
    with _connect() as conn:
        cur = conn.execute("SELECT value FROM bot_meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def set_meta(key: str, value: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bot_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
