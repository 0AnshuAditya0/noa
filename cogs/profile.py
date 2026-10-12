from datetime import datetime, timezone

import discord
from discord.ext import commands

import db
import roadmaps
from cogs.roadmap import _bar
from platforms import ADAPTERS

PAGE_SIZE = 10
BOARD_TIMEOUT = 180

# Fixed display list. Platforms without an adapter yet render as "coming soon";
# registering a new adapter flips its line live with no profile code change.
KNOWN_PLATFORMS: list[tuple[str, str]] = [
    ("codeforces", "Codeforces"),
    ("leetcode", "LeetCode"),
    ("atcoder", "AtCoder"),
]
PLATFORM_SHORT = {"codeforces": "CF", "leetcode": "LC", "atcoder": "AC"}


def _short(platform: str) -> str:
    return PLATFORM_SHORT.get(platform, platform[:2].upper())


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


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()

    @commands.command(name="profile")
    async def profile(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """Show your (or another member's) learning profile."""
        target = member or ctx.author
        if target.bot:
            await ctx.send("Bots don't have profiles.")
            return
        user = db.get_user(target.id)
        if user is None:
            await ctx.send(f"{target.display_name} isn't registered yet.")
            return
        paths = roadmaps.load_roadmaps()
        pid = user.get("active_path")
        data = paths.get(pid, {}) if pid else {}
        if pid and data:
            done_n, total, pct = _progress_line(target.id, pid, data)
            path_label = f"{data.get('title', pid)} (`{pid}`)\n`{_bar(pct)}` {pct}% ({done_n}/{total})"
        else:
            path_label = str(pid or "—")
            done_n, total = 0, 0
        current, longest = db.get_streaks(target.id)
        embed = discord.Embed(title=f"{target.display_name}'s profile", color=discord.Color.blurple())
        embed.add_field(name="Active path", value=path_label, inline=False)
        embed.add_field(name="Experience", value=str(user.get("experience_level") or "—"), inline=True)
        embed.add_field(name="Hours/week", value=str(user.get("weekly_hours") or "—"), inline=True)
        embed.add_field(name="Goal", value=str(user.get("goal") or "—"), inline=False)
        embed.add_field(name="Registered", value=str(user.get("registered_at", "—"))[:10], inline=True)
        embed.add_field(name="Items completed", value=str(done_n), inline=True)
        embed.add_field(name="Roadmap streak", value=f"🔥 {current}d current · {longest}d longest", inline=False)
        lines = [_platform_line(target.id, plat, label, ctx.prefix) for plat, label in KNOWN_PLATFORMS]
        totals = db.platform_totals(target.id)
        ccur, clong = db.coding_streak(target.id)
        lines.append(
            f"Total: {totals['total']} solved across verified platforms | "
            f"coding streak 🔥 {ccur}d (longest {clong}d)"
        )
        embed.add_field(name="Coding Platforms", value="\n".join(lines)[:1024], inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="leaderboard")
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


class LeaderboardView(discord.ui.View):
    """Two boards (Problems default, Roadmap unchanged) + All time/Week toggle. Invoker-only."""

    def __init__(self, owner_id: int, prefix: str, guild: discord.Guild) -> None:
        super().__init__(timeout=BOARD_TIMEOUT)
        self.owner_id = owner_id
        self.prefix = prefix
        self.guild = guild
        self.page = 0
        self.mode: str = "all"  # or "week"
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
        """Rank verified-platform totals desc, best rating desc, registered_at asc."""
        member_ids, names = self._member_maps()
        since = db.week_cutoff_iso() if self.mode == "week" else None
        board = db.problems_board(since)
        in_scope = [r for r in board if r["user_id"] in member_ids]
        ids = [r["user_id"] for r in in_scope]
        dates = db.solve_dates_batch(ids)
        tags = db.verified_platforms_batch(ids, since)
        rows: list[dict] = []
        for r in in_scope:
            uid = r["user_id"]
            cur, _ = db.streak_from_dates(dates.get(uid, []))
            rows.append({
                "uid": uid,
                "name": names.get(uid, str(uid)),
                "items": r["total"],
                "streak": cur,
                "tags": "+".join(_short(p) for p in tags.get(uid, [])) or "—",
            })
        return rows  # already ordered by SQL

    def render(self) -> discord.Embed | None:
        if self.board == "roadmap" and self._roadmap_pid is None:
            return None
        mode_label = "All time" if self.mode == "all" else "This week"
        if self.board == "problems":
            rows = self._problems_rows()
            embed = discord.Embed(
                title=f"Leaderboard — Problems solved ({mode_label})", color=discord.Color.gold()
            )
            if not rows:
                embed.description = "Nobody on the board yet. Link an account and solve something!"
            else:
                lines = []
                for rank, r in enumerate(rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE], start=self.page * PAGE_SIZE + 1):
                    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}`")
                    lines.append(
                        f"{medal} **{r['name']}** — {r['items']} solved · 🔥 {r['streak']}d\n"
                        f"└ `{r['tags']}`"
                    )
                embed.description = "\n".join(lines)[:4000]
            pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
            self.page = max(0, min(self.page, pages - 1))
            embed.set_footer(text=f"Page {self.page + 1}/{pages} · Not on the board? Link an account with {self.prefix}link")
            self._refresh_buttons(pages)
            return embed
        rows = self._roadmap_rows()
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        embed = discord.Embed(title=f"Leaderboard — {self._roadmap_title} ({mode_label})", color=discord.Color.gold())
        if not rows:
            embed.description = "Nobody on the board yet. Be the first — finish an item!"
        else:
            lines = []
            for rank, r in enumerate(rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE], start=self.page * PAGE_SIZE + 1):
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}`")
                lines.append(f"{medal} **{r['name']}** — {r['items']} items · {r['pct']}% · 🔥 {r['streak']}d")
            embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text=f"Page {self.page + 1}/{pages}")
        self._refresh_buttons(pages)
        return embed

    def _refresh_buttons(self, pages: int) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id in ("lb_prev", "lb_next"):
                if child.custom_id == "lb_prev":
                    child.disabled = self.page <= 0
                else:
                    child.disabled = self.page >= pages - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, custom_id="lb_prev")
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page -= 1
        await interaction.response.edit_message(embed=self.render(), view=self)
        self.message = interaction.message

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="lb_next")
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page += 1
        await interaction.response.edit_message(embed=self.render(), view=self)
        self.message = interaction.message

    @discord.ui.button(label="⇄ All time / Week", style=discord.ButtonStyle.primary, custom_id="lb_toggle")
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.mode = "week" if self.mode == "all" else "all"
        self.page = 0
        await interaction.response.edit_message(embed=self.render(), view=self)
        self.message = interaction.message

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


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Profile(bot))
