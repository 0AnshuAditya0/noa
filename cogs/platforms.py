import asyncio
import secrets
import time

import discord
from discord.ext import commands, tasks

import db
from platforms import ADAPTERS
from platforms.base import PlatformError

VERIFY_TIMEOUT = 300
SYNC_COOLDOWN = 10 * 60
LOOP_DELAY = 3


async def sync_one(adapter, user_id: int, platform: str, handle: str, since: str | None = None) -> dict:
    """Fetch profile + solves, store them, rebuild stats. Raises PlatformError on API failure."""
    profile = await adapter.fetch_profile(handle)
    if not profile.get("exists"):
        raise PlatformError(f"Handle '{handle}' not found on {platform}.")
    solves = await adapter.fetch_solves(handle, since)
    new = db.upsert_solves(user_id, platform, solves)
    stats = db.rebuild_platform_stats(user_id, platform, profile.get("rating"), profile.get("max_rating"))
    stats["new_solves"] = new
    return stats


class VerifyView(discord.ui.View):
    def __init__(self, owner_id: int, platform: str, handle: str, token: str) -> None:
        super().__init__(timeout=VERIFY_TIMEOUT)
        self.owner_id = owner_id
        self.platform = platform
        self.handle = handle
        self.token = token
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
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        adapter = ADAPTERS.get(self.platform)
        if adapter is None:
            await interaction.edit_original_response(content=f"Unknown platform `{self.platform}`.", embed=None, view=None)
            return
        try:
            field_text = await adapter.get_verification_field(self.handle)
        except PlatformError as exc:
            await interaction.edit_original_response(
                content=f"Could not reach {self.platform}: {exc}. Try again.", embed=None, view=self
            )
            self.message = interaction.message
            return
        if self.token not in field_text:
            embed = discord.Embed(
                title="Token not found yet",
                description=(
                    f"Put `{self.token}` in your Codeforces **organization** field and save, "
                    f"then press Verify again."
                ),
                color=discord.Color.orange(),
            )
            await interaction.edit_original_response(content=None, embed=embed, view=self)
            self.message = interaction.message
            return
        owner = db.handle_verified_by(self.platform, self.handle)
        if owner is not None and owner != self.owner_id:
            await interaction.edit_original_response(
                content="That handle was just verified by someone else.", embed=None, view=None
            )
            return
        db.set_verified(self.owner_id, self.platform)
        try:
            stats = await sync_one(adapter, self.owner_id, self.platform, self.handle)
        except PlatformError as exc:
            await interaction.edit_original_response(
                content=f"Verified, but the first sync failed: {exc}. Use `>sync` later.", embed=None, view=None
            )
            return
        embed = discord.Embed(title=f"{self.platform} linked: {self.handle}", color=discord.Color.green())
        embed.add_field(name="Rating", value=str(stats["rating"] or "unrated"), inline=True)
        embed.add_field(name="Max rating", value=str(stats["max_rating"] or "—"), inline=True)
        embed.add_field(
            name="Solved",
            value=f"{stats['total_solved']} (easy {stats['easy']} · medium {stats['medium']} · hard {stats['hard']})",
            inline=False,
        )
        await interaction.edit_original_response(content=None, embed=embed, view=None)


