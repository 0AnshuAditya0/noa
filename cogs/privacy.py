import discord
from discord.ext import commands

import db
import policy

CONFIRM_TIMEOUT = 60


class DeleteConfirmView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT)
        self.owner_id = owner_id
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
                await self.message.edit(content="Deletion cancelled (timed out).", embed=None, view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Yes, delete everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        counts = db.delete_user_data(interaction.user.id)
        total = sum(counts.values())
        await interaction.response.edit_message(
            content=f"Deleted {total} stored rows across {len(counts)} tables. Goodbye!",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Deletion cancelled.", embed=None, view=None)


class Privacy(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()

    @commands.command(name="privacy")
    async def privacy(self, ctx: commands.Context) -> None:
        """Explain in plain words what Noa stores about you."""
        embed = discord.Embed(title="Your privacy with Noa", color=discord.Color.blurple())
        version, at = db.get_consent(ctx.author.id)
        embed.description = (
            policy.full_policy_text()
            + f"\n\nYour consent: policy {version or '—'}"
              f"{f' accepted {at[:10]}' if at else ''}."
              f"\nRun `{ctx.prefix}export` for a copy of your data, "
              f"`{ctx.prefix}deleteme` to erase everything."
        )
        await ctx.send(embed=embed)

    @commands.command(name="export")
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def export(self, ctx: commands.Context) -> None:
        """Download everything Noa stores about you as a JSON file."""
        import io
        import json
        from datetime import datetime, timezone

        payload = json.dumps(
            {"user_id": ctx.author.id,
             "exported_at": datetime.now(timezone.utc).isoformat(),
             "tables": db.export_user_data(ctx.author.id)},
            indent=2,
            default=str,
        ).encode("utf-8")
        filename = f"noa-export-{ctx.author.id}.json"
        warning = "This file contains your personal data — keep it private."
        try:
            await ctx.author.send(
                content=warning,
                file=discord.File(io.BytesIO(payload), filename=filename),
            )
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send(
                content=f"{warning} (Could not DM you, so here it is:)",
                file=discord.File(io.BytesIO(payload), filename=filename),
            )
            return
        await ctx.send("Check your DMs — I sent your export there.")

    @commands.command(name="deleteme")
    async def deleteme(self, ctx: commands.Context) -> None:
        """Delete all your stored data, after confirmation."""
        if not db.is_registered(ctx.author.id):
            await ctx.send("You have no account to delete.")
            return
        view = DeleteConfirmView(ctx.author.id)
        msg = await ctx.send(
            "This permanently deletes your account, progress, streaks, linked accounts "
            "and learned marks. Are you sure?",
            view=view,
        )
        view.message = msg


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Privacy(bot))
