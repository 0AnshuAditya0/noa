"""Registration gate: handle exclusivity + unverified accounts never count."""
import db


def test_handle_verified_by_single_user_case_insensitive():
    db.create_user(51, "a")
    db.create_user(52, "b")
    assert db.create_pending_link(51, "codeforces", "CoolHandle", "tok-a") == "ok"
    # Pending alone blocks nobody yet, but B cannot take it once A verifies.
    assert db.handle_verified_by("codeforces", "coolhandle") is None
    assert db.set_verified(51, "codeforces") is True
    assert db.handle_verified_by("codeforces", "COOLHANDLE") == 51
    assert db.create_pending_link(52, "codeforces", "coolhandle", "tok-b") == "taken"
    assert db.create_pending_link(52, "codeforces", "COOLHANDLE", "tok-b") == "taken"


def test_unverified_account_never_counts():
    db.create_user(53, "a")
    db.create_user(54, "b")
    db.create_pending_link(53, "codeforces", "pending-handle", "t")
    db.upsert_solves(53, "codeforces", [
        {"problem_id": "1A", "difficulty": "easy", "solved_at": "2026-10-12T00:00:00+00:00"},
    ])
    db.write_platform_stats(53, "codeforces", 1, 1, 0, 0, 1200, 1200)
    assert db.platform_totals(53) == {"total": 0, "easy": 0, "medium": 0, "hard": 0}
    board_ids = {row["user_id"] for row in db.problems_board()}
    assert 53 not in board_ids


def test_verified_account_counts_after_verification():
    db.create_user(55, "a")
    db.create_pending_link(55, "codeforces", "real-handle", "t")
    assert db.set_verified(55, "codeforces") is True
    db.upsert_solves(55, "codeforces", [
        {"problem_id": "1A", "difficulty": "easy", "solved_at": "2026-10-12T00:00:00+00:00"},
    ])
    db.write_platform_stats(55, "codeforces", 1, 1, 0, 0, 1200, 1200)
    assert db.platform_totals(55)["total"] == 1
    board_ids = {row["user_id"] for row in db.problems_board()}
    assert 55 in board_ids
