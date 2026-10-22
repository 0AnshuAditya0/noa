import time as _time
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

import db
import emojis
import points
import roadmaps
import tzutil
from cogs.roadmap import _bar
from platforms import ADAPTERS

PAGE_SIZE = 10
BOARD_TIMEOUT = 180
PROFILE_CACHE_TTL = 60

# Fixed display list. Platforms without an adapter yet render as "coming soon";
# registering a new adapter flips its line live with no profile code change.
KNOWN_PLATFORMS: list[tuple[str, str]] = [
    ("codeforces", "Codeforces"),
    ("leetcode", "LeetCode"),
    ("atcoder", "AtCoder"),
]
PERIODS = ("week", "lastweek", "month", "year", "all")
BARS = "▁▂▃▄▅▆▇█"

_PROFILE_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}


def _rel_time(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "unknown"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _platform_line(user_id: int, platform: str, label: str, prefix: str, handle_hint: str = "") -> str:
    link = db.get_link(user_id, platform)
    if platform not in ADAPTERS:
        return f"{label}: coming soon"
    if link and link.get("verified"):
        stats = db.get_platform_stats(user_id, platform)
        if stats is None:
            return f"{label}: connected, waiting for first sync"
        return (
            f"{label}: {stats['total_solved']} solved "
            f"(E/M/H: {stats['easy']}/{stats['medium']}/{stats['hard']}) | "
            f"rating {stats['rating'] or 'unrated'} (max {stats['max_rating'] or '—'}) | "
            f"synced {_rel_time(stats['last_synced'])}"
        )
    if link:
        handle = link.get("handle", "")
        return f"{label}: linked, not verified yet. Run `{prefix}link {platform} {handle}`"
    return f"{label}: not connected"


def _progress_line(user_id: int, path_id: str, data: dict) -> tuple[int, int, int]:
    total = roadmaps.total_count(data)
    done_n = len(db.get_done_ids(user_id, path_id))
    pct = round(done_n / total * 100) if total else 0
    return done_n, total, pct


def _period_window(period: str) -> tuple[str, str | None, str]:
    """Return (start_date_str, prev_start_date_str_or_None, title_suffix) for problems boards."""
    if period == "lastweek":
        start, _ = tzutil.last_week_range()
        prev, _ = tzutil.week_before_last_range()
        end = start + timedelta(days=6)
        return start.date().isoformat(), prev.date().isoformat(), f"Last week · {tzutil.fmt_day(start)} – {tzutil.fmt_day(end)}"
    if period == "month":
        start, _ = tzutil.this_month_range()
        return start.date().isoformat(), None, tzutil.fmt_month(start)
    if period == "year":
        start, _ = tzutil.this_year_range()
        return start.date().isoformat(), None, str(start.year)
    if period == "all":
        return "", None, "All time"
    start, _ = tzutil.this_week_range()
    prev, _ = tzutil.last_week_range()
    return start.date().isoformat(), prev.date().isoformat(), f"This week · resets in {tzutil.countdown_to_monday()}"


def _order_uids(uids: list[int], points: dict[int, int], counts: dict[int, int],
                info: dict[int, tuple[int | None, str]]) -> list[int]:
    """Points desc, solved desc, best rating desc (NULLs last), registered_at asc."""

    def key(uid: int):
        best, reg = info.get(uid, (None, ""))
        return (-points.get(uid, 0), -counts.get(uid, 0), best is None, -(best or 0), reg)

    return sorted(uids, key=key)


def ranked_period_rows(member_ids: set[int], names: dict[int, str],
                       start_date: str, prev_start: str | None = None) -> list[dict]:
    """Ordered Problems-board rows for a snapshot window, shared by the
    leaderboard, the podium command and the announce job.

    Ranked by period points desc, solved desc, best rating desc, registered
    asc. Each row: uid/name/points/items/streak/tags/rank_emoji/move.
    """
    verified = {r["user_id"] for r in db.list_verified_links() if r["user_id"] in member_ids}
    uids = sorted(verified)
    pts = db.period_points_batch(uids, start_date)
    counts = db.window_scores(uids, start_date)
    info = db.user_rank_info(uids)
    ordered = _order_uids(uids, pts, counts, info)
    prev_ranks: dict[int, int] = {}
    if prev_start is not None:
        prev_pts = db.period_points_batch(uids, prev_start)
        prev_counts = db.window_scores(uids, prev_start)
        prev_ranks = {uid: i + 1 for i, uid in enumerate(_order_uids(uids, prev_pts, prev_counts, info))}
    dates = db.solve_dates_batch(uids)
    tags = db.verified_platforms_batch(uids, start_date)
    lifetime = db.lifetime_points_batch(uids)
    rows = []
    for rank, uid in enumerate(ordered, start=1):
        cur, _ = db.streak_from_dates(dates.get(uid, []))
        rows.append({
            "uid": uid,
            "name": names.get(uid, str(uid)),
            "points": pts.get(uid, 0),
            "items": counts.get(uid, 0),
            "streak": cur,
            "tags": " ".join(emojis.platform_emoji(p) for p in tags.get(uid, [])) or "—",
            "rank_emoji": emojis.rank_emoji(points.rank_for_points(lifetime.get(uid, 0))),
            "move": _move_arrow(rank, prev_ranks.get(uid)),
        })
    return rows


def _move_arrow(rank: int, prev_rank: int | None) -> str | None:
    if prev_rank is None:
        return "•"
    delta = prev_rank - rank
    if delta > 0:
        return f"▲{delta}"
    if delta < 0:
        return f"▼{-delta}"
    return "="


def _spark(values: list[int]) -> str:
    if not values:
        return "—"
    peak = max(values)
    if peak <= 0:
        return "▁" * len(values)
    return "".join(BARS[min(7, round(v / peak * 7))] for v in values)


def _badges(total: int, clong: int, week_rank: int | None) -> str:
    joined = " · ".join(_badge_list(total, clong, week_rank))
    return joined if joined else "No badges yet — solve to earn!"


def _badge_list(total: int, clong: int, week_rank: int | None) -> list[str]:
    badges: list[str] = []
    if clong >= 100:
        badges.append(f"{emojis.STREAK} Century Streak (100d)")
    elif clong >= 30:
        badges.append(f"{emojis.STREAK} Month Streak (30d)")
    elif clong >= 7:
        badges.append(f"{emojis.STREAK} Week Streak (7d)")
    if total >= 1000:
        badges.append("🏆 1000 Solved")
    elif total >= 500:
        badges.append("⭐ 500 Solved")
    elif total >= 100:
        badges.append("💯 100 Solved")
    if week_rank is not None and week_rank <= 3:
        badges.append(f"{emojis.podium_medal(2)} Top 3 This Week")
    return badges


def progress_bar(done: int, total: int, width: int = 10) -> str:
    """Reusable mini bar, e.g. progress_bar(3, 4) -> '███████░░░'."""
    if total <= 0:
        return "░" * width
    filled = min(width, max(0, round(done / total * width)))
    return "█" * filled + "░" * (width - filled)


def tier_color(week_rank: int | None) -> discord.Color:
    """Gold for top 3, silver for top 10, blue otherwise, grey when unranked."""
    if week_rank is None:
        return discord.Color.dark_grey()
    if week_rank <= 3:
        return discord.Color.gold()
    if week_rank <= 10:
        return discord.Color.light_grey()
    return discord.Color.blurple()


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()

    @commands.command(name="profile")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def profile(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Tabbed learning profile: Overview / Platforms / Roadmap / DSA."""
        target = member or ctx.author
        if target.bot:
            await ctx.send("Bots don't have profiles.")
            return
        if db.get_user(target.id) is None:
            await ctx.send(f"{target.display_name} isn't registered yet.")
            return
        view = ProfileView(ctx.author.id, ctx.prefix, ctx.guild, target.id, target.display_name,
                           avatar_url=target.display_avatar.url)
        msg = await ctx.send(embed=view.render(), view=view)
        view.message = msg

    @commands.group(name="leaderboard", invoke_without_command=True)
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def leaderboard(self, ctx: commands.Context) -> None:
        """Server leaderboard: Problems solved (default) or Roadmap. 10 per page."""
        if ctx.guild is None:
            await ctx.send("Run this command in a server, not DMs.")
            return
        view = LeaderboardView(ctx.author.id, ctx.prefix, ctx.guild)
        embed = view.render()
        if embed is None:
            await ctx.send(f"Set your path first (use `{ctx.prefix}paths`).")
            return
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @leaderboard.command(name="podium")
    async def podium(self, ctx: commands.Context) -> None:
        """Show last week's top 3 by points."""
        if ctx.guild is None:
            await ctx.send("Run this command in a server, not DMs.")
            return
        member_ids = {m.id for m in ctx.guild.members if not m.bot}
        names = {m.id: m.display_name for m in ctx.guild.members}
        start, _ = tzutil.last_week_range()
        rows = ranked_period_rows(member_ids, names, start.date().isoformat())
        top = [r for r in rows if r["points"] > 0][:3]
        embed = discord.Embed(title="Podium — last week", color=discord.Color.gold())
        if not top:
            embed.description = "No podium yet — nobody scored last week."
        else:
            embed.description = "\n".join(
                f"{emojis.podium_medal(i)} **{r['name']}** — {r['points']} pts · {r['items']} solved"
                for i, r in enumerate(top)
            )[:4000]
        await ctx.send(embed=embed)

    @commands.command(name="ranks")
    async def ranks(self, ctx: commands.Context) -> None:
        """Show all points ranks, thresholds, weights and your progress."""
        embed = discord.Embed(title="Points ranks", color=discord.Color.gold())
        embed.description = "\n".join(
            f"{emojis.rank_emoji(r['name'])} **{r['name']}** — {r['threshold']}+ pts"
            for r in points.RANKS
        )[:4000]
        embed.add_field(
            name="Weights",
            value=f"Easy {points.EASY_POINTS} · Medium {points.MEDIUM_POINTS} · Hard {points.HARD_POINTS}",
            inline=False,
        )
        user = db.get_user(ctx.author.id)
        if user is not None:
            total = db.lifetime_points(ctx.author.id)
            cur, nxt, _into, need = points.progress_to_next(total)
            if nxt is None:
                progress = f"{emojis.rank_emoji(cur)} **{cur}** — {total} pts · max rank!"
            else:
                progress = (
                    f"{emojis.rank_emoji(cur)} **{cur}** — {total} pts · "
                    f"{need} more to {emojis.rank_emoji(nxt)} **{nxt}**"
                )
            embed.add_field(name="You", value=progress[:1024], inline=False)
        await ctx.send(embed=embed)


def _compute_profile_data(guild: discord.Guild | None, target_id: int, prefix: str) -> dict:
    """All numbers for every tab. Callers go through the 60s cache below."""
    import dsa_library as _dsa

    user = db.get_user(target_id) or {}
    paths = roadmaps.load_roadmaps()
    pid = user.get("active_path")
    data = paths.get(pid, {}) if pid else {}
    if pid and data:
        done_n, total, pct = _progress_line(target_id, pid, data)
        path_title = str(data.get("title", pid))
    else:
        path_title, done_n, total, pct = str(pid or "—"), 0, 0, 0
    rcur, rlong = db.get_streaks(target_id)
    totals = db.platform_totals(target_id)
    ccur, clong = db.coding_streak(target_id)
    plat_lines = [_platform_line(target_id, plat, label, prefix) for plat, label in KNOWN_PLATFORMS]
    plat_lines.append(
        f"Total: {totals['total']} solved across verified platforms | "
        f"coding streak {emojis.STREAK} {ccur}d (longest {clong}d)"
    )
    lib = _dsa.load_dsa()
    learned = db.learned_ids(target_id)
    dsa_total = sum(len(_dsa.entries_of(d)) for d in lib.values())
    dsa_cats = []
    for category, cat_data in lib.items():
        entries = _dsa.entries_of(cat_data)
        got = sum(1 for e in entries if str(e.get("id")) in learned)
        dsa_cats.append(f"{cat_data.get('title', category)}: {got}/{len(entries)}")
    week_start, _ = tzutil.this_week_range()
    week_scores: dict[int, int] = {}
    week_rank: int | None = None
    if guild is not None:
        member_ids = {m.id for m in guild.members if not m.bot}
        verified = {r["user_id"] for r in db.list_verified_links() if r["user_id"] in member_ids}
        if target_id in verified:
            uids = sorted(verified)
            start_date = week_start.date().isoformat()
            pts = db.period_points_batch(uids, start_date)
            counts = db.window_scores(uids, start_date)
            ordered = _order_uids(uids, pts, counts, db.user_rank_info(uids))
            week_rank = ordered.index(target_id) + 1
            week_scores = counts
    week_solved = week_scores.get(target_id, 0)
    week_days = [(week_start + timedelta(days=i)).date().isoformat() for i in range(7)]
    sums = db.daily_sums(target_id, week_days[0])
    spark_vals = [sums.get(day, 0) for day in week_days]
    return {
        "user": user, "path_title": path_title, "done_n": done_n, "total": total, "pct": pct,
        "rcur": rcur, "rlong": rlong, "totals": totals, "ccur": ccur, "clong": clong,
        "plat_lines": plat_lines, "dsa_total": dsa_total, "dsa_learned": len(learned),
        "dsa_cats": dsa_cats, "week_solved": week_solved, "week_rank": week_rank,
        "spark": _spark(spark_vals), "spark_total": sum(spark_vals),
        "badges": _badges(totals["total"], clong, week_rank),
    }


def _cached_profile_data(guild: discord.Guild | None, target_id: int, prefix: str) -> dict:
    key = (getattr(guild, "id", 0) or 0, target_id)
    now = _time.monotonic()
    hit = _PROFILE_CACHE.get(key)
    if hit and now - hit[0] < PROFILE_CACHE_TTL:
        return hit[1]
    data = _compute_profile_data(guild, target_id, prefix)
    _PROFILE_CACHE[key] = (now, data)
    return data


class ProfileView(discord.ui.View):
    """Tabbed profile: Overview / Platforms / Roadmap / DSA. Invoker-only."""

    TABS = ("overview", "platforms", "roadmap", "dsa")
    TAB_LABELS = {"overview": "Overview", "platforms": "Platforms", "roadmap": "Roadmap", "dsa": "DSA"}

    def __init__(self, owner_id: int, prefix: str, guild: discord.Guild | None,
                 target_id: int, display_name: str, tab: str = "overview",
                 avatar_url: str | None = None) -> None:
        super().__init__(timeout=BOARD_TIMEOUT)
        self.owner_id = owner_id
        self.prefix = prefix
        self.guild = guild
        self.target_id = target_id
        self.display_name = display_name
        self.tab = tab if tab in self.TABS else "overview"
        self.avatar_url = avatar_url
        self.message: discord.Message | None = None
        for key in self.TABS:
            btn = discord.ui.Button(
                label=self.TAB_LABELS[key], style=discord.ButtonStyle.secondary,
                disabled=(key == self.tab),
            )
            btn.callback = self._make_tab_cb(key)
            self.add_item(btn)

    def _make_tab_cb(self, key: str):
        async def go(interaction: discord.Interaction) -> None:
            view = ProfileView(self.owner_id, self.prefix, self.guild,
                               self.target_id, self.display_name, key,
                               avatar_url=self.avatar_url)
            await interaction.response.edit_message(embed=view.render(), view=view)
            view.message = interaction.message
        return go

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def render(self) -> discord.Embed:
        d = _cached_profile_data(self.guild, self.target_id, self.prefix)
        user = d["user"]
        embed = discord.Embed(
            title=f"{self.display_name}'s profile — {self.TAB_LABELS[self.tab]}",
            color=tier_color(d["week_rank"]),
        )
        if self.avatar_url:
            embed.set_thumbnail(url=self.avatar_url)
        if self.tab == "overview":
            rank = f"#{d['week_rank']} this week" if d["week_rank"] else "unranked"
            embed.description = f"**{self.display_name}**\n{user.get('bio') or 'No bio yet.'}"
            embed.add_field(
                name="This week",
                value=f"{rank} · {d['week_solved']} solved",
                inline=False,
            )
            embed.add_field(
                name="Coding streak",
                value=f"{emojis.STREAK} {d['ccur']}d (longest {d['clong']}d)",
                inline=False,
            )
            embed.add_field(
                name="Roadmap",
                value=f"{progress_bar(d['done_n'], d['total'])} {d['pct']}% ({d['done_n']}/{d['total']})",
                inline=False,
            )
            embed.add_field(
                name="DSA",
                value=f"{progress_bar(d['dsa_learned'], d['dsa_total'])} {d['dsa_learned']}/{d['dsa_total']}",
                inline=False,
            )
            top_badges = _badge_list(d["totals"]["total"], d["clong"], d["week_rank"])[:3]
            embed.add_field(
                name="Badges",
                value=" · ".join(top_badges) if top_badges else "No badges yet — solve to earn!",
                inline=False,
            )
        elif self.tab == "platforms":
            embed.add_field(name="Coding Platforms", value="\n".join(d["plat_lines"])[:1024], inline=False)
            embed.add_field(name="All-time solved", value=str(d["totals"]["total"]), inline=True)
            embed.add_field(name="Last 7 days", value=f"`{d['spark']}` ({d['spark_total']} solved)", inline=False)
        elif self.tab == "roadmap":
            embed.add_field(
                name="Active path",
                value=f"{d['path_title']}\n`{_bar(d['pct'])}` {d['pct']}% ({d['done_n']}/{d['total']})",
                inline=False,
            )
            embed.add_field(name="Experience", value=str(user.get("experience_level") or "—"), inline=True)
            embed.add_field(name="Hours/week", value=str(user.get("weekly_hours") or "—"), inline=True)
            embed.add_field(name="Goal", value=str(user.get("goal") or "—"), inline=False)
            embed.add_field(name="Registered", value=str(user.get("registered_at", "—"))[:10], inline=True)
            embed.add_field(name="Items completed", value=str(d["done_n"]), inline=True)
            embed.add_field(name="Roadmap streak", value=f"{emojis.STREAK} {d['rcur']}d current · {d['rlong']}d longest", inline=False)
        else:
            embed.add_field(
                name="DSA library",
                value=f"{d['dsa_learned']}/{d['dsa_total']} learned",
                inline=False,
            )
            for i in range(0, len(d["dsa_cats"]), 2):
                embed.add_field(name="Categories", value="\n".join(d["dsa_cats"][i:i + 2])[:1024], inline=False)
        return embed


class LeaderboardView(discord.ui.View):
    """Problems board with periods (default This week) + unchanged Roadmap board. Invoker-only."""

    def __init__(self, owner_id: int, prefix: str, guild: discord.Guild) -> None:
        super().__init__(timeout=BOARD_TIMEOUT)
        self.owner_id = owner_id
        self.prefix = prefix
        self.guild = guild
        self.page = 0
        self.mode: str = "all"  # legacy roadmap toggle: all/week (roadmap board only)
        self.period: str = "week"  # problems board: week/lastweek/month/year/all
        self.board: str = "problems"  # or "roadmap"
        self.message: discord.Message | None = None
        # Roadmap board context (resolved lazily so Problems works without a path).
        paths = roadmaps.load_roadmaps()
        self._roadmap_pid: str | None = None
        self._roadmap_title: str = ""
        user = db.get_user(owner_id)
        pid = (user or {}).get("active_path")
        if pid and pid in paths:
            self._roadmap_pid = pid
            self._roadmap_title = str(paths[pid].get("title", pid))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _member_maps(self) -> tuple[set[int], dict[int, str]]:
        members = [m for m in self.guild.members if not m.bot]
        return {m.id for m in members}, {m.id: m.display_name for m in members}

    def _roadmap_rows(self) -> list[dict]:
        """Existing roadmap behavior, unchanged: zeros included at the bottom."""
        import roadmaps as _rm

        assert self._roadmap_pid is not None
        paths = _rm.load_roadmaps()
        data = paths.get(self._roadmap_pid, {})
        total = _rm.total_count(data)
        member_ids, names = self._member_maps()
        since = db.week_cutoff_iso() if self.mode == "week" else None
        counts = db.board_counts(self._roadmap_pid, since)
        rows: list[dict] = []
        for u in db.list_users():
            uid = u["user_id"]
            if uid not in member_ids:
                continue  # left the server (or not cached) — skip
            cnt, last = counts.get(uid, (0, None))
            current, _ = db.get_streaks(uid)
            pct = round(cnt / total * 100) if total else 0
            rows.append(
                {"uid": uid, "name": names.get(uid, u.get("username", str(uid))), "items": cnt, "last": last or "",
                 "pct": pct, "streak": current}
            )
        # Rank: items desc, tie-break by who reached the count first (earliest last completion).
        rows.sort(key=lambda r: (-r["items"], r["last"]))
        return rows

    def _problems_rows(self) -> list[dict]:
        member_ids, names = self._member_maps()
        if self.period == "all":
            board = db.problems_board()
            in_scope = [r for r in board if r["user_id"] in member_ids]
            ids = [r["user_id"] for r in in_scope]
            totals = {r["user_id"]: r["total"] for r in in_scope}
            lifetime = db.lifetime_points_batch(ids)
            ordered = _order_uids(ids, lifetime, totals, db.user_rank_info(ids))
            dates = db.solve_dates_batch(ids)
            tags = db.verified_platforms_batch(ids)
            rows: list[dict] = []
            for rank, uid in enumerate(ordered, start=1):
                cur, _ = db.streak_from_dates(dates.get(uid, []))
                rows.append({
                    "uid": uid,
                    "name": names.get(uid, str(uid)),
                    "points": lifetime.get(uid, 0),
                    "items": totals.get(uid, 0),
                    "streak": cur,
                    "tags": " ".join(emojis.platform_emoji(p) for p in tags.get(uid, [])) or "—",
                    "rank_emoji": emojis.rank_emoji(points.rank_for_points(lifetime.get(uid, 0))),
                    "move": None,
                })
            return rows
        start_date, prev_start, _ = _period_window(self.period)
        return ranked_period_rows(member_ids, names, start_date, prev_start)

    def render(self) -> discord.Embed | None:
        if self.board == "roadmap" and self._roadmap_pid is None:
            return None
        if self.board == "problems":
            rows = self._problems_rows()
            if self.period == "all":
                title = "Leaderboard — Problems solved (All time)"
            else:
                _, _, suffix = _period_window(self.period)
                title = f"Leaderboard — Problems solved ({suffix})"
            embed = discord.Embed(title=title, color=discord.Color.gold())
            if not rows:
                embed.description = "Nobody on the board yet. Link an account and solve something!"
            else:
                lines = []
                for rank, r in enumerate(rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE], start=self.page * PAGE_SIZE + 1):
                    move = f" {r['move']}" if r.get("move") else ""
                    lines.append(
                        f"{r['rank_emoji']} **{r['name']}** — {r['points']} pts · {r['items']} solved · {emojis.STREAK} {r['streak']}d{move}\n"
                        f"└ {r['tags']}"
                    )
                embed.description = "\n".join(lines)[:4000]
            pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
            self.page = max(0, min(self.page, pages - 1))
            embed.set_footer(text=f"Page {self.page + 1}/{pages} · Easy 1 · Medium 3 · Hard 10 · Not on the board? Link an account with {self.prefix}link")
            self._refresh_buttons(pages)
            return embed
        mode_label = "All time" if self.mode == "all" else "This week"
        rows = self._roadmap_rows()
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        embed = discord.Embed(title=f"Leaderboard — {self._roadmap_title} ({mode_label})", color=discord.Color.gold())
        if not rows:
            embed.description = "Nobody on the board yet. Be the first — finish an item!"
        else:
            lines = []
            for rank, r in enumerate(rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE], start=self.page * PAGE_SIZE + 1):
                lines.append(f"{emojis.podium_medal(rank - 1)} **{r['name']}** — {r['items']} items · {r['pct']}% · {emojis.STREAK} {r['streak']}d")
            embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text=f"Page {self.page + 1}/{pages}")
        self._refresh_buttons(pages)
        return embed

    def _refresh_buttons(self, pages: int) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            cid = child.custom_id
            if cid == "lb_prev":
                child.disabled = self.page <= 0
            elif cid == "lb_next":
                child.disabled = self.page >= pages - 1
            elif cid == "lb_toggle":
                child.disabled = self.board != "roadmap"
            elif cid and cid.startswith("lb_p_"):
                child.disabled = self.board != "problems"

    async def _rerender(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.render(), view=self)
        self.message = interaction.message

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="lb_prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await self._rerender(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="lb_next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await self._rerender(interaction)

    @discord.ui.button(label="⇄ All time / Week", style=discord.ButtonStyle.primary, custom_id="lb_toggle")
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.board != "roadmap":
            await interaction.response.send_message(
                "Period buttons above control the Problems board.", ephemeral=True
            )
            return
        self.mode = "week" if self.mode == "all" else "all"
        self.page = 0
        await self._rerender(interaction)

    @discord.ui.button(label="⇄ Problems / Roadmap", style=discord.ButtonStyle.primary, custom_id="lb_board")
    async def board_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.board = "roadmap" if self.board == "problems" else "problems"
        self.page = 0
        embed = self.render()
        if embed is None:
            await interaction.response.send_message(
                f"Set your path first (use `{self.prefix}paths`).", ephemeral=True
            )
            self.board = "problems"
            return
        await interaction.response.edit_message(embed=embed, view=self)
        self.message = interaction.message

    @discord.ui.button(label="Week", style=discord.ButtonStyle.secondary, custom_id="lb_p_week")
    async def p_week(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "week"
        self.page = 0
        await self._rerender(interaction)

    @discord.ui.button(label="Last wk", style=discord.ButtonStyle.secondary, custom_id="lb_p_lastweek")
    async def p_lastweek(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "lastweek"
        self.page = 0
        await self._rerender(interaction)

    @discord.ui.button(label="Month", style=discord.ButtonStyle.secondary, custom_id="lb_p_month")
    async def p_month(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "month"
        self.page = 0
        await self._rerender(interaction)

    @discord.ui.button(label="Year", style=discord.ButtonStyle.secondary, custom_id="lb_p_year")
    async def p_year(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "year"
        self.page = 0
        await self._rerender(interaction)

    @discord.ui.button(label="All", style=discord.ButtonStyle.secondary, custom_id="lb_p_all")
    async def p_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.period = "all"
        self.page = 0
        await self._rerender(interaction)


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Profile(bot))
