import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")

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
    bot = NoaBot(command_prefix="!", intents=intents)
    return bot


bot = make_bot()


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


async def main() -> None:
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
