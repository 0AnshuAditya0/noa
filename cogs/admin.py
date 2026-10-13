"""Owner-only admin commands. Deliberately hidden from >help."""
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

import db
from platforms import ADAPTERS

logger = logging.getLogger(__name__)
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)


def _is_owner(ctx: commands.Context) -> bool:
    return bool(OWNER_ID) and ctx.author.id == OWNER_ID


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()
        self.host_reminder_loop.start()

    def cog_unload(self) -> None:
        self.host_reminder_loop.cancel()

    @tasks.loop(hours=24)
    async def host_reminder_loop(self) -> None:
        """DM the owner to renew the host if 6+ days since the last reminder."""
        try:
            if not OWNER_ID:
                return
            last = db.get_meta("host_reminder_at")
            now = datetime.now(timezone.utc)
            if last:
                try:
                    if (now - datetime.fromisoformat(last)).days <= 6:
                        return
                except ValueError:
                    pass
            try:
                owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)
                await owner.send("Renew your host")
            except Exception:
                return  # skip silently on DM failure; try again tomorrow
            db.set_meta("host_reminder_at", now.isoformat())
        except Exception:  # noqa: BLE001 - reminders must never crash the bot
            logger.exception("Host reminder check failed")

    @host_reminder_loop.before_loop
    async def _before_host_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()

    @commands.group(name="admin", invoke_without_command=True)
    async def admin(self, ctx: commands.Context) -> None:
        """Owner-only administration."""
        if not _is_owner(ctx):
            await ctx.send("This command is for the bot owner.")
            return
        await ctx.send(f"Available: `{ctx.prefix}admin stats`, `{ctx.prefix}admin sync <@user>`, `{ctx.prefix}admin announce-now`.")

    @admin.command(name="stats")
    async def admin_stats(self, ctx: commands.Context) -> None:
        """Show instance stats: users, links, sync health."""
        if not _is_owner(ctx):
            await ctx.send("This command is for the bot owner.")
            return
        data = db.admin_stats()
        embed = discord.Embed(title="Admin stats", color=discord.Color.dark_grey())
        embed.add_field(name="Registered users", value=str(data["users"]), inline=False)
        links = "\n".join(
            f"{row['platform']}: {row['count']} ({'verified' if row['verified'] else 'pending'})"
            for row in data["links"]
        ) or "none"
        embed.add_field(name="Linked accounts", value=links[:1024], inline=False)
        syncs = "\n".join(f"{plat}: {ts or 'never'}" for plat, ts in data["latest_sync"].items()) or "none"
        embed.add_field(name="Latest sync per platform", value=syncs[:1024], inline=False)
        failing = "\n".join(
            f"<@{row['user_id']}> {row['platform']}: {row['last_error'] or 'unknown error'}"
            for row in data["failing"][:10]
        ) or "none"
        embed.add_field(name="Failing syncs", value=failing[:1024], inline=False)
        await ctx.send(embed=embed)

    @admin.command(name="sync")
    async def admin_sync(self, ctx: commands.Context, member: discord.Member) -> None:
        """Force one sync for a user, bypassing cooldowns."""
        if not _is_owner(ctx):
            await ctx.send("This command is for the bot owner.")
            return
        from cogs.platforms import sync_one

        links = [lnk for lnk in db.list_links(member.id) if lnk["verified"]]
        if not links:
            await ctx.send(f"{member.display_name} has no verified accounts.")
            return
        lines: list[str] = []
        for link in links:
            adapter = ADAPTERS.get(link["platform"])
            if adapter is None:
                continue
            try:
                result = await sync_one(adapter, member.id, link["platform"], link["handle"])
                lines.append(f"**{link['platform']}**: {result['total_solved']} solved (+{result['new_solves']} new)")
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                logger.warning("Admin-forced sync failed: platform=%s error=%s", link["platform"], type(exc).__name__)
                lines.append(f"**{link['platform']}**: failed ({type(exc).__name__})")
        await ctx.send("\n".join(lines)[:2000] or "Nothing synced.")

    @admin.command(name="announce-now")
    async def admin_announce_now(self, ctx: commands.Context) -> None:
        """Force-post last week's podium for testing. Never touches last_announced_week."""
        if not _is_owner(ctx):
            await ctx.send("This command is for the bot owner.")
            return
        if ctx.guild is None:
            await ctx.send("Run this command in a server, not DMs.")
            return
        from cogs.announce import build_podium_embed, compute_last_week_board

        label, rows = compute_last_week_board(ctx.guild)
        embed = build_podium_embed(label, rows)
        if embed is None:
            await ctx.send(f"No podium to post for {label}: nobody scored.")
            return
        settings = db.get_guild_settings(ctx.guild.id)
        target = None
        if settings and settings.get("announce_channel_id"):
            target = ctx.guild.get_channel(settings["announce_channel_id"])
        await (target or ctx.channel).send(embed=embed)
        logger.info("Owner forced podium post in guild %s week %s (week marker untouched)",
                    ctx.guild.id, label)
        await ctx.send(f"Posted test podium for {label} (last_announced_week untouched).")


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Admin(bot))
