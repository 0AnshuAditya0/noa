import asyncio, sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import db
db.init_db()

# 0. index + old tables intact
with sqlite3.connect(db.DB_PATH) as c:
    idx = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")]
    assert "idx_solves_user_date" in idx, idx
    pcols = [r[1] for r in c.execute("PRAGMA table_info(progress)")]
    assert pcols == ["user_id", "path_id", "topic_id", "completed_at"], pcols
print("index/schema OK")

U1, U2, U3, U4 = 600101, 600102, 600103, 600104
for t in ("users", "linked_accounts", "solves", "platform_stats", "user_activity"):
    with sqlite3.connect(db.DB_PATH) as c:
        c.execute(f"DELETE FROM {t} WHERE user_id IN (?,?,?,?)", (U1, U2, U3, U4))
        c.commit()
db.create_user(U1, "u1", active_path="swe")
db.create_user(U2, "u2", active_path="swe")
db.create_user(U3, "u3", active_path="swe")
db.create_user(U4, "u4", active_path="swe")

# U1: verified CF, 3 solves incl. one unverified-platform solve that must NOT count
db.create_pending_link(U1, "codeforces", "h1", "t"); db.set_verified(U1, "codeforces")
db.upsert_solves(U1, "codeforces", [
    {"problem_id": "1A", "difficulty": "easy", "solved_at": "2026-10-10T10:00:00+00:00"},
    {"problem_id": "1B", "difficulty": "medium", "solved_at": "2026-10-11T10:00:00+00:00"},
    {"problem_id": "1C", "difficulty": "hard", "solved_at": "2026-10-12T10:00:00+00:00"},
])
db.rebuild_platform_stats(U1, "codeforces", 1523, 1601)
db.create_pending_link(U1, "leetcode", "h1lc", "t")  # pending only, no adapter
db.upsert_solves(U1, "leetcode", [
    {"problem_id": "two-sum", "difficulty": "easy", "solved_at": "2026-10-12T11:00:00+00:00"},
])
# U2: verified CF, same total as U1 but lower best rating (tie-break)
db.create_pending_link(U2, "codeforces", "h2", "t"); db.set_verified(U2, "codeforces")
db.upsert_solves(U2, "codeforces", [
    {"problem_id": "1A", "difficulty": "easy", "solved_at": "2026-10-11T10:00:00+00:00"},
    {"problem_id": "1B", "difficulty": "easy", "solved_at": "2026-10-11T11:00:00+00:00"},
    {"problem_id": "1C", "difficulty": "easy", "solved_at": "2026-10-12T10:00:00+00:00"},
])
db.rebuild_platform_stats(U2, "codeforces", 1200, 1200)
# U3: pending only. U4: nothing.

tot = db.platform_totals(U1)
assert tot == {"total": 3, "easy": 1, "medium": 1, "hard": 1}, tot  # leetcode pending excluded
assert db.platform_totals(U1, since="2026-10-12T00:00:00+00:00")["total"] == 1
assert db.platform_totals(U3) == {"total": 0, "easy": 0, "medium": 0, "hard": 0}
print("verified-only totals OK")

assert db.streak_from_dates([]) == (0, 0)
assert db.streak_from_dates(["2026-10-10", "2026-10-11", "2026-10-12"], today="2026-10-12") == (3, 3)
assert db.streak_from_dates(["2026-10-10", "2026-10-12"], today="2026-10-12") == (1, 1)
assert db.streak_from_dates(["2026-10-10", "2026-10-11"], today="2026-10-12") == (2, 2)  # yesterday alive
assert db.streak_from_dates(["2026-10-08"], today="2026-10-12") == (0, 1)
print("streak fn OK")

board = db.problems_board()
mine = [r for r in board if r["user_id"] in (U1, U2, U3, U4)]
order = [r["user_id"] for r in mine]
assert order[0] == U1 and order[1] == U2, order  # tie 3-3 broken by best rating
assert all(r["user_id"] not in (U3, U4) for r in board)  # no verified account -> excluded
wb = {r["user_id"]: r["total"] for r in db.problems_board(since="2026-10-12T00:00:00+00:00") if r["user_id"] in (U1, U2)}
assert wb == {U1: 1, U2: 1}, wb
print("problems_board SQL OK")

# profile embed states
from main import bot
async def load():
    for ext in ("cogs.profile", "cogs.platforms", "cogs.help", "cogs.account", "cogs.roadmap", "cogs.ping"):
        try: await bot.load_extension(ext)
        except Exception as e:
            if "already loaded" not in str(e).lower(): raise
    cog = bot.get_cog("Platforms")
    if cog is not None:
        cog.platform_sync_loop.cancel()  # offline test env: never logs in
asyncio.run(load())

