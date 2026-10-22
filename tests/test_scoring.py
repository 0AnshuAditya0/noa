"""Competition scoring: snapshot deltas, clamps, same-day upserts, tie-break order."""
import db
from cogs.profile import _order_uids


def row(uid, platform, date, total):
    return {"user_id": uid, "platform": platform, "date": date, "total_solved": total}


def test_mid_week_joiner_gets_credit_from_first_snapshot():
    rows = [row(1, "cf", "2026-10-01", 10), row(1, "cf", "2026-10-08", 25)]
    assert db.score_from_snapshots(rows, "2026-10-05") == {1: 15}


def test_no_snapshot_before_start_uses_earliest():
    rows = [row(1, "cf", "2026-10-06", 50), row(1, "cf", "2026-10-09", 53)]
    assert db.score_from_snapshots(rows, "2026-10-05") == {1: 3}


def test_old_solves_never_leak_into_weekly_score():
    rows = [row(1, "cf", "2026-10-01", 100), row(1, "cf", "2026-10-03", 100)]
    assert db.score_from_snapshots(rows, "2026-10-05") == {1: 0}


def test_clamp_at_zero_on_decrease():
    rows = [row(1, "cf", "2026-10-01", 100), row(1, "cf", "2026-10-10", 90)]
    assert db.score_from_snapshots(rows, "2026-10-05") == {1: 0}


def test_same_day_upsert_does_not_zero_monday():
    # Monday's own row is the baseline cutoff, not the baseline value.
    rows = [row(1, "cf", "2026-10-10", 108), row(1, "cf", "2026-10-12", 115)]
    assert db.score_from_snapshots(rows, "2026-10-12") == {1: 7}


def test_multi_platform_and_multi_user_sum():
    rows = [
        row(1, "cf", "2026-10-04", 100), row(1, "cf", "2026-10-10", 108),
        row(1, "lc", "2026-10-06", 50), row(1, "lc", "2026-10-09", 53),
        row(2, "cf", "2026-10-05", 200), row(2, "cf", "2026-10-08", 205),
    ]
    assert db.score_from_snapshots(rows, "2026-10-05") == {1: 11, 2: 5}


def test_window_scores_end_to_end():
    for uid in (31, 32):
        db.create_user(uid, f"u{uid}")
    db.create_pending_link(31, "codeforces", "h31", "t")
    db.set_verified(31, "codeforces")
    db.upsert_snapshot(31, "codeforces", "2026-10-01", 10)
    db.upsert_snapshot(31, "codeforces", "2026-10-08", 25)
    assert db.window_scores([31, 32], "2026-10-05") == {31: 15, 32: 0}


def test_tiebreak_points_then_solved_then_rating_then_registered():
    uids = [1, 2, 3]
    points = {1: 30, 2: 30, 3: 30}
    counts = {1: 10, 2: 12, 3: 12}
    info = {
        1: (1500, "2026-01-01T00:00:00+00:00"),
        2: (1500, "2026-01-01T00:00:00+00:00"),
        3: (1600, "2026-06-01T00:00:00+00:00"),
    }
    assert _order_uids(uids, points, counts, info) == [3, 2, 1]


def test_tiebreak_unrated_sorts_last_then_earliest_registered():
    uids = [1, 2, 3]
    points = {1: 5, 2: 5, 3: 5}
    counts = {1: 5, 2: 5, 3: 5}
    info = {
        1: (None, "2026-01-01T00:00:00+00:00"),
        2: (1400, "2026-03-01T00:00:00+00:00"),
        3: (1400, "2026-02-01T00:00:00+00:00"),
    }
    assert _order_uids(uids, points, counts, info) == [3, 2, 1]


def test_points_outrank_solved_counts():
    uids = [1, 2]
    points = {1: 10, 2: 100}
    counts = {1: 50, 2: 5}
    info = {1: (3000, "2026-01-01T00:00:00+00:00"), 2: (None, "2026-06-01T00:00:00+00:00")}
    assert _order_uids(uids, points, counts, info) == [2, 1]
