"""delete_user_data must clear every table holding user rows.

The expected set is hardcoded on purpose: if a future table with a user_id
column is added without wiring it into delete_user_data, this fails loudly.
"""
import db

EXPECTED_USER_TABLES = {
    "users",
    "progress",
    "user_activity",
    "linked_accounts",
    "solves",
    "platform_stats",
    "platform_daily",
    "stats_snapshots",
    "dsa_learned",
}


def _tables_with_user_id():
    with db._connect() as conn:
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        found = set()
        for table in tables:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            if "user_id" in cols:
                found.add(table)
    return found


def test_no_forgotten_user_tables():
    assert _tables_with_user_id() == EXPECTED_USER_TABLES


def _populate(uid):
    db.create_user(uid, f"u{uid}", active_path="swe")
    db.update_user(uid, bio="Likes graphs")
    db.set_consent(uid, "2026-10-1", username=f"u{uid}")
    db.mark_done(uid, "swe", "05.2")
    db.log_activity(uid, "2026-10-12")
    db.create_pending_link(uid, "codeforces", f"h{uid}", "t")
    db.set_verified(uid, "codeforces")
    assert db.upsert_solves(uid, "codeforces", [
        {"problem_id": "1A", "difficulty": "easy", "solved_at": "2026-10-12T00:00:00+00:00"},
    ]) == 1
    db.write_platform_stats(uid, "codeforces", 1, 1, 0, 0, 1200, 1200)
    assert db.upsert_daily(uid, "codeforces", [("2026-10-12", 1)]) == 1
    db.upsert_snapshot(uid, "codeforces", "2026-10-12", 1)
    assert db.mark_learned(uid, "pat.sliding-window") is True
    # guild_settings has no user_id: it must be left alone.
    db.set_announce_channel(9001, 9002)


def _counts(uid):
    with db._connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (uid,)).fetchone()[0]
            for table in EXPECTED_USER_TABLES
        }


def test_delete_clears_everything():
    _populate(61)
    before = _counts(61)
    assert all(v >= 1 for v in before.values()), before
    result = db.delete_user_data(61)
    assert set(result) == EXPECTED_USER_TABLES
    after = _counts(61)
    assert all(v == 0 for v in after.values()), after
    # Non-user table untouched.
    with db._connect() as conn:
        assert conn.execute("SELECT announce_channel_id FROM guild_settings WHERE guild_id = 9001").fetchone()[0] == 9002
        conn.execute("DELETE FROM guild_settings WHERE guild_id = 9001")
