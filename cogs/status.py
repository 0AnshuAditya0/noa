import logging

import discord
from discord.ext import commands, tasks

import db

logger = logging.getLogger(__name__)

STATUSES: list[tuple[discord.ActivityType, str]] = [
    (discord.ActivityType.playing, "Debugging your life"),
    (discord.ActivityType.watching, "Watching {n} learners grind"),
    (discord.ActivityType.playing, "Playing {prefix}help"),
    (discord.ActivityType.competing, "Competing in the weekly leaderboard"),
    (discord.ActivityType.playing, "Compiling your career path"),
]


def render_statuses(prefix: str, learner_count: int) -> list[tuple[discord.ActivityType, str]]:
    """Fill the {prefix} and {n} placeholders in every status."""
    return [(kind, text.format(prefix=prefix, n=learner_count)) for kind, text in STATUSES]


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._index = 0
        db.init_db()
        self.status_loop.start()

    def cog_unload(self) -> None:
        self.status_loop.cancel()

    @tasks.loop(minutes=3)
    async def status_loop(self) -> None:
        try:
            with db._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            prefix = str(getattr(self.bot, "command_prefix", ">"))
            if isinstance(prefix, (list, tuple)):
                prefix = prefix[0]
            statuses = render_statuses(prefix, count)
            kind, text = statuses[self._index % len(statuses)]
            self._index += 1
            await self.bot.change_presence(activity=discord.Activity(type=kind, name=text))
        except Exception:  # noqa: BLE001 - presence must never crash the bot
            logger.exception("Status rotation failed")

    @status_loop.before_loop
    async def _before_status_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Status(bot))
