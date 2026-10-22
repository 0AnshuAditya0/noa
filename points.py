"""Lifetime points and rank tiers. Pure functions, no Discord imports.

Weights: easy 1, medium 3, hard 10. Ranks: Acolyte 0, Sentinel 250,
Warden 600, Centurion 1200, Archon 2200, Titan 3500.
"""
from __future__ import annotations

from typing import Any

import emojis

EASY_POINTS = 1
MEDIUM_POINTS = 3
HARD_POINTS = 10

POINT_WEIGHTS = {"easy": EASY_POINTS, "medium": MEDIUM_POINTS, "hard": HARD_POINTS}

ACOLYTE_THRESHOLD = 0
SENTINEL_THRESHOLD = 250
WARDEN_THRESHOLD = 600
CENTURION_THRESHOLD = 1200
ARCHON_THRESHOLD = 2200
TITAN_THRESHOLD = 3500

RANKS: list[dict[str, Any]] = [
    {"name": "Acolyte", "threshold": ACOLYTE_THRESHOLD, "emoji": emojis.RANK_EMOJIS["acolyte"]},
    {"name": "Sentinel", "threshold": SENTINEL_THRESHOLD, "emoji": emojis.RANK_EMOJIS["sentinel"]},
    {"name": "Warden", "threshold": WARDEN_THRESHOLD, "emoji": emojis.RANK_EMOJIS["warden"]},
    {"name": "Centurion", "threshold": CENTURION_THRESHOLD, "emoji": emojis.RANK_EMOJIS["centurion"]},
    {"name": "Archon", "threshold": ARCHON_THRESHOLD, "emoji": emojis.RANK_EMOJIS["archon"]},
    {"name": "Titan", "threshold": TITAN_THRESHOLD, "emoji": emojis.RANK_EMOJIS["titan"]},
]


def apply_remainder(total: int, easy: int, medium: int, hard: int) -> tuple[int, int, int]:
    """If easy+medium+hard falls short of the total, count the rest as medium."""
    known = easy + medium + hard
    if total > known:
        medium += total - known
    return easy, medium, hard


def points_from_counts(easy: int, medium: int, hard: int) -> int:
    """Weighted points for bucket counts."""
    return easy * EASY_POINTS + medium * MEDIUM_POINTS + hard * HARD_POINTS


def rank_for_points(points: int) -> str:
    """Highest rank whose threshold the points reach. Never below Acolyte."""
    name = RANKS[0]["name"]
    for rank in RANKS:
        if points >= rank["threshold"]:
            name = rank["name"]
    return name


def progress_to_next(points: int) -> tuple[str, str | None, int, int]:
    """(current_rank, next_rank_or_None, points_into_rank, points_needed).

    Titan has no next rank: needed is 0.
    """
    current = RANKS[0]
    for rank in RANKS:
        if points >= rank["threshold"]:
            current = rank
    idx = RANKS.index(current)
    if idx >= len(RANKS) - 1:
        return current["name"], None, points - current["threshold"], 0
    nxt = RANKS[idx + 1]
    return current["name"], nxt["name"], points - current["threshold"], nxt["threshold"] - points