class Platforms(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sync_cooldown: dict[int, float] = {}
        db.init_db()
        self.platform_sync_loop.start()

    def cog_unload(self) -> None:
        self.platform_sync_loop.cancel()

    @commands.command(name="link")
    async def link(self, ctx: commands.Context, platform: str, handle: str) -> None:
        """Link a coding-platform handle. Usage: >link codeforces <handle>."""
        platform = platform.strip().lower()
        handle = handle.strip()
        adapter = ADAPTERS.get(platform)
        if adapter is None:
            await ctx.send(f"Unknown platform `{platform}`. Supported: {', '.join(sorted(ADAPTERS))}.")
            return
        if not handle:
            await ctx.send("Give a handle: `>link codeforces <handle>`.")
            return
        existing = db.get_link(ctx.author.id, platform)
        if existing and existing.get("verified") and existing.get("handle", "").lower() == handle.lower():
            await ctx.send(f"`{handle}` is already linked and verified. Use `{ctx.prefix}sync` to refresh.")
            return
        try:
            profile = await adapter.fetch_profile(handle)
        except PlatformError as exc:
            await ctx.send(f"Could not reach {platform}: {exc}. Try again later.")
            return
        if not profile.get("exists"):
            await ctx.send(f"Handle `{handle}` not found on {platform}. Check the spelling.")
            return
        owner = db.handle_verified_by(platform, handle)
        if owner is not None and owner != ctx.author.id:
            await ctx.send("That handle is already verified by someone else.")
            return
        token = f"noa-{secrets.token_hex(3)}"
        db.create_pending_link(ctx.author.id, platform, handle, token)
        embed = discord.Embed(title=f"Link {platform}: {handle}", color=discord.Color.blurple())
        embed.description = (
            f"Paste this token into your Codeforces **organization** field "
            f"(profile settings) and save:\n`{token}`\n\nThen press Verify."
        )
        view = VerifyView(ctx.author.id, platform, handle, token)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="unlink")
    async def unlink(self, ctx: commands.Context, platform: str) -> None:
        """Unlink a platform and drop its cached data. Usage: >unlink <platform>."""
        platform = platform.strip().lower()
        if db.unlink_account(ctx.author.id, platform):
            await ctx.send(f"Unlinked `{platform}` and cleared its cached data.")
        else:
            await ctx.send(f"No linked `{platform}` account found. See `{ctx.prefix}accounts`.")

    @commands.command(name="accounts")
    async def accounts(self, ctx: commands.Context) -> None:
        """List your linked coding-platform accounts and their status."""
        links = db.list_links(ctx.author.id)
        if not links:
            await ctx.send(f"No linked accounts. Start with `{ctx.prefix}link codeforces <handle>`.")
            return
        embed = discord.Embed(title=f"{ctx.author.display_name}'s linked accounts", color=discord.Color.blurple())
        for link in links:
            platform, handle = link["platform"], link["handle"]
            status = "verified" if link["verified"] else "pending verification"
            stats = db.get_platform_stats(ctx.author.id, platform)
            if stats:
                value = (
                    f"{status} · rating {stats['rating'] or 'unrated'} · "
                    f"{stats['total_solved']} solved "
                    f"(e{stats['easy']}/m{stats['medium']}/h{stats['hard']})"
                )
            else:
                value = f"{status} · `{handle}`"
            embed.add_field(name=f"{platform}: {handle}", value=value, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="sync")
    async def sync(self, ctx: commands.Context) -> None:
        """Refresh your linked-platform stats (10 min cooldown)."""
        now = time.monotonic()
        last = self._sync_cooldown.get(ctx.author.id, 0)
        if now - last < SYNC_COOLDOWN:
            wait = int((SYNC_COOLDOWN - (now - last) + 59) // 60)
            await ctx.send(f"Sync on cooldown. Try again in ~{max(wait, 1)} min.")
            return
        links = [lnk for lnk in db.list_links(ctx.author.id) if lnk["verified"]]
        if not links:
            await ctx.send(f"No verified accounts. Use `{ctx.prefix}link codeforces <handle>` first.")
            return
        self._sync_cooldown[ctx.author.id] = now
        status_msg = await ctx.send("Syncing…")
        lines: list[str] = []
        for link in links:
            adapter = ADAPTERS.get(link["platform"])
            if adapter is None:
                continue
            stats = db.get_platform_stats(ctx.author.id, link["platform"])
            try:
                result = await sync_one(
                    adapter, ctx.author.id, link["platform"], link["handle"],
                    since=stats["last_synced"] if stats and stats.get("last_synced") else None,
                )
                lines.append(
                    f"**{link['platform']}: {link['handle']}** — {result['total_solved']} solved "
                    f"(+{result['new_solves']} new), rating {result['rating'] or 'unrated'}"
                )
            except PlatformError as exc:
                lines.append(f"**{link['platform']}: {link['handle']}** — sync failed ({exc}); kept last good data.")
        embed = discord.Embed(title="Sync complete", description="\n".join(lines), color=discord.Color.green())
        await status_msg.edit(content=None, embed=embed)

    @tasks.loop(hours=3)
    async def platform_sync_loop(self) -> None:
        """Background sync: one verified account at a time; a failure never stops the loop."""
        for row in db.list_verified_links():
            adapter = ADAPTERS.get(row["platform"])
            if adapter is None:
                continue
            try:
                stats = db.get_platform_stats(row["user_id"], row["platform"])
                await sync_one(
                    adapter, row["user_id"], row["platform"], row["handle"],
                    since=stats["last_synced"] if stats and stats.get("last_synced") else None,
                )
            except Exception as exc:  # noqa: BLE001 - one bad account must not stop the loop
                print(f"[platforms] background sync failed for {row['platform']}:{row['handle']}: {exc}")
            await asyncio.sleep(LOOP_DELAY)

    @platform_sync_loop.before_loop
    async def _before_sync_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Platforms(bot))
