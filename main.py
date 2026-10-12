import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()
TOKEN = os.getenv("TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX", ">")

if not TOKEN:
    raise RuntimeError("TOKEN not found in .env. Add TOKEN=your_bot_token to .env")


class NoaBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Auto-load every cog in cogs/*.py (discord.py 2.x async pattern)
        cogs_dir = Path(__file__).parent / "cogs"
        for file in cogs_dir.glob("*.py"):
            if file.name.startswith("_"):
                continue
            extension = f"cogs.{file.stem}"
            await self.load_extension(extension)
            print(f"Loaded extension: {extension}")


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
    embed = discord.Embed(
        title="Account required",
        description=f"You need an account first. Run `{ctx.prefix}register`.",
        color=discord.Color.orange(),
    )
    try:
        await ctx.send(embed=embed)
    except discord.HTTPException:
        pass
    return False


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    # Silently ignore unknown commands and gate blocks (already messaged).
    if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
        return
    raise error


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


async def main() -> None:
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
