"""Adapter tests with saved sample responses and mocked httpx. No live network.

httpx.AsyncClient is swapped for a fake; per-test route tables decide the
responses. Module rate-limit timestamps are reset so the suite stays fast.
"""
import asyncio
import json

import httpx
import pytest

import platforms.atcoder as ac_mod
import platforms.codeforces as cf_mod
import platforms.leetcode as lc_mod
from platforms.base import PlatformError


@pytest.fixture(autouse=True)
def _no_throttle_and_cold_caches():
    cf_mod._last_call = 0.0
    lc_mod._last_call = 0.0
    ac_mod._last_call = 0.0
    ac_mod._models = None
    ac_mod._models_fetched_at = 0.0
    yield
    ac_mod._models = None
    ac_mod._models_fetched_at = 0.0


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", json_error=False):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("No JSON here")
        return self._json_data


class FakeAsyncClient:
    """Routes (method, url, kwargs) to a response, an exception, or a callable."""

    def __init__(self, routes, *args, **kwargs):
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        return self._dispatch("GET", url, kwargs)

    async def post(self, url, **kwargs):
        return self._dispatch("POST", url, kwargs)

    def _dispatch(self, method, url, kwargs):
        for matcher, action in self._routes:
            if matcher(method, url, kwargs):
                if isinstance(action, BaseException):
                    raise action
                if callable(action):
                    return action(method, url, kwargs)
                return action
        raise AssertionError(f"unexpected request {method} {url} {kwargs}")


def patch_client(monkeypatch, routes):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient(routes))


def cf_ok_profile():
    return FakeResponse(200, {"status": "OK", "result": [
        {"handle": "tourist", "rating": 3615, "maxRating": 3819, "organization": "ITMO"},
    ]})


# ---------------- Codeforces ----------------

def test_cf_profile_rated(monkeypatch):
    patch_client(monkeypatch, [(lambda m, u, k: u.endswith("/user.info"), cf_ok_profile())])
    assert run(cf_mod.CodeforcesAdapter().fetch_profile("tourist")) == {
        "rating": 3615, "max_rating": 3819, "exists": True}


def test_cf_profile_unrated(monkeypatch):
    body = {"status": "OK", "result": [{"handle": "newbie"}]}
    patch_client(monkeypatch, [(lambda m, u, k: u.endswith("/user.info"), FakeResponse(200, body))])
    assert run(cf_mod.CodeforcesAdapter().fetch_profile("newbie")) == {
        "rating": None, "max_rating": None, "exists": True}


def test_cf_profile_missing_user(monkeypatch):
    body = {"status": "FAILED", "comment": "handles: User with handle zzz not found"}
    patch_client(monkeypatch, [(lambda m, u, k: u.endswith("/user.info"), FakeResponse(200, body))])
    prof = run(cf_mod.CodeforcesAdapter().fetch_profile("zzz"))
    assert prof["exists"] is False
    assert run(cf_mod.CodeforcesAdapter().get_verification_field("zzz")) == ""


def test_cf_verification_field(monkeypatch):
    patch_client(monkeypatch, [(lambda m, u, k: u.endswith("/user.info"), cf_ok_profile())])
    assert run(cf_mod.CodeforcesAdapter().get_verification_field("tourist")) == "ITMO"


def test_cf_solves_filter_dedupe_since(monkeypatch):
    subs = [
        {"verdict": "OK", "creationTimeSeconds": 100,
         "problem": {"contestId": 1, "index": "A", "rating": 800}},
        {"verdict": "WRONG_ANSWER", "creationTimeSeconds": 50,
         "problem": {"contestId": 1, "index": "A", "rating": 800}},
        {"verdict": "OK", "creationTimeSeconds": 200,
         "problem": {"contestId": 1, "index": "A", "rating": 800}},  # dup, later
        {"verdict": "OK", "creationTimeSeconds": 300, "problem": {"contestId": 2, "index": "B"}},  # unrated
        {"verdict": "TIME_LIMIT_EXCEEDED", "creationTimeSeconds": 400,
         "problem": {"contestId": 3, "index": "C", "rating": 2000}},
        {"verdict": "OK", "creationTimeSeconds": 500, "problem": {"index": "D"}},  # no contestId
    ]
    patch_client(monkeypatch, [(lambda m, u, k: u.endswith("/user.status"),
                                FakeResponse(200, {"status": "OK", "result": subs}))])
    got = {s["problem_id"]: s for s in run(cf_mod.CodeforcesAdapter().fetch_solves("u"))}
    assert set(got) == {"1A", "2B"}
    assert got["1A"]["solved_at"].endswith("+00:00") and "00:01:40" in got["1A"]["solved_at"]
    assert got["2B"]["difficulty"] == "medium"
    since = got["2B"]["solved_at"]
    got2 = run(cf_mod.CodeforcesAdapter().fetch_solves("u", since=since))
    assert {s["problem_id"] for s in got2} == {"2B"}


