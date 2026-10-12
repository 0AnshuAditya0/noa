"""Owner-only admin commands. Deliberately hidden from >help."""
import logging
import os

import discord
from discord.ext import commands

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

    @commands.group(name="admin", invoke_without_command=True)
    async def admin(self, ctx: commands.Context) -> None:
        """Owner-only administration."""
        if not _is_owner(ctx):
            await ctx.send("This command is for the bot owner.")
            return
        await ctx.send(f"Available: `{ctx.prefix}admin stats`, `{ctx.prefix}admin sync <@user>`.")

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


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Admin(bot))
