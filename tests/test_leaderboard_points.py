"""Points leaderboards end to end: lifetime/period points, ranks, podium, announce."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import db
import emojis
import points
import tzutil
from cogs.profile import LeaderboardView, Profile

U1, U2, U3, U4, U5 = 101, 102, 103, 104, 105


def _member(uid, name, bot=False):
    return SimpleNamespace(id=uid, bot=bot, display_name=name)


def _guild(*members):
    return SimpleNamespace(members=list(members))


def _monday():
    return tzutil.monday_00()


def _seed():
    mon = _monday().date().isoformat()
    prev = (_monday() - timedelta(days=7)).date().isoformat()
    today = tzutil.now_local().date().isoformat()
    yesterday = (tzutil.now_local().date() - timedelta(days=1)).isoformat()
    for uid in (U1, U2, U3, U4, U5):
        db.create_user(uid, f"user{uid}", active_path="swe")
    # U1: CF verified, full bucket history.
    db.create_pending_link(U1, "codeforces", "h1", "t")
    db.set_verified(U1, "codeforces")
    db.write_platform_stats(U1, "codeforces", 110, 65, 33, 12, 1500, 1600)
    db.upsert_snapshot(U1, "codeforces", prev, 100, 60, 30, 10)
    db.upsert_snapshot(U1, "codeforces", mon, 110, 65, 33, 12)
    db.upsert_daily(U1, "codeforces", [(yesterday, 2), (today, 3)])
    # Roadmap + DSA rows must never add points.
    db.mark_done(U1, "swe", "05.2")
    db.mark_learned(U1, "pat.sliding-window")
    # U2: LC verified, old NULL-bucket snapshots -> medium fallback.
    db.create_pending_link(U2, "leetcode", "h2", "t")
    db.set_verified(U2, "leetcode")
    db.write_platform_stats(U2, "leetcode", 52, 52, 0, 0, None, None)
    db.upsert_snapshot(U2, "leetcode", prev, 50)
    db.upsert_snapshot(U2, "leetcode", mon, 52)
    db.upsert_daily(U2, "leetcode", [(today, 1)])
    # U3: verified, remainder above buckets counts as medium.
    db.create_pending_link(U3, "codeforces", "h3", "t")
    db.set_verified(U3, "codeforces")
    db.write_platform_stats(U3, "codeforces", 10, 2, 2, 1, None, None)
    # U4: stats but UNVERIFIED link -> excluded everywhere.
    db.create_pending_link(U4, "codeforces", "h4", "t")
    db.write_platform_stats(U4, "codeforces", 1000, 1000, 0, 0, 3000, 3000)
    db.upsert_snapshot(U4, "codeforces", prev, 900)
    db.upsert_snapshot(U4, "codeforces", mon, 1000)
    # U5: registered, no links at all.
    return mon, prev


def test_lifetime_points_verified_only_with_remainder():
    _seed()
    got = db.lifetime_points_batch([U1, U2, U3, U4, U5])
    assert got[U1] == 65 + 99 + 120  # 284, Sentinel
    assert got[U2] == 52
    assert got[U3] == 2 + 6 + 10 + 5 * 3  # remainder 5 as medium -> 33
    assert got[U4] == 0  # unverified never counts
    assert got[U5] == 0
    assert db.lifetime_points(U1) == 284


def test_period_points_buckets_and_null_fallback():
    _seed()
    mon = _monday().date().isoformat()
    got = db.period_points_batch([U1, U2, U3, U4], mon)
    assert got[U1] == 5 + 9 + 20  # bucket deltas: 5e/3m/2h
    assert got[U2] == (52 - 50) * 3  # NULL baseline -> total delta as medium
    assert got[U3] == 0  # no snapshots in scope
    assert got[U4] == 0  # unverified excluded


def test_sync_writes_snapshot_buckets():
    _seed()

    class _FakeAdapter:
        NATIVE_DAILY = True
        AUTHORITATIVE_PROFILE_STATS = True

        async def fetch_profile(self, handle):
            return {"exists": True, "rating": 1500, "max_rating": 1600,
                    "total_solved": 10, "easy": 4, "medium": 3, "hard": 1}

        async def fetch_solves(self, handle, since=None):
            return []

        async def fetch_daily_counts(self, handle):
            return [("2026-10-12", 2)]

    from cogs.platforms import sync_one

    db.create_pending_link(U1, "leetcode", "h1lc", "t")
    db.set_verified(U1, "leetcode")
    asyncio.run(sync_one(_FakeAdapter(), U1, "leetcode", "h1lc"))
    rows = [r for r in db.get_snapshots([U1]) if r["platform"] == "leetcode"]
    assert rows, "sync must write a snapshot row"
    latest = sorted(rows, key=lambda r: r["date"])[-1]
    assert (latest["total_solved"], latest["easy"], latest["medium"], latest["hard"]) == (10, 4, 3, 1)


def _week_guild():
    return _guild(_member(U1, "User101"), _member(U2, "User102"), _member(U3, "User103"),
                  _member(U4, "User104"), _member(U5, "User105"), _member(999, "Bot", True))


def test_leaderboard_week_ranks_by_points():
    _seed()
    view = LeaderboardView(U1, ">", _week_guild())
    assert view.period == "week"
    embed = view.render()
    desc = embed.description
    assert "This week" in embed.title and "resets in" in embed.title
    assert desc.index("User101") < desc.index("User102") < desc.index("User103")
    assert "User104" not in desc and "User105" not in desc and "Bot" not in desc
    assert "34 pts" in desc and "10 solved" in desc
    assert "<:sentinel:1559492393852473424>" in desc  # U1 lifetime Sentinel
    assert "<:acolyte:1559491379913039922>" in desc  # U2 lifetime Acolyte
    assert "<:cf:1559216598038876250>" in desc and "<:lc:1559216834090238072>" in desc
    assert "<:streak:1559216255498452992>" in desc
    assert "Easy 1 · Medium 3 · Hard 10" in embed.footer.text


def test_leaderboard_all_time_uses_lifetime_points():
    _seed()
    view = LeaderboardView(U1, ">", _week_guild())
    view.period = "all"
    desc = view.render().description
    assert desc.index("User101") < desc.index("User102") < desc.index("User103")
    assert "284 pts" in desc and "52 pts" in desc and "33 pts" in desc


def test_leaderboard_fallback_mode():
    _seed()
    import emojis as _em
    old = _em.USE_CUSTOM_EMOJIS
    _em.USE_CUSTOM_EMOJIS = False
    try:
        desc = LeaderboardView(U1, ">", _week_guild()).render().description
    finally:
        _em.USE_CUSTOM_EMOJIS = old
    assert "[CF]" in desc and "[LC]" in desc
    assert "○" in desc  # acolyte fallback
    assert "streak" in desc
    assert "<:" not in desc


def _sent_embed(ctx):
    args, kwargs = ctx.send.await_args
    return args[0] if args else kwargs["embed"]


def test_podium_ranks_by_points():
    _seed()
    cog = Profile(SimpleNamespace())
    ctx = SimpleNamespace(
        author=SimpleNamespace(id=U1, bot=False),
        guild=_week_guild(), prefix=">", send=AsyncMock(),
    )
    asyncio.run(cog.podium.callback(cog, ctx))
    desc = _sent_embed(ctx).description
    assert desc.index("User101") < desc.index("User102")
    assert "34 pts" in desc and "10 solved" in desc
    assert "🥇" in desc and "🥈" in desc


def test_ranks_command_lists_tiers_and_progress():
    _seed()
    cog = Profile(SimpleNamespace())
    ctx = SimpleNamespace(
        author=SimpleNamespace(id=U1, bot=False),
        prefix=">", send=AsyncMock(),
    )
    asyncio.run(cog.ranks.callback(cog, ctx))
    embed = _sent_embed(ctx)
    assert embed.title == "Points ranks"
    for name, threshold in [("Acolyte", "0+"), ("Sentinel", "250+"), ("Warden", "600+"),
                            ("Centurion", "1200+"), ("Archon", "2200+"), ("Titan", "3500+")]:
        assert name in embed.description and threshold in embed.description
    weights = next(f for f in embed.fields if f.name == "Weights")
    assert weights.value == "Easy 1 · Medium 3 · Hard 10"
    you = next(f for f in embed.fields if f.name == "You")
    assert "Sentinel" in you.value and "284 pts" in you.value and "316 more" in you.value


def test_ranks_command_unregistered_has_no_progress_field():
    cog = Profile(SimpleNamespace())
    ctx = SimpleNamespace(
        author=SimpleNamespace(id=777001, bot=False),
        prefix=">", send=AsyncMock(),
    )
    asyncio.run(cog.ranks.callback(cog, ctx))
    embed = _sent_embed(ctx)
    assert [f.name for f in embed.fields] == ["Weights"]


def test_announce_reuses_points_scoring():
    _seed()
    from cogs.announce import build_podium_embed, compute_week_board

    label, rows = compute_week_board(_week_guild(), tzutil.last_week_range()[0])
    assert [r["name"] for r in rows[:2]] == ["User101", "User102"]
    assert rows[0]["points"] == 34
    embed = build_podium_embed(label, rows)
    assert "34 pts" in embed.description and "10 solved" in embed.description
    assert "keep grinding" in embed.footer.text
    assert build_podium_embed(label, [{"name": "Nobody", "points": 0, "items": 0, "streak": 0}]) is None