def test_cf_status_failed_raises(monkeypatch):
    body = {"status": "FAILED", "comment": "handle not found"}
    patch_client(monkeypatch, [(lambda m, u, k: u.endswith("/user.status"), FakeResponse(200, body))])
    with pytest.raises(PlatformError):
        run(cf_mod.CodeforcesAdapter().fetch_solves("zzz"))


def test_cf_non_json_raises_for_solves(monkeypatch):
    patch_client(monkeypatch, [(lambda m, u, k: True, FakeResponse(200, None, text="<html>", json_error=True))])
    with pytest.raises(PlatformError, match="non-JSON"):
        run(cf_mod.CodeforcesAdapter().fetch_solves("u"))


def test_cf_transport_error_raises(monkeypatch):
    err = httpx.ConnectError("boom", request=httpx.Request("GET", "http://x"))
    patch_client(monkeypatch, [(lambda m, u, k: True, err)])
    with pytest.raises(PlatformError, match="ConnectError"):
        run(cf_mod.CodeforcesAdapter().fetch_solves("u"))


def test_cf_difficulty_buckets():
    assert [cf_mod.difficulty_for(r) for r in (800, 1199, 1200, 1799, 1800, 3000, None, "x")] == \
        ["easy", "easy", "medium", "medium", "hard", "hard", "medium", "medium"]


# ---------------- LeetCode ----------------

LC_PROFILE = {"data": {"matchedUser": {
    "username": "u",
    "submitStats": {"acSubmissionNum": [
        {"difficulty": "All", "count": 100, "submissions": 200},
        {"difficulty": "Easy", "count": 40, "submissions": 60},
        {"difficulty": "Medium", "count": 50, "submissions": 90},
        {"difficulty": "Hard", "count": 10, "submissions": 50}]},
    "profile": {"aboutMe": "hello noa-abc123"},
}, "userContestRanking": {"rating": 1875.6, "attendedContestsCount": 5}}}


def lc_route(payload):
    def match(method, url, kwargs):
        q = (kwargs.get("json") or {}).get("query", "")
        if payload == "profile":
            return "userProfile" in q
        if payload == "calendar_years":
            return "userCalendarYears" in q
        if payload == "calendar_plain":
            return "userCalendarPlain" in q
        if payload == "recent":
            return "userRecent" in q
        return False
    return match


def test_lc_profile_full(monkeypatch):
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(200, LC_PROFILE))])
    assert run(lc_mod.LeetCodeAdapter().fetch_profile("u")) == {
        "rating": 1875, "max_rating": None, "exists": True,
        "total_solved": 100, "easy": 40, "medium": 50, "hard": 10}
    assert run(lc_mod.LeetCodeAdapter().get_verification_field("u")) == "hello noa-abc123"


def test_lc_profile_unrated(monkeypatch):
    import copy
    body = copy.deepcopy(LC_PROFILE)
    body["data"]["userContestRanking"] = {"rating": 1500, "attendedContestsCount": 0}
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(200, body))])
    prof = run(lc_mod.LeetCodeAdapter().fetch_profile("u"))
    assert prof["rating"] is None and prof["exists"] is True


def test_lc_profile_missing_user(monkeypatch):
    body = {"data": {"matchedUser": None, "userContestRanking": None}}
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(200, body))])
    prof = run(lc_mod.LeetCodeAdapter().fetch_profile("u"))
    assert prof["exists"] is False and prof["total_solved"] == 0
    assert run(lc_mod.LeetCodeAdapter().get_verification_field("u")) == ""


def test_lc_graphql_not_found_message_means_missing(monkeypatch):
    body = {"errors": [{"message": "User not found"}]}
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(200, body))])
    assert run(lc_mod.LeetCodeAdapter().fetch_profile("u"))["exists"] is False


def test_lc_graphql_other_error_raises(monkeypatch):
    body = {"errors": [{"message": "Something unexpected happened"}]}
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(200, body))])
    with pytest.raises(PlatformError, match="GraphQL error"):
        run(lc_mod.LeetCodeAdapter().fetch_profile("u"))


def test_lc_malformed_payload_raises(monkeypatch):
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(200, {"data": None}))])
    with pytest.raises(PlatformError, match="unexpected response shape"):
        run(lc_mod.LeetCodeAdapter().fetch_profile("u"))


