"""Abstract adapter every coding platform must implement.

Adding a platform later means subclassing PlatformAdapter and registering
one instance in platforms/__init__.py — callers only use this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PlatformError(Exception):
    """Raised when the platform API fails (network, bad handle, rate limit)."""


class PlatformAdapter(ABC):
    platform: str = ""

    @abstractmethod
    async def fetch_profile(self, handle: str) -> dict[str, Any]:
        """Return {rating, max_rating, exists}. Never raises for unknown handles."""

    @abstractmethod
    async def fetch_solves(self, handle: str, since: str | None = None) -> list[dict[str, Any]]:
        """Return [{problem_id, difficulty, solved_at}]. solved_at is UTC ISO."""

    @abstractmethod
    async def get_verification_field(self, handle: str) -> str:
        """Return the profile text the user edits to prove ownership."""