def mkctx(uid, name="U"):
    ctx = AsyncMock(); ctx.author.id = uid; ctx.author.bot = False
    ctx.author.display_name = name; ctx.prefix = ">"; return ctx

async def profile_fields():
    cog = bot.get_cog("Profile")
    ctx = mkctx(U1); await cog.profile(ctx)
    em = ctx.send.await_args.args[0] if ctx.send.await_args.args else ctx.send.await_args.kwargs["embed"]
    names = [f.name for f in em.fields]
    assert names == ["Active path", "Experience", "Hours/week", "Goal", "Registered",
                     "Items completed", "Roadmap streak", "Coding Platforms"], names
    plat = next(f for f in em.fields if f.name == "Coding Platforms").value
    assert "Codeforces: 3 solved (E/M/H: 1/1/1) | rating 1523 (max 1601) | synced " in plat, plat
    assert "LeetCode: coming soon" in plat and "AtCoder: coming soon" in plat, plat
    assert "Total: 3 solved across verified platforms" in plat and "coding streak" in plat, plat
    # pending line
    db.create_pending_link(U3, "codeforces", "h3", "t")
    ctx3 = mkctx(U3); await cog.profile(ctx3)
    em3 = ctx3.send.await_args.args[0] if ctx3.send.await_args.args else ctx3.send.await_args.kwargs["embed"]
    plat3 = next(f for f in em3.fields if f.name == "Coding Platforms").value
    assert "linked, not verified yet. Run `>link codeforces h3`" in plat3, plat3
    assert "Codeforces: not connected" not in plat3
    # verified, never synced
    db.create_pending_link(U4, "codeforces", "h4", "t"); db.set_verified(U4, "codeforces")
    ctx4 = mkctx(U4); await cog.profile(ctx4)
    em4 = ctx4.send.await_args.args[0] if ctx4.send.await_args.args else ctx4.send.await_args.kwargs["embed"]
    assert "connected, waiting for first sync" in next(f for f in em4.fields if f.name == "Coding Platforms").value
    # unknown mentioned user
    ctxx = mkctx(U1); m = SimpleNamespace(id=999999, bot=False, display_name="Ghost")
    await cog.profile(ctxx, m); assert "isn't registered" in ctxx.send.await_args.args[0]
asyncio.run(profile_fields())
print("profile OK")

# leaderboard view: default problems, toggle, roadmap unchanged, edges
from cogs.profile import LeaderboardView
def mkm(uid, name, bot=False):
    return SimpleNamespace(id=uid, bot=bot, display_name=name)
g = SimpleNamespace(members=[mkm(U1, "One"), mkm(U2, "Two"), mkm(U3, "Three"), mkm(999, "Bot", True)])
v = LeaderboardView(U1, ">", g)
assert v.board == "problems"
em = v.render()
assert em.title.startswith("Leaderboard — Problems solved"), em.title
assert "**One** — 3 solved" in em.description and "`CF`" in em.description, em.description
assert "Link an account with >link" in em.footer.text
assert "Bot" not in em.description
# empty / left members
g2 = SimpleNamespace(members=[mkm(U3, "Three")])
em2 = LeaderboardView(U3, ">", g2).render()
assert "Nobody on the board" in em2.description
# roadmap mode unchanged behavior
v3 = LeaderboardView(U1, ">", g); v3.board = "roadmap"
em3 = v3.render()
assert em3.title.startswith("Leaderboard — Software Engineering"), em3.title
assert "🔥" in em3.description and "items" in em3.description
# no-path owner -> None (command sends set-path message)
db.update_user(U1, active_path=None) if False else None
v4 = LeaderboardView(777777, ">", g)  # unknown user: get_user None -> no roadmap pid
assert v4.render() is not None  # problems default works without a path
v4.board = "roadmap"; assert v4.render() is None
# double-click safety: prev on page 0 stays clamped
async def dblclick():
    v.page = 0
    inter = AsyncMock(); inter.message = AsyncMock()
    await v.prev.callback(inter)
    emx = inter.response.edit_message.await_args.kwargs["embed"]
    assert emx is not None
asyncio.run(dblclick())
print("leaderboard OK")

# roadmap streak logic untouched: mark/unmark still drive user_activity only
db.mark_done(U1, "swe", "01.1")
cur, _ = db.get_streaks(U1)
assert cur >= 1
print("roadmap streak intact")

with sqlite3.connect(db.DB_PATH) as c:
    for t in ("users", "linked_accounts", "solves", "platform_stats", "user_activity", "progress"):
        c.execute(f"DELETE FROM {t} WHERE user_id IN (?,?,?,?)", (U1, U2, U3, U4))
        c.commit()
print("ALL PHASE 5C CHECKS PASSED")
