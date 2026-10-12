"""Platform adapter registry. New platforms plug in here."""
from __future__ import annotations

from platforms.base import PlatformAdapter, PlatformError
from platforms.codeforces import CodeforcesAdapter

ADAPTERS: dict[str, PlatformAdapter] = {
    "codeforces": CodeforcesAdapter(),
}

__all__ = ["ADAPTERS", "PlatformAdapter", "PlatformError"]
