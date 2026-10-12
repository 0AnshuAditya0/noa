"""Codeforces adapter over the official read-only API.

Uses user.info (profile + organization field) and user.status (submissions).
Rate limit: max 1 request per 2 seconds, shared process-wide via module
globals. Difficulty buckets: rating <1200 easy, 1200-1799 medium, 1800+ hard
(unrated problems count as medium). Only verdict OK counts, deduped by
contestId+index keeping the earliest solve.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from platforms.base import PlatformAdapter, PlatformError

API_BASE = "https://codeforces.com/api"
TIMEOUT = 10.0
MIN_INTERVAL = 2.0

_lock = asyncio.Lock()
_last_call: float = 0.0


def difficulty_for(rating: Any) -> str:
    if not isinstance(rating, (int, float)):
        return "medium"
    if rating < 1200:
        return "easy"
    if rating < 1800:
        return "medium"
    return "hard"


def parse_submissions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, tuple[str, str]] = {}
    for sub in items:
        if sub.get("verdict") != "OK":
            continue
        problem = sub.get("problem") or {}
        if "contestId" not in problem or "index" not in problem:
            continue
        pid = f"{problem['contestId']}{problem['index']}"
        ts = sub.get("creationTimeSeconds") or 0
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        diff = difficulty_for(problem.get("rating"))
        if pid not in best or iso < best[pid][0]:
            best[pid] = (iso, diff)
    return [{"problem_id": pid, "difficulty": diff, "solved_at": iso} for pid, (iso, diff) in best.items()]


class CodeforcesAdapter(PlatformAdapter):
    platform = "codeforces"

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        global _last_call
        async with _lock:
            now = time.monotonic()
            wait = MIN_INTERVAL - (now - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                    resp = await client.get(f"{API_BASE}/{method}", params=params)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise PlatformError(f"Codeforces request failed: {type(exc).__name__}") from exc
            _last_call = time.monotonic()
        try:
            data = resp.json()
        except ValueError as exc:
            raise PlatformError(f"Codeforces returned non-JSON (HTTP {resp.status_code})") from exc
        if not isinstance(data, dict) or data.get("status") != "OK":
            comment = data.get("comment", "unknown error") if isinstance(data, dict) else "bad payload"
            raise PlatformError(f"Codeforces API error: {comment}")
        return data.get("result")

    async def _user_info(self, handle: str) -> dict[str, Any] | None:
        try:
            result = await self._call("user.info", {"handles": handle})
        except PlatformError:
            return None
        if isinstance(result, list) and result:
            return result[0]
        return None

    async def fetch_profile(self, handle: str) -> dict[str, Any]:
        info = await self._user_info(handle)
        if info is None:
            return {"rating": None, "max_rating": None, "exists": False}
        return {"rating": info.get("rating"), "max_rating": info.get("maxRating"), "exists": True}

    async def get_verification_field(self, handle: str) -> str:
        info = await self._user_info(handle)
        if info is None:
            return ""
        return str(info.get("organization") or "")

    async def fetch_solves(self, handle: str, since: str | None = None) -> list[dict[str, Any]]:
        result = await self._call("user.status", {"handle": handle, "from": 1, "count": 10000})
        solves = parse_submissions(result if isinstance(result, list) else [])
        if since:
            solves = [s for s in solves if s["solved_at"] >= since]
        return solves
