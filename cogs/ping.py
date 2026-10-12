from discord.ext import commands


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Replies with pong."""
        await ctx.send("pong")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ping(bot))
