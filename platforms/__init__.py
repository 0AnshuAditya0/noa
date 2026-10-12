"""Platform adapter registry. New platforms plug in here."""
from __future__ import annotations

from platforms.atcoder import AtCoderAdapter
from platforms.base import PlatformAdapter, PlatformError
from platforms.codeforces import CodeforcesAdapter
from platforms.leetcode import LeetCodeAdapter

ADAPTERS: dict[str, PlatformAdapter] = {
    "codeforces": CodeforcesAdapter(),
    "leetcode": LeetCodeAdapter(),
    "atcoder": AtCoderAdapter(),
}

__all__ = ["ADAPTERS", "PlatformAdapter", "PlatformError"]