def test_lc_http_statuses(monkeypatch):
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(403, None, text="blocked"))])
    with pytest.raises(PlatformError, match="403"):
        run(lc_mod.LeetCodeAdapter().fetch_profile("u"))
    patch_client(monkeypatch, [(lc_route("profile"), FakeResponse(500, None, text="err"))])
    with pytest.raises(PlatformError, match="HTTP 500"):
        run(lc_mod.LeetCodeAdapter().fetch_profile("u"))


def test_lc_calendar_merged_sorted(monkeypatch):
    y1 = json.dumps({"1760227200": 3, "1760313600": 1})
    y2 = json.dumps({"1759276500": 2})
    body = {"data": {"matchedUser": {"y1": {"submissionCalendar": y1}, "y2": {"submissionCalendar": y2}}}}
    patch_client(monkeypatch, [(lc_route("calendar_years"), FakeResponse(200, body))])
    rows = run(lc_mod.LeetCodeAdapter().fetch_daily_counts("u"))
    assert rows == [("2025-09-30", 2), ("2025-10-12", 3), ("2025-10-13", 1)]


def test_lc_recents_mapped_and_filtered(monkeypatch):
    items = [{"titleSlug": "two-sum", "timestamp": 1760227200},
             {"titleSlug": None, "timestamp": 1760227200},
             {"titleSlug": "x", "timestamp": "bad"}]
    body = {"data": {"recentAcSubmissionList": items}}
    patch_client(monkeypatch, [(lc_route("recent"), FakeResponse(200, body))])
    got = run(lc_mod.LeetCodeAdapter().fetch_solves("u"))
    assert len(got) == 1 and got[0]["problem_id"] == "two-sum"
    assert got[0]["difficulty"] == "medium" and got[0]["solved_at"].startswith("2025-10-12")


def test_lc_stat_counts_fallback_without_all():
    assert lc_mod.parse_stat_counts([{"difficulty": "Easy", "count": 2}])["total"] == 2
    assert lc_mod.parse_stat_counts(None)["total"] == 0


# ---------------- AtCoder ----------------

AC_HISTORY = [{"NewRating": 1200}, {"NewRating": 1500}, {"NewRating": 1400}]
AC_HTML = '<tr><th class="no-break">Affiliation</th><td class="break-all">X <b>Lab</b></td></tr>'
AC_MODELS = {"abc001_a": {"difficulty": 100}, "abc001_b": {}, "p000": {"difficulty": 100}}

# Paged server emulation: honors from_second, 500 per page (mirrors the API).
AC_BIG_SUBS = []
for _i in range(50):
    for _j in range(10):
        AC_BIG_SUBS.append({
            "result": "WA" if _j == 0 else "AC",
            "problem_id": f"p{_i:03d}",
            "epoch_second": 1000 + 10 * _i + _j,
        })
AC_BIG_SUBS += [
    {"result": "AC", "problem_id": "final", "epoch_second": 2000},
    {"result": "AC", "problem_id": "final2", "epoch_second": 2001},
]


def ac_server_routes(subs, models):
    """Fake AtCoder Problems server: slices by from_second, 500 rows per page."""
    calls = {"subs": 0}

    def handler(method, url, kwargs):
        if "history/json" in url:
            return FakeResponse(200, AC_HISTORY)
        if "problem-models" in url:
            return FakeResponse(200, models)
        if "atcoder-api" in url:
            calls["subs"] += 1
            params = kwargs.get("params", {}) or {}
            start = int(params.get("from_second", 0))
            page = [s for s in subs if s["epoch_second"] >= start][:500]
            return FakeResponse(200, page)
        return FakeResponse(200, None, text=AC_HTML)

    return [(lambda m, u, k: True, handler)], calls


def ac_routes(history=None, profile="page", subs_pages=None, models=None, history_status=200):
    calls = {"subs": 0}

    def handler(method, url, kwargs):
        if "history/json" in url:
            return FakeResponse(history_status, history if history is not None else [])
        if "atcoder-api" in url:
            calls["subs"] += 1
            pages = subs_pages if subs_pages is not None else []
            idx = calls["subs"] - 1
            return FakeResponse(200, pages[idx] if idx < len(pages) else [])
        if "problem-models" in url:
            return FakeResponse(200, models if models is not None else {})
        if profile == "404":
            return FakeResponse(404, None, text="not found")
        return FakeResponse(200, None, text=profile if isinstance(profile, str) else "")

    def match(method, url, kwargs):
        return True

    return [(match, handler)], calls


def test_ac_profile_rated(monkeypatch):
    routes, _ = ac_routes(history=AC_HISTORY)
    patch_client(monkeypatch, routes)
    assert run(ac_mod.AtCoderAdapter().fetch_profile("u")) == {
        "rating": 1400, "max_rating": 1500, "exists": True}


