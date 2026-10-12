import discord
from discord.ext import commands

import db


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context) -> None:
        """Show grouped command help. Works for unregistered users too."""
        p = ctx.prefix
        registered = db.is_registered(ctx.author.id)
        desc = f"`{p}register` — create your account and pick a path."
        if not registered:
            desc = f"Run `{p}register` first.\n\n" + desc
        embed = discord.Embed(title="Noa commands", description=desc, color=discord.Color.blurple())
        embed.add_field(
            name="Account",
            value=f"`{p}register` — interactive onboarding\n`{p}help` — this message",
            inline=False,
        )
        embed.add_field(
            name="Roadmap",
            value=(
                f"`{p}paths` — list learning paths\n"
                f"`{p}path [name]` — show a path (defaults to yours)\n"
                f"`{p}finish <id> [path]` — mark done (`05` = whole topic)\n"
                f"`{p}unfinish <id> [path]` — unmark\n"
                f"`{p}progress [path]` — percent + bar"
            ),
            inline=False,
        )
        embed.add_field(name="General", value=f"`{p}ping` — pong", inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
