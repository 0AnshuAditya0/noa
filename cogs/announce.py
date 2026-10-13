"""Weekly podium announcements. Server admins pick a channel; a 5-minute
loop posts last week's top 3 once Monday 00:05 (BOT_TZ) passes."""
import logging
import os
from datetime import timedelta

import discord
from discord.ext import commands, tasks

import db
import tzutil

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
CHECK_MINUTES = 5
TRIGGER_MINUTES = 5
MAX_LATE_DAYS = 3
MAX_FAILURES = 3


def _is_owner(ctx: commands.Context) -> bool:
    return bool(OWNER_ID) and ctx.author.id == OWNER_ID


def compute_week_board(guild: discord.Guild, week_start) -> tuple[str, list[dict]]:
    """Ranked rows for the week starting at week_start (local Monday 00:00),
    reusing the leaderboard scoring (snapshot deltas + E/M/H + rating +
    registered_at ordering)."""
    from cogs.profile import _order_uids

    member_ids = {m.id for m in guild.members if not m.bot}
    names = {m.id: m.display_name for m in guild.members}
    verified = {r["user_id"] for r in db.list_verified_links() if r["user_id"] in member_ids}
    start_date = week_start.date().isoformat()
    uids = sorted(verified)
    scores = db.window_scores(uids, start_date)
    wcounts = db.solve_difficulty_counts(uids, tzutil.to_utc_iso(week_start))
    weighted = {u: c["easy"] + 3 * c["medium"] + 5 * c["hard"] for u, c in wcounts.items()}
    ordered = _order_uids(uids, scores, weighted, db.user_rank_info(uids))
    dates = db.solve_dates_batch(uids)
    rows = []
    for uid in ordered:
        cur, _ = db.streak_from_dates(dates.get(uid, []))
        rows.append({"uid": uid, "name": names.get(uid, str(uid)),
                     "items": scores.get(uid, 0), "streak": cur})
    return tzutil.week_label(week_start), rows


def compute_last_week_board(guild: discord.Guild) -> tuple[str, list[dict]]:
    """Ranked rows for the just-ended week."""
    start, _ = tzutil.last_week_range()
    return compute_week_board(guild, start)


def build_podium_embed(week_label: str, rows: list[dict]) -> discord.Embed | None:
    """Top-3 embed, or None when nobody scored (caller still records the week)."""
    scorers = [r for r in rows if r["items"] > 0][:3]
    if not scorers:
        return None
    embed = discord.Embed(title=f"Weekly podium — {week_label}", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    embed.description = "\n".join(
        f"{medals[i]} **{r['name']}** — {r['items']} solved · 🔥 {r['streak']}d"
        for i, r in enumerate(scorers)
    )[:4000]
    footer = "Next week starts now, keep grinding"
    if len(scorers) < 3:
        footer += f" · only {len(scorers)} competitor{'s' if len(scorers) != 1 else ''} this week"
    embed.set_footer(text=footer)
    return embed


class Announce(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()
        self.announce_loop.start()

    def cog_unload(self) -> None:
        self.announce_loop.cancel()

    @commands.command(name="setchannel")
    async def setchannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        """Set this server's weekly-podium channel. Needs Manage Server."""
        if ctx.guild is None:
            await ctx.send("Run this command in a server, not DMs.")
            return
        if not (ctx.author.guild_permissions.manage_guild or _is_owner(ctx)):
            await ctx.send("You need the Manage Server permission for that.")
            return
        target = channel or ctx.channel
        if not isinstance(target, discord.TextChannel):
            await ctx.send("Mention a text channel, e.g. `>setchannel #general`.")
            return
        me = ctx.guild.me or ctx.guild.get_member(self.bot.user.id)
        perms = target.permissions_for(me) if me else None
        missing = []
        if perms is None or not perms.send_messages:
            missing.append("Send Messages")
        if perms is None or not perms.embed_links:
            missing.append("Embed Links")
        if missing:
            await ctx.send(f"I'm missing {' and '.join(missing)} in {target.mention}.")
            return
        db.set_announce_channel(ctx.guild.id, target.id)
        await ctx.send(f"Weekly podium will be posted in {target.mention}.")

    @commands.command(name="unsetchannel")
    async def unsetchannel(self, ctx: commands.Context) -> None:
        """Turn off weekly-podium announcements for this server."""
        if ctx.guild is None:
            await ctx.send("Run this command in a server, not DMs.")
            return
        if not (ctx.author.guild_permissions.manage_guild or _is_owner(ctx)):
            await ctx.send("You need the Manage Server permission for that.")
            return
        if db.clear_announce_channel(ctx.guild.id):
            await ctx.send("Announcements off. No channel is set now.")
        else:
            await ctx.send("No announce channel was set.")

    @tasks.loop(minutes=CHECK_MINUTES)
    async def announce_loop(self) -> None:
        """Every 5 min: past Monday 00:05 local and week unannounced -> post once."""
        try:
            await self._tick(tzutil.now_local())
        except Exception:  # noqa: BLE001 - the loop itself must never die
            logger.exception("Announce loop tick failed")

    async def _tick(self, now) -> None:
        trigger = tzutil.monday_00(now) + timedelta(minutes=TRIGGER_MINUTES)
        if now < trigger:
            return
        start = tzutil.monday_00(now) - timedelta(days=7)
        label = tzutil.week_label(start)
        for row in db.list_guild_settings():
            if row.get("last_announced_week") == label:
                continue
            try:
                if now - trigger > timedelta(days=MAX_LATE_DAYS):
                    db.mark_week_announced(row["guild_id"], label)
                    logger.info("Skipped stale podium for guild %s week %s (>%dd late)",
                                row["guild_id"], label, MAX_LATE_DAYS)
                    continue
                await self._announce_one(row, label, start)
            except Exception:  # noqa: BLE001 - one guild must not stop the loop
                logger.exception("Announce tick failed for guild %s", row["guild_id"])

    async def _announce_one(self, row: dict, label: str, week_start=None) -> None:
        guild_id = row["guild_id"]
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            await self._delivery_failed(guild_id, "bot no longer in guild")
            return
        channel = guild.get_channel(row["announce_channel_id"])
        if channel is None:
            await self._delivery_failed(guild_id, "channel deleted/inaccessible")
            return
        if week_start is None:
            start, _ = tzutil.last_week_range()
        else:
            start = week_start
        _, rows = compute_week_board(guild, start)
        embed = build_podium_embed(label, rows)
        if embed is None:
            db.mark_week_announced(guild_id, label)
            logger.info("Skipped podium for guild %s week %s: nobody scored", guild_id, label)
            return
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            await self._delivery_failed(guild_id, f"{type(exc).__name__}: {exc}")
            return
        db.mark_week_announced(guild_id, label)
        logger.info("Posted podium for guild %s week %s", guild_id, label)

    async def _delivery_failed(self, guild_id: int, reason: str) -> None:
        failures = db.record_announce_failure(guild_id)
        logger.warning("Podium delivery failed for guild %s (%s), consecutive failures: %d",
                       guild_id, reason, failures)
        if failures >= MAX_FAILURES:
            db.clear_announce_channel(guild_id)
            logger.warning("Cleared announce channel for guild %s after %d failures", guild_id, failures)

    @announce_loop.before_loop
    async def _before_announce_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Announce(bot))
