import math
import time

import discord
from discord.ext import commands

import db

VERSION = "1.0.0"
_STARTED_AT = time.monotonic()

PING_GREEN_MS = 150
PING_YELLOW_MS = 400


def format_ms(value: float | None) -> str:
    """Format a latency in ms, or 'n/a' for missing/non-finite readings."""
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:.0f} ms"


def latency_color(*values_ms: float | None) -> discord.Color:
    """Green/yellow/red by the worst finite reading; grey when all are n/a."""
    finite = [v for v in values_ms if v is not None and math.isfinite(v)]
    if not finite:
        return discord.Color.dark_grey()
    worst = max(finite)
    if worst <= PING_GREEN_MS:
        return discord.Color.green()
    if worst <= PING_YELLOW_MS:
        return discord.Color.yellow()
    return discord.Color.red()


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ping(self, ctx: commands.Context) -> None:
        """Check the bot is alive: heartbeat, API and database latency."""
        heartbeat_ms = self.bot.latency * 1000 if self.bot.latency is not None else None
        t0 = time.perf_counter()
        msg = await ctx.send("Pinging…")
        api_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        with db._connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ms = (time.perf_counter() - t1) * 1000
        embed = discord.Embed(title="Pong!", color=latency_color(heartbeat_ms, api_ms, db_ms))
        embed.add_field(name="Heartbeat", value=format_ms(heartbeat_ms), inline=True)
        embed.add_field(name="API round trip", value=format_ms(api_ms), inline=True)
        embed.add_field(name="Database", value=format_ms(db_ms), inline=True)
        await msg.edit(content=None, embed=embed)

    @commands.command(name="about")
    async def about(self, ctx: commands.Context) -> None:
        """About this bot: version, uptime, servers and users."""
        import os
        import platform as _platform

        uptime = int(time.monotonic() - _STARTED_AT)
        hours, remainder = divmod(uptime, 3600)
        days, hours = divmod(hours, 24)
        uptime_str = f"{days}d {hours}h {remainder // 60}m" if days else f"{hours}h {remainder // 60}m"
        with db._connect() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        embed = discord.Embed(title=f"Noa v{VERSION}", color=discord.Color.blurple())
        embed.add_field(name="Uptime", value=uptime_str, inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Registered users", value=str(users), inline=True)
        embed.add_field(name="Python", value=_platform.python_version(), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        repo = os.getenv("REPO_URL", "")
        if repo:
            embed.add_field(name="Repo", value=repo, inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ping(bot))
