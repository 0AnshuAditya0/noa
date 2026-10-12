import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX", ">")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Copy .env.example to .env and set DISCORD_TOKEN to your bot token."
    )


def setup_logging() -> logging.Logger:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    handler_file = RotatingFileHandler(
        logs_dir / "noa.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler_console = logging.StreamHandler()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler_file, handler_console],
    )
    return logging.getLogger("noa")


logger = setup_logging()

# Import cogs after logging exists so theirs inherit the configuration.
# (Cogs use logging.getLogger(__name__); nothing to do here.)


class UnregisteredError(commands.CheckFailure):
    """Raised by the registration gate; the error handler turns it into an embed."""


class NoaBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Auto-load every cog in cogs/*.py (discord.py 2.x async pattern)
        cogs_dir = Path(__file__).parent / "cogs"
        for file in cogs_dir.glob("*.py"):
            if file.name.startswith("_"):
                continue
            extension = f"cogs.{file.stem}"
            await self.load_extension(extension)
            logger.info("Loaded extension: %s", extension)


def make_bot() -> NoaBot:
    intents = discord.Intents.default()
    intents.message_content = True  # required for prefix commands to read content
    intents.members = True  # privileged: also enable "Server Members Intent" in the portal
    bot = NoaBot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)
    return bot


bot = make_bot()


@bot.check
async def registration_gate(ctx: commands.Context) -> bool:
    """Unregistered users may only use 'register' and 'help'."""
    if ctx.author.bot:
        return False
    if ctx.command is None:  # unknown command; let on_command_error ignore it
        return True
    if ctx.command.name in ("register", "help"):
        return True
    try:
        if db.is_registered(ctx.author.id):
            return True
    except Exception:
        return True  # fail open so a DB hiccup can't lock everyone out
    raise UnregisteredError()


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, UnregisteredError):
        embed = discord.Embed(
            title="Account required",
            description=f"You need an account first. Run `{ctx.prefix}register`.",
            color=discord.Color.orange(),
        )
        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            pass
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"Missing argument `{error.param.name}`.\n"
            f"Usage: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
        )
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"Couldn't understand that argument. {error}")
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Slow down — try again in {error.retry_after:.0f}s.")
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send("You can't use that command.")
        return
    logger.exception("Unhandled error in command %s", getattr(ctx.command, "qualified_name", "?"))
    try:
        await ctx.send("Something went wrong. The error has been logged.")
    except discord.HTTPException:
        pass


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)


async def main() -> None:
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
