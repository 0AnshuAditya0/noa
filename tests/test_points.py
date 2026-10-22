"""Points math and rank tiers (pure points.py)."""
import points
from points import (
    ACOLYTE_THRESHOLD,
    ARCHON_THRESHOLD,
    CENTURION_THRESHOLD,
    EASY_POINTS,
    HARD_POINTS,
    MEDIUM_POINTS,
    POINT_WEIGHTS,
    SENTINEL_THRESHOLD,
    TITAN_THRESHOLD,
    WARDEN_THRESHOLD,
)


def test_weights_and_threshold_constants():
    assert (EASY_POINTS, MEDIUM_POINTS, HARD_POINTS) == (1, 3, 10)
    assert POINT_WEIGHTS == {"easy": 1, "medium": 3, "hard": 10}
    assert (ACOLYTE_THRESHOLD, SENTINEL_THRESHOLD, WARDEN_THRESHOLD,
            CENTURION_THRESHOLD, ARCHON_THRESHOLD, TITAN_THRESHOLD) == (0, 250, 600, 1200, 2200, 3500)


def test_ranks_list_in_order():
    assert [r["name"] for r in points.RANKS] == [
        "Acolyte", "Sentinel", "Warden", "Centurion", "Archon", "Titan"]
    thresholds = [r["threshold"] for r in points.RANKS]
    assert thresholds == sorted(thresholds)
    for r in points.RANKS:
        assert set(r) == {"name", "threshold", "emoji"}


def test_points_from_counts():
    assert points.points_from_counts(0, 0, 0) == 0
    assert points.points_from_counts(2, 1, 0) == 5
    assert points.points_from_counts(0, 0, 1) == 10
    assert points.points_from_counts(180, 200, 32) == 180 + 600 + 320


def test_rank_for_points_boundaries():
    assert points.rank_for_points(0) == "Acolyte"
    assert points.rank_for_points(249) == "Acolyte"
    assert points.rank_for_points(250) == "Sentinel"
    assert points.rank_for_points(599) == "Sentinel"
    assert points.rank_for_points(600) == "Warden"
    assert points.rank_for_points(1199) == "Warden"
    assert points.rank_for_points(1200) == "Centurion"
    assert points.rank_for_points(2199) == "Centurion"
    assert points.rank_for_points(2200) == "Archon"
    assert points.rank_for_points(3499) == "Archon"
    assert points.rank_for_points(3500) == "Titan"
    assert points.rank_for_points(99999) == "Titan"
    assert points.rank_for_points(-5) == "Acolyte"


def test_progress_to_next():
    assert points.progress_to_next(0) == ("Acolyte", "Sentinel", 0, 250)
    assert points.progress_to_next(100) == ("Acolyte", "Sentinel", 100, 150)
    assert points.progress_to_next(250) == ("Sentinel", "Warden", 0, 350)
    assert points.progress_to_next(2199) == ("Centurion", "Archon", 999, 1)


def test_progress_to_next_titan_has_no_next():
    cur, nxt, into, need = points.progress_to_next(3500)
    assert (cur, nxt, need) == ("Titan", None, 0)
    assert into == 0
    cur, nxt, into, need = points.progress_to_next(4000)
    assert cur == "Titan" and nxt is None and into == 500 and need == 0


def test_apply_remainder_counts_shortfall_as_medium():
    assert points.apply_remainder(10, 4, 3, 1) == (4, 5, 1)
    assert points.apply_remainder(5, 2, 2, 1) == (2, 2, 1)
    assert points.apply_remainder(0, 0, 0, 0) == (0, 0, 0)
