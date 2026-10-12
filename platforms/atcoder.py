"""AtCoder adapter.

Rating: per-user history JSON on atcoder.jp (latest entry's NewRating is
current, max over entries; empty history + profile page 200 = unrated user,
profile 404 = missing user). History returns 200 [] for missing users, so the
profile page is the existence check in that case.
Solves: AtCoder Problems submissions API, paged forward with from_second
(overlap-safe: dedupe by problem keeps the earliest solve). Incremental sync
converts the stored ISO `since` to epoch seconds. Only result "AC" counts.
Difficulty: community estimates JSON, cached in memory for 24h.
Thresholds: estimate <400 easy, 400-1199 medium, 1200+ hard, missing medium.
Daily counts derive from solves (no extra calls). Verification reads the
Affiliation field from the profile HTML with a markup-tolerant parse.

Polite usage: max 1 request per second process-wide, descriptive User-Agent.
Every shape verified live before relying on it; anything unexpected raises
PlatformError with a clear message instead of crashing.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from platforms.base import PlatformAdapter, PlatformError

TIMEOUT = 10.0
MIN_INTERVAL = 1.0
USER_AGENT = "NoaBot/1.0 (Discord study bot; polite usage, max 1 req/s)"
HISTORY_URL = "https://atcoder.jp/users/{handle}/history/json"
PROFILE_URL = "https://atcoder.jp/users/{handle}"
SUBMISSIONS_URL = "https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions"
MODELS_URL = "https://kenkoooo.com/atcoder/resources/problem-models.json"
PAGE_SIZE = 500
MAX_PAGES = 100
MODELS_TTL = 24 * 3600

EASY_BELOW = 400
MEDIUM_BELOW = 1200

_lock = asyncio.Lock()
_last_call: float = 0.0
_models: dict[str, Any] | None = None
_models_fetched_at: float = 0.0

_AFFILIATION_RE = re.compile(
    r"<th[^>]*>\s*affiliation\s*</th>\s*<td[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def difficulty_for(estimate: Any) -> str:
    if not isinstance(estimate, (int, float)) or isinstance(estimate, bool):
        return "medium"
    if estimate < EASY_BELOW:
        return "easy"
    if estimate < MEDIUM_BELOW:
        return "medium"
    return "hard"


def parse_history(items: Any) -> tuple[int | None, int | None] | None:
    """Return (current, max) rating, or None when there is no contest history."""
    if not isinstance(items, list) or not items:
        return None
    current: int | None = None
    peak: int | None = None
    for entry in items:
        if not isinstance(entry, dict):
            continue
        value = entry.get("NewRating")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        current = int(value)
        peak = int(value) if peak is None else max(peak, int(value))
    if current is None:
        return None
    return current, peak


def parse_submissions(items: Any, models: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Keep AC verdicts only, deduped by problem_id with the earliest solve."""
    models = models or {}
    best: dict[str, tuple[str, str]] = {}
    if not isinstance(items, list):
        return []
    for sub in items:
        if not isinstance(sub, dict) or sub.get("result") != "AC":
            continue
        pid = sub.get("problem_id")
        ts = sub.get("epoch_second")
        if not pid or not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue
        try:
            iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            continue
        info = models.get(str(pid))
        diff = difficulty_for(info.get("difficulty") if isinstance(info, dict) else None)
        key = str(pid)
        if key not in best or iso < best[key][0]:
            best[key] = (iso, diff)
    return [{"problem_id": pid, "difficulty": diff, "solved_at": iso} for pid, (iso, diff) in best.items()]


def parse_affiliation(page_html: str) -> str | None:
    """Extract the Affiliation cell, tolerant of attribute/order changes. None if absent."""
    match = _AFFILIATION_RE.search(page_html)
    if not match:
        return None
    text = _TAG_RE.sub("", match.group(1))
    return html.unescape(text).strip()


