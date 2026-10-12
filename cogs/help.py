import discord
from discord.ext import commands

import db


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context) -> None:
        """Show every command with its use case. Works for unregistered users too."""
        p = ctx.prefix
        desc = "Every command and what it's for."
        if not db.is_registered(ctx.author.id):
            desc = f"Run `{p}register` first — most commands need an account.\n\n" + desc
        embed = discord.Embed(title="Noa commands", description=desc, color=discord.Color.blurple())
        embed.add_field(
            name="Account",
            value=(
                f"`{p}register` — create your account. Walks you through picking a path, "
                f"experience level, weekly hours, and goal.\n"
                f"`{p}switchpath` — change your active path later. Progress on other paths is kept.\n"
                f"`{p}help` — show this message."
            ),
            inline=False,
        )
        embed.add_field(
            name="Roadmap",
            value=(
                f"`{p}paths` — list all learning paths with topic/item counts.\n"
                f"`{p}path [name]` — open the interactive topic browser for a path "
                f"(defaults to yours). Browse one topic at a time, jump with the menu, "
                f"mark/unmark items with the buttons.\n"
                f"`{p}finish <id> [path]` — mark one subtopic done (e.g. `{p}finish 05.2`). "
                f"A parent id like `05` marks its whole topic. Says so if already done.\n"
                f"`{p}unfinish <id> [path]` — unmark a subtopic or whole topic.\n"
                f"`{p}progress [path]` — your percent, progress bar, item count, and streak."
            ),
            inline=False,
        )
        embed.add_field(
            name="Social",
            value=(
                f"`{p}profile [@user]` — learning profile: path, stats, roadmap streak, "
                f"coding platforms, totals and coding streak. Without a mention it shows yours.\n"
                f"`{p}leaderboard` — server rankings: Problems solved (default) or Roadmap board, "
                f"Prev/Next pages and All time / This week toggle."
            ),
            inline=False,
        )
        embed.add_field(
            name="Platforms",
            value=(
                f"`{p}link <platform> <handle>` — link a coding account (codeforces). "
                f"Paste the token into your profile, then press Verify.\n"
                f"`{p}unlink <platform>` — unlink and clear its cached data.\n"
                f"`{p}accounts` — list your linked accounts and status.\n"
                f"`{p}sync` — refresh platform stats (10 min cooldown)."
            ),
            inline=False,
        )
        embed.add_field(
            name="General",
            value=f"`{p}ping` — check the bot is alive. Replies with pong.",
            inline=False,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