def test_ac_profile_unrated_user_exists(monkeypatch):
    routes, _ = ac_routes(history=[], profile=AC_HTML)
    patch_client(monkeypatch, routes)
    assert run(ac_mod.AtCoderAdapter().fetch_profile("u")) == {
        "rating": None, "max_rating": None, "exists": True}


def test_ac_profile_missing_user(monkeypatch):
    routes, _ = ac_routes(history=[], profile="404")
    patch_client(monkeypatch, routes)
    assert run(ac_mod.AtCoderAdapter().fetch_profile("u")) == {
        "rating": None, "max_rating": None, "exists": False}
    assert run(ac_mod.AtCoderAdapter().get_verification_field("u")) == ""


def test_ac_verification_field(monkeypatch):
    routes, _ = ac_routes(history=AC_HISTORY, profile=AC_HTML)
    patch_client(monkeypatch, routes)
    assert run(ac_mod.AtCoderAdapter().get_verification_field("u")) == "X Lab"


def test_ac_verification_field_absent_raises(monkeypatch):
    routes, _ = ac_routes(history=AC_HISTORY, profile="<html><body>no table</body></html>")
    patch_client(monkeypatch, routes)
    with pytest.raises(PlatformError, match="Affiliation"):
        run(ac_mod.AtCoderAdapter().get_verification_field("u"))


def test_ac_solves_pagination_dedupe_difficulty(monkeypatch):
    routes, calls = ac_server_routes(AC_BIG_SUBS, AC_MODELS)
    patch_client(monkeypatch, routes)
    got = {s["problem_id"]: s for s in run(ac_mod.AtCoderAdapter().fetch_solves("u"))}
    assert calls["subs"] == 2  # full page -> continue; short page -> stop
    assert len(got) == 52 and "final" in got and "final2" in got
    assert got["p000"]["difficulty"] == "easy"  # estimate 100
    assert got["p000"]["solved_at"].startswith("1970-01-01T00:16:41")  # earliest AC kept
    assert got["p001"]["difficulty"] == "medium"  # no estimate


def test_ac_solves_since_filters(monkeypatch):
    routes, _ = ac_server_routes(AC_BIG_SUBS, AC_MODELS)
    patch_client(monkeypatch, routes)
    got = run(ac_mod.AtCoderAdapter().fetch_solves("u", since="1970-01-01T00:25:00+00:00"))
    assert {s["problem_id"] for s in got} == {"final", "final2"}


def test_ac_malformed_submissions_raise(monkeypatch):
    routes, _ = ac_routes(history=AC_HISTORY, subs_pages=[{"oops": 1}], models=AC_MODELS)
    patch_client(monkeypatch, routes)
    with pytest.raises(PlatformError, match="unexpected shape"):
        run(ac_mod.AtCoderAdapter().fetch_solves("u"))


def test_ac_malformed_models_raise(monkeypatch):
    import platforms.atcoder as ac
    ac._models = None
    ac._models_fetched_at = 0.0
    routes, _ = ac_routes(history=AC_HISTORY, subs_pages=[[]], models=["not", "a", "dict"])
    patch_client(monkeypatch, routes)
    with pytest.raises(PlatformError, match="difficulty data"):
        run(ac.AtCoderAdapter().fetch_solves("u"))


def test_ac_non_json_raises(monkeypatch):
    def full(method, url, kwargs):
        if "atcoder-api" in url:
            return FakeResponse(200, None, text="<html>", json_error=True)
        if "history/json" in url:
            return FakeResponse(200, AC_HISTORY)
        if "problem-models" in url:
            return FakeResponse(200, AC_MODELS)
        return FakeResponse(200, None, text=AC_HTML)
    patch_client(monkeypatch, [(lambda m, u, k: True, full)])
    with pytest.raises(PlatformError, match="non-JSON"):
        run(ac_mod.AtCoderAdapter().fetch_solves("u"))


def test_ac_server_error_raises(monkeypatch):
    patch_client(monkeypatch, [(lambda m, u, k: True, FakeResponse(500, None, text="err"))])
    with pytest.raises(PlatformError, match="HTTP 500"):
        run(ac_mod.AtCoderAdapter().fetch_profile("u"))


def test_ac_models_cached_24h(monkeypatch):
    import platforms.atcoder as ac
    calls = {"n": 0}

    def handler(method, url, kwargs):
        if "problem-models" in url:
            calls["n"] += 1
            return FakeResponse(200, AC_MODELS)
        if "atcoder-api" in url:
            return FakeResponse(200, [])
        return FakeResponse(200, AC_HISTORY)

    patch_client(monkeypatch, [(lambda m, u, k: True, handler)])
    run(ac.AtCoderAdapter().fetch_solves("u"))
    run(ac.AtCoderAdapter().fetch_solves("u"))
    assert calls["n"] == 1