class AtCoderAdapter(PlatformAdapter):
    platform = "atcoder"
    verification_field_label = "Affiliation"
    NATIVE_DAILY = False
    AUTHORITATIVE_PROFILE_STATS = False

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        async with _lock:
            await self._wait_turn()
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
                    resp = await client.get(url, params=params)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise PlatformError(f"AtCoder request failed: {type(exc).__name__}") from exc
            global _last_call
            _last_call = time.monotonic()
        if resp.status_code == 404:
            raise PlatformError("AtCoder returned HTTP 404 (unknown user or page)")
        if resp.status_code != 200:
            raise PlatformError(f"AtCoder returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise PlatformError("AtCoder returned non-JSON") from exc

    async def _get_page(self, url: str) -> str:
        async with _lock:
            await self._wait_turn()
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
                    resp = await client.get(url)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise PlatformError(f"AtCoder request failed: {type(exc).__name__}") from exc
            global _last_call
            _last_call = time.monotonic()
        if resp.status_code == 404:
            raise PlatformError("AtCoder returned HTTP 404 (unknown user)")
        if resp.status_code != 200:
            raise PlatformError(f"AtCoder returned HTTP {resp.status_code}")
        return resp.text

    @staticmethod
    async def _wait_turn() -> None:
        global _last_call
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)

    async def _models(self) -> dict[str, Any]:
        global _models, _models_fetched_at
        if _models is not None and time.monotonic() - _models_fetched_at < MODELS_TTL:
            return _models
        data = await self._get_json(MODELS_URL)
        if not isinstance(data, dict):
            raise PlatformError("AtCoder difficulty data has an unexpected shape")
        _models = data
        _models_fetched_at = time.monotonic()
        return _models

    async def _profile_exists(self, handle: str) -> bool:
        try:
            await self._get_page(PROFILE_URL.format(handle=handle))
        except PlatformError as exc:
            if "404" in str(exc):
                return False
            raise
        return True

    async def fetch_profile(self, handle: str) -> dict[str, Any]:
        try:
            history = await self._get_json(HISTORY_URL.format(handle=handle))
        except PlatformError as exc:
            if "404" in str(exc):
                return {"rating": None, "max_rating": None, "exists": False}
            raise
        parsed = parse_history(history)
        if parsed is not None:
            current, peak = parsed
            return {"rating": current, "max_rating": peak, "exists": True}
        if await self._profile_exists(handle):
            return {"rating": None, "max_rating": None, "exists": True}
        return {"rating": None, "max_rating": None, "exists": False}

    async def get_verification_field(self, handle: str) -> str:
        try:
            page = await self._get_page(PROFILE_URL.format(handle=handle))
        except PlatformError:
            return ""
        found = parse_affiliation(page)
        if found is None:
            raise PlatformError("AtCoder profile has no readable Affiliation field (page layout changed?)")
        return found

    async def fetch_solves(self, handle: str, since: str | None = None) -> list[dict[str, Any]]:
        if since:
            try:
                from_second = int(datetime.fromisoformat(since).timestamp())
            except (TypeError, ValueError):
                from_second = 0
        else:
            from_second = 0
        models = await self._models()
        merged: dict[str, tuple[str, str]] = {}
        for _ in range(MAX_PAGES):
            data = await self._get_json(SUBMISSIONS_URL, {"user": handle, "from_second": from_second})
            if not isinstance(data, list):
                raise PlatformError("AtCoder submissions have an unexpected shape")
            if not data:
                break
            for solve in parse_submissions(data, models):
                pid = solve["problem_id"]
                if pid not in merged or solve["solved_at"] < merged[pid][0]:
                    merged[pid] = (solve["solved_at"], solve["difficulty"])
            try:
                from_second = max(int(s.get("epoch_second", 0)) for s in data if isinstance(s, dict))
            except (TypeError, ValueError):
                break
            if len(data) < PAGE_SIZE:
                break
        return [
            {"problem_id": pid, "difficulty": diff, "solved_at": iso}
            for pid, (iso, diff) in merged.items()
        ]

    async def fetch_daily_counts(self, handle: str) -> list[tuple[str, int]] | None:
        """Derived from the full solve history — no extra API calls."""
        from platforms.base import count_by_date

        return count_by_date(await self.fetch_solves(handle))
