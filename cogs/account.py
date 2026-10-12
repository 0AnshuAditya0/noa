import discord
from discord.ext import commands

import db
import roadmaps

TIMEOUT = 120

EXPERIENCE_OPTIONS = ["Beginner", "Intermediate", "Advanced"]
HOURS_OPTIONS = ["<5", "5-10", "10-20", "20+"]
GOAL_OPTIONS = ["Placement/Job", "Internship", "Skill building", "Switching careers"]


class _OwnerView(discord.ui.View):
    """Base view: only the command invoker may interact; disables on timeout."""

    def __init__(self, owner_id: int, prefix: str, timeout: float = TIMEOUT) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.prefix = prefix
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content=f"Menu expired. Run `{self.prefix}register` again.",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass


def _step_embed(step: str, title: str, description: str) -> discord.Embed:
    embed = discord.Embed(title=f"{step}: {title}", description=description, color=discord.Color.blurple())
    return embed


class PathSelect(discord.ui.Select):
    def __init__(self, paths: dict, state: dict, prefix: str, owner_id: int) -> None:
        self._state = state
        self._prefix = prefix
        self._owner_id = owner_id
        options = []
        for pid, data in list(paths.items())[:25]:
            label = str(data.get("title", pid))[:100] or pid
            desc = str(data.get("description", ""))[:100] or None
            options.append(discord.SelectOption(label=label, value=pid, description=desc))
        super().__init__(placeholder="Choose your learning path…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        self._state["active_path"] = self.values[0]
        view = ExperienceView(self._owner_id, self._state, self._prefix)
        embed = _step_embed("Step 2/4", "Experience level", "How would you describe yourself?")
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = interaction.message


class PathView(_OwnerView):
    def __init__(self, owner_id: int, paths: dict, state: dict, prefix: str) -> None:
        super().__init__(owner_id, prefix)
        self.add_item(PathSelect(paths, state, prefix, owner_id))


class ExperienceView(_OwnerView):
    def __init__(self, owner_id: int, state: dict, prefix: str) -> None:
        super().__init__(owner_id, prefix)
        self._state = state
        for level in EXPERIENCE_OPTIONS:
            self.add_item(self._button(level, level))

    def _button(self, label: str, value: str) -> discord.ui.Button:
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

        async def cb(interaction: discord.Interaction) -> None:
            self._state["experience_level"] = value
            view = HoursView(self.owner_id, self._state, self.prefix)
            embed = _step_embed("Step 3/4", "Weekly study hours", "How many hours can you study per week?")
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message

        btn.callback = cb
        return btn


class HoursView(_OwnerView):
    def __init__(self, owner_id: int, state: dict, prefix: str) -> None:
        super().__init__(owner_id, prefix)
        self._state = state
        for hours in HOURS_OPTIONS:
            self.add_item(self._button(hours, hours))

    def _button(self, label: str, value: str) -> discord.ui.Button:
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

        async def cb(interaction: discord.Interaction) -> None:
            self._state["weekly_hours"] = value
            view = GoalView(self.owner_id, self._state, self.prefix)
            embed = _step_embed("Step 4/4", "Main goal", "What is your main goal?")
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message

        btn.callback = cb
        return btn


class GoalView(_OwnerView):
    def __init__(self, owner_id: int, state: dict, prefix: str) -> None:
        super().__init__(owner_id, prefix)
        self._state = state
        for goal in GOAL_OPTIONS:
            self.add_item(self._button(goal, goal))

    def _button(self, label: str, value: str) -> discord.ui.Button:
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.success)

        async def cb(interaction: discord.Interaction) -> None:
            self._state["goal"] = value
            user_id = interaction.user.id
            username = str(interaction.user)
            created = db.create_user(
                user_id,
                username,
                active_path=self._state.get("active_path"),
                experience_level=self._state.get("experience_level"),
                weekly_hours=self._state.get("weekly_hours"),
                goal=self._state.get("goal"),
            )
            if not created:
                # Registered mid-flow on another device; update instead.
                db.update_user(user_id, username=username, **self._state)
            paths = roadmaps.load_roadmaps()
            pid = self._state.get("active_path")
            title = paths.get(pid, {}).get("title", pid) if pid else pid
            p = self.prefix
            embed = discord.Embed(title="Welcome to Noa!", color=discord.Color.green())
            embed.add_field(name="Path", value=str(title), inline=False)
            embed.add_field(name="Experience", value=str(self._state.get("experience_level")), inline=True)
            embed.add_field(name="Hours/week", value=str(self._state.get("weekly_hours")), inline=True)
            embed.add_field(name="Goal", value=str(self._state.get("goal")), inline=False)
            embed.add_field(
                name="Next steps",
                value=f"`{p}path` — view your path\n`{p}finish <id>` — mark a topic done\n`{p}progress` — see your progress",
                inline=False,
            )
            await interaction.response.edit_message(content=None, embed=embed, view=None)

        btn.callback = cb
        return btn


class Account(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()

    @commands.command(name="register")
    async def register(self, ctx: commands.Context) -> None:
        """Register and pick a learning path via an interactive menu."""
        if db.is_registered(ctx.author.id):
            user = db.get_user(ctx.author.id)
            active = (user or {}).get("active_path", "?")
            embed = discord.Embed(
                title="Already registered",
                description=f"You're already registered (path: `{active}`). Use `{ctx.prefix}help` to continue.",
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)
            return
        paths = roadmaps.load_roadmaps()
        if not paths:
            await ctx.send("No learning paths available yet. Try again later.")
            return
        state: dict = {}
        view = PathView(ctx.author.id, paths, state, ctx.prefix)
        embed = _step_embed("Step 1/4", "Pick a learning path", "Select the path you want to follow.")
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Account(bot))
