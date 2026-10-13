"""Streak math (pure streak_from_dates)."""
import db


def test_consecutive_days():
    days = ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"]
    assert db.streak_from_dates(days, today="2026-10-12") == (5, 5)


def test_gap_resets_current_but_keeps_longest():
    days = ["2026-10-01", "2026-10-02", "2026-10-05"]
    assert db.streak_from_dates(days, today="2026-10-05") == (1, 2)


def test_today_idle_yesterday_active():
    days = ["2026-10-10", "2026-10-11"]
    assert db.streak_from_dates(days, today="2026-10-12") == (2, 2)


def test_today_idle_long_ago_breaks_current():
    days = ["2026-10-08"]
    assert db.streak_from_dates(days, today="2026-10-12") == (0, 1)


def test_empty():
    assert db.streak_from_dates([], today="2026-10-12") == (0, 0)


def test_duplicate_days_count_once():
    days = ["2026-10-11", "2026-10-11", "2026-10-12", "2026-10-12"]
    assert db.streak_from_dates(days, today="2026-10-12") == (2, 2)


def test_single_today():
    assert db.streak_from_dates(["2026-10-12"], today="2026-10-12") == (1, 1)


def test_longest_in_the_past():
    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-10", "2026-10-11"]
    assert db.streak_from_dates(days, today="2026-10-11") == (2, 3)
