"""Abstract adapter every coding platform must implement.

Adding a platform later means subclassing PlatformAdapter and registering
one instance in platforms/__init__.py — callers only use this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PlatformError(Exception):
    """Raised when the platform API fails (network, bad handle, rate limit)."""


def count_by_date(solves: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Fold [{solved_at: UTC ISO, ...}] into sorted [(UTC date, count)] rows."""
    counts: dict[str, int] = {}
    for s in solves:
        day = str(s.get("solved_at", ""))[:10]
        if len(day) == 10 and day[4] == "-" and day[7] == "-":
            counts[day] = counts.get(day, 0) + 1
    return sorted(counts.items())


class PlatformAdapter(ABC):
    platform: str = ""
    # Human label for the profile field users edit to prove ownership.
    verification_field_label: str = "profile"
    # True when the adapter serves native per-day activity (no derivation needed).
    NATIVE_DAILY: bool = False
    # True when fetch_profile returns authoritative lifetime totals
    # (total_solved/easy/medium/hard) instead of deriving them from solves.
    AUTHORITATIVE_PROFILE_STATS: bool = False

    @abstractmethod
    async def fetch_profile(self, handle: str) -> dict[str, Any]:
        """Return {rating, max_rating, exists} (+ optional totals). Never raises for unknown handles."""

    @abstractmethod
    async def fetch_solves(self, handle: str, since: str | None = None) -> list[dict[str, Any]]:
        """Return [{problem_id, difficulty, solved_at}]. solved_at is UTC ISO."""

    @abstractmethod
    async def get_verification_field(self, handle: str) -> str:
        """Return the profile text the user edits to prove ownership."""

    async def fetch_daily_counts(self, handle: str) -> list[tuple[str, int]] | None:
        """Optional: [(UTC date YYYY-MM-DD, solves that day)]. None = unsupported."""
        return None
