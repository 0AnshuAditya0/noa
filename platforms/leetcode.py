"""LeetCode adapter over the public GraphQL endpoint (leetcode.com only).

Profile totals come from submitStats (authoritative: the recent-submissions
list is only a best-effort window). Contest rating is used when contest
history exists, else None (unrated, never an error). Verification reads the
profile "About me" bio. Daily activity comes from the submission calendar.
Every payload is validated defensively: transport/GraphQL problems raise
PlatformError with a clear message, unknown handles report exists=False,
and a changed schema degrades to empty results instead of crashing.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from platforms.base import PlatformAdapter, PlatformError

GRAPHQL_URL = "https://leetcode.com/graphql"
TIMEOUT = 10.0
MIN_INTERVAL = 2.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://leetcode.com/",
    "Content-Type": "application/json",
}

_lock = asyncio.Lock()
_last_call: float = 0.0

PROFILE_QUERY = """
query userProfile($username: String!) {
  matchedUser(username: $username) {
    username
    submitStats { acSubmissionNum { difficulty count submissions } }
    profile { aboutMe }
  }
  userContestRanking(username: $username) { rating attendedContestsCount }
}
"""

CALENDAR_YEARS_QUERY = """
query userCalendarYears($username: String!, $year1: Int!, $year2: Int!) {
  matchedUser(username: $username) {
    y1: userCalendar(year: $year1) { submissionCalendar }
    y2: userCalendar(year: $year2) { submissionCalendar }
  }
}
"""

CALENDAR_QUERY = """
query userCalendarPlain($username: String!) {
  matchedUser(username: $username) { userCalendar { submissionCalendar } }
}
"""

RECENT_QUERY = """
query userRecent($username: String!) {
  recentAcSubmissionList(username: $username, limit: 20) { titleSlug timestamp }
}
"""


def parse_stat_counts(entries: Any) -> dict[str, int]:
    """Fold acSubmissionNum entries into {total, easy, medium, hard}."""
    out = {"total": 0, "easy": 0, "medium": 0, "hard": 0}
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        diff = str(entry.get("difficulty", ""))
        try:
            count = int(entry.get("count", 0) or 0)
        except (TypeError, ValueError):
            continue
        if diff == "All":
            out["total"] = count
        elif diff == "Easy":
            out["easy"] = count
        elif diff == "Medium":
            out["medium"] = count
        elif diff == "Hard":
            out["hard"] = count
    if out["total"] == 0:
        out["total"] = out["easy"] + out["medium"] + out["hard"]
    return out


def parse_calendar_value(raw: Any) -> list[tuple[str, int]]:
    """Decode a submissionCalendar JSON string into sorted [(UTC date, count)]."""
    if not raw:
        return []
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(decoded, dict):
            return []
        items = decoded.items()
    else:
        return []
    rows: dict[str, int] = {}
    for key, value in items:
        try:
            day = datetime.fromtimestamp(int(key), tz=timezone.utc).date().isoformat()
            count = int(value)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if count > 0:
            rows[day] = rows.get(day, 0) + count
    return sorted(rows.items())


def parse_recent_list(items: Any) -> list[dict[str, Any]]:
    """Map recent accepted submissions to solves (difficulty unknown -> medium)."""
    if not isinstance(items, list):
        return []
    solves: list[dict[str, Any]] = []
    for sub in items:
        if not isinstance(sub, dict):
            continue
        slug = sub.get("titleSlug")
        ts = sub.get("timestamp")
        if not slug:
            continue
        try:
            iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        solves.append({"problem_id": str(slug), "difficulty": "medium", "solved_at": iso})
    return solves


class LeetCodeAdapter(PlatformAdapter):
    platform = "leetcode"
    verification_field_label = "About me"
    NATIVE_DAILY = True
    AUTHORITATIVE_PROFILE_STATS = True

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        global _last_call
        async with _lock:
            now = time.monotonic()
            wait = MIN_INTERVAL - (now - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
                    resp = await client.post(GRAPHQL_URL, json={"query": query, "variables": variables})
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                raise PlatformError(f"LeetCode request failed: {type(exc).__name__}") from exc
            _last_call = time.monotonic()
        if resp.status_code == 403:
            raise PlatformError("LeetCode blocked the request (HTTP 403, bot protection)")
        if resp.status_code == 429:
            raise PlatformError("LeetCode rate limited the request (HTTP 429)")
        if resp.status_code != 200:
            detail = resp.text[:160].replace("\n", " ")
            raise PlatformError(f"LeetCode returned HTTP {resp.status_code}: {detail}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise PlatformError("LeetCode returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise PlatformError("LeetCode returned an unexpected response shape")
        errors = payload.get("errors")
        if errors:
            message = errors[0].get("message", "unknown GraphQL error") if isinstance(errors[0], dict) else str(errors[0])
            raise PlatformError(f"LeetCode GraphQL error: {message}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlatformError("LeetCode returned an unexpected response shape")
        return data

    async def fetch_profile(self, handle: str) -> dict[str, Any]:
        try:
            data = await self._gql(PROFILE_QUERY, {"username": handle})
        except PlatformError as exc:
            message = str(exc).lower()
            if "not exist" in message or "not found" in message or "no such user" in message:
                return {"rating": None, "max_rating": None, "exists": False,
                        "total_solved": 0, "easy": 0, "medium": 0, "hard": 0}
            raise
        matched = data.get("matchedUser")
        if not isinstance(matched, dict):
            return {"rating": None, "max_rating": None, "exists": False,
                    "total_solved": 0, "easy": 0, "medium": 0, "hard": 0}
        stats = matched.get("submitStats") or {}
        counts = parse_stat_counts(stats.get("acSubmissionNum"))
        contest = data.get("userContestRanking")
        rating = None
        if isinstance(contest, dict):
            attended = contest.get("attendedContestsCount") or 0
            value = contest.get("rating")
            if attended and isinstance(value, (int, float)):
                rating = int(value)
        return {"rating": rating, "max_rating": None, "exists": True,
                "total_solved": counts["total"], "easy": counts["easy"],
                "medium": counts["medium"], "hard": counts["hard"]}

    async def get_verification_field(self, handle: str) -> str:
        try:
            data = await self._gql(PROFILE_QUERY, {"username": handle})
        except PlatformError:
            return ""
        matched = data.get("matchedUser")
        if not isinstance(matched, dict):
            return ""
        profile = matched.get("profile")
        if not isinstance(profile, dict):
            return ""
        return str(profile.get("aboutMe") or "")

    async def fetch_solves(self, handle: str, since: str | None = None) -> list[dict[str, Any]]:
        data = await self._gql(RECENT_QUERY, {"username": handle})
        solves = parse_recent_list(data.get("recentAcSubmissionList"))
        if since:
            solves = [s for s in solves if s["solved_at"] >= since]
        return solves

    async def fetch_daily_counts(self, handle: str) -> list[tuple[str, int]] | None:
        year = datetime.now(timezone.utc).year
        try:
            data = await self._gql(
                CALENDAR_YEARS_QUERY, {"username": handle, "year1": year, "year2": year - 1}
            )
            matched = data.get("matchedUser")
            calendars = []
            if isinstance(matched, dict):
                for alias in ("y1", "y2"):
                    node = matched.get(alias)
                    if isinstance(node, dict):
                        calendars.append(node.get("submissionCalendar"))
            else:
                raise PlatformError("LeetCode returned no user for the calendar query")
        except PlatformError:
            # Year-parameterized calendar unsupported (or transient failure):
            # fall back to the plain calendar query; if that fails too, raise it.
            data = await self._gql(CALENDAR_QUERY, {"username": handle})
            matched = data.get("matchedUser")
            node = matched.get("userCalendar") if isinstance(matched, dict) else None
            calendars = [node.get("submissionCalendar")] if isinstance(node, dict) else []
        merged: dict[str, int] = {}
        for raw in calendars:
            for day, count in parse_calendar_value(raw):
                merged[day] = merged.get(day, 0) + count
        return sorted(merged.items())
