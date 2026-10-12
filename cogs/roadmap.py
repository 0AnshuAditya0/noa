import discord
from discord.ext import commands

import db
import roadmaps

BAR_LEN = 12
BROWSER_TIMEOUT = 180


def _bar(pct: float, length: int = BAR_LEN) -> str:
    filled = round(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


def _user_active_path(paths: dict, user_id: int) -> tuple[str, dict] | None:
    """Return the user's active_path if it still exists."""
    user = db.get_user(user_id)
    if user and user.get("active_path") in paths:
        pid = user["active_path"]
        return pid, paths[pid]
    return None


def _resolve_single_path(paths: dict, name: str | None, user_id: int | None = None):
    if name:
        return roadmaps.find_path(paths, name)
    if user_id is not None:
        hit = _user_active_path(paths, user_id)
        if hit is not None:
            return hit
    if len(paths) == 1:
        pid = next(iter(paths))
        return pid, paths[pid]
    return None


def _topic_page_embed(path_id: str, data: dict, index: int, user_id: int) -> discord.Embed:
    """Build the one-topic embed, loading fresh progress each render."""
    topics = data.get("topics", [])
    index = max(0, min(index, len(topics) - 1))
    topic = topics[index]
    done = db.get_done_ids(user_id, path_id)
    subs = topic.get("subtopics", [])
    done_n = sum(1 for s in subs if str(s.get("id")) in done)
    embed = discord.Embed(
        title=f"{topic.get('id')} — {topic.get('title')}",
        color=discord.Color.blurple(),
    )
    lines: list[str] = []
    for sub in subs:
        sid = str(sub.get("id"))
        mark = "✅" if sid in done else "⬜"
        line = f"{mark} `{sid}` **{sub.get('title', '')}**"
        resources = sub.get("resources", [])[:3]
        if resources:
            links = " · ".join(f"[{r.get('name', 'link')[:40]}]({r.get('url')})" for r in resources if r.get("url"))
            if links:
                line += f"\n└ {links}"
        lines.append(line)
    embed.description = "\n".join(lines)[:4000] or "No subtopics."
    total = roadmaps.total_count(data)
    overall_n = len(done)
    pct = round(overall_n / total * 100) if total else 0
    current, _ = db.get_streaks(user_id)
    embed.set_footer(
        text=f"Topic {index + 1}/{len(topics)} · {done_n}/{len(subs)} · Overall {_bar(pct)} {pct}% ({overall_n}/{total}) · 🔥 {current}"
    )
    return embed


class _OwnerView(discord.ui.View):
    def __init__(self, owner_id: int, prefix: str, timeout: float = BROWSER_TIMEOUT) -> None:
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
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def _show_browser_page(
    interaction: discord.Interaction, owner_id: int, prefix: str, path_id: str, index: int
) -> None:
    """Re-render one topic page from fresh JSON (handles JSON changing mid-session)."""
    paths = roadmaps.load_roadmaps()
    data = paths.get(path_id)
    if data is None:
        await interaction.response.edit_message(
            content=f"That path is no longer available. Use `{prefix}paths`.", embed=None, view=None
        )
        return
    topics = data.get("topics", [])
    if not topics:
        await interaction.response.edit_message(content="That path has no topics.", embed=None, view=None)
        return
    index = max(0, min(index, len(topics) - 1))
    embed = _topic_page_embed(path_id, data, index, owner_id)
    view = PathBrowserView(owner_id, prefix, path_id, index)
    await interaction.response.edit_message(content=None, embed=embed, view=view)
    view.message = interaction.message


class TopicJumpSelect(discord.ui.Select):
    def __init__(self, owner_id: int, prefix: str, path_id: str, topics: list[dict]) -> None:
        self._owner_id = owner_id
        self._prefix = prefix
        self._path_id = path_id
        options = [
            discord.SelectOption(
                label=f"{t.get('id')} {str(t.get('title', ''))[:80]}",
                value=str(t.get("id")),
            )
            for t in topics[:25]
        ]
        super().__init__(placeholder="Jump to a topic…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        paths = roadmaps.load_roadmaps()
        topics = paths.get(self._path_id, {}).get("topics", [])
        ids = [str(t.get("id")) for t in topics]
        try:
            new_index = ids.index(self.values[0])
        except ValueError:
            new_index = 0  # topic removed/renamed since render
        await _show_browser_page(interaction, self._owner_id, self._prefix, self._path_id, new_index)


class PathBrowserView(_OwnerView):
    def __init__(self, owner_id: int, prefix: str, path_id: str, index: int) -> None:
        super().__init__(owner_id, prefix)
        self.path_id = path_id
        self.index = index
        topics = roadmaps.load_roadmaps().get(path_id, {}).get("topics", [])
        if topics:
            self.add_item(TopicJumpSelect(owner_id, prefix, path_id, topics))

        prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=index <= 0)
        next_btn = discord.ui.Button(
            label="Next ▶", style=discord.ButtonStyle.secondary, disabled=index >= len(topics) - 1
        )
        done_btn = discord.ui.Button(label="✅ Mark done", style=discord.ButtonStyle.success)
        undo_btn = discord.ui.Button(label="↩ Undo", style=discord.ButtonStyle.danger)

        async def go(interaction: discord.Interaction, step: int) -> None:
            await _show_browser_page(interaction, self.owner_id, self.prefix, self.path_id, self.index + step)

        async def go_prev(interaction: discord.Interaction) -> None:
            await go(interaction, -1)

        async def go_next(interaction: discord.Interaction) -> None:
            await go(interaction, 1)

        async def open_done(interaction: discord.Interaction) -> None:
            paths = roadmaps.load_roadmaps()
            topics = paths.get(self.path_id, {}).get("topics", [])
            if not topics:
                await interaction.response.send_message("That path has no topics.", ephemeral=True)
                return
            idx = max(0, min(self.index, len(topics) - 1))
            done = db.get_done_ids(self.owner_id, self.path_id)
            pending = [s for s in topics[idx].get("subtopics", []) if str(s.get("id")) not in done]
            if not pending:
                await interaction.response.send_message("Everything on this page is already done. ✅", ephemeral=True)
                return
            view = MarkSelectView(self.owner_id, self.prefix, self.path_id, idx, pending, do_mark=True)
            embed = discord.Embed(
                title="Select items to mark done",
                description=f"Topic `{topics[idx].get('id')}` — you can pick several.",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message

        async def open_undo(interaction: discord.Interaction) -> None:
            paths = roadmaps.load_roadmaps()
            topics = paths.get(self.path_id, {}).get("topics", [])
            if not topics:
                await interaction.response.send_message("That path has no topics.", ephemeral=True)
                return
            idx = max(0, min(self.index, len(topics) - 1))
            done = db.get_done_ids(self.owner_id, self.path_id)
            finished = [s for s in topics[idx].get("subtopics", []) if str(s.get("id")) in done]
            if not finished:
                await interaction.response.send_message("Nothing on this page is marked done yet.", ephemeral=True)
                return
            view = MarkSelectView(self.owner_id, self.prefix, self.path_id, idx, finished, do_mark=False)
            embed = discord.Embed(
                title="Select items to unmark",
                description=f"Topic `{topics[idx].get('id')}` — you can pick several.",
                color=discord.Color.orange(),
            )
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message

        prev_btn.callback = go_prev
        next_btn.callback = go_next
        done_btn.callback = open_done
        undo_btn.callback = open_undo
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(done_btn)
        self.add_item(undo_btn)


class MarkSelectView(_OwnerView):
    """Multi-select to mark/unmark items on one topic page, with a Back button."""

    def __init__(
        self, owner_id: int, prefix: str, path_id: str, index: int, items: list[dict], do_mark: bool
    ) -> None:
        super().__init__(owner_id, prefix)
        self.path_id = path_id
        self.index = index
        self.do_mark = do_mark
        select = discord.ui.Select(
            placeholder="Pick one or more…",
            min_values=1,
            max_values=min(len(items), 25),
            options=[
                discord.SelectOption(label=f"{s.get('id')} {str(s.get('title', ''))[:80]}", value=str(s.get("id")))
                for s in items[:25]
            ],
        )

        async def apply(interaction: discord.Interaction) -> None:
            # Idempotent: double-clicks / retries can't create dupes (INSERT OR IGNORE).
            if self.do_mark:
                db.mark_many(self.owner_id, self.path_id, list(select.values))
            else:
                db.unmark_many(self.owner_id, self.path_id, list(select.values))
            await _show_browser_page(interaction, self.owner_id, self.prefix, self.path_id, self.index)

        async def back(interaction: discord.Interaction) -> None:
            await _show_browser_page(interaction, self.owner_id, self.prefix, self.path_id, self.index)

        select.callback = apply
        back_btn = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)
        back_btn.callback = back
        self.add_item(select)
        self.add_item(back_btn)


class Roadmap(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()

    @commands.command(name="paths")
    async def paths(self, ctx: commands.Context) -> None:
        """List all available roadmap paths."""
        paths = roadmaps.load_roadmaps()
        if not paths:
            await ctx.send("No roadmap paths found.")
            return
        embed = discord.Embed(title="Available paths", color=discord.Color.blurple())
        for pid, data in paths.items():
            total = roadmaps.total_count(data)
            topics = len(data.get("topics", []))
            desc = str(data.get("description", ""))[:200]
            embed.add_field(
                name=f"{data.get('title', pid)} (`{pid}`)",
                value=f"{desc}\n{topics} topics · {total} items",
                inline=False,
            )
        embed.set_footer(text=f"Use {ctx.prefix}path <name> to view a path.")
        await ctx.send(embed=embed)

    @commands.command(name="path")
    async def path(self, ctx: commands.Context, *, name: str | None = None) -> None:
        """Interactive topic browser. Defaults to your active path."""
        paths = roadmaps.load_roadmaps()
        resolved = _resolve_single_path(paths, name, ctx.author.id)
        if resolved is None:
            await ctx.send(f"Path not found. Use `{ctx.prefix}paths` to list them.")
            return
        pid, data = resolved
        topics = data.get("topics", [])
        if not topics:
            await ctx.send("That path has no topics.")
            return
        embed = _topic_page_embed(pid, data, 0, ctx.author.id)
        view = PathBrowserView(ctx.author.id, ctx.prefix, pid, 0)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    def _resolve_for_topic(self, topic_id: str, path_name: str | None, user_id: int | None = None):
        paths = roadmaps.load_roadmaps()
        if path_name:
            hit = roadmaps.find_path(paths, path_name)
            if hit is None:
                return None, None, "unknown_path"
            pid, data = hit
            if roadmaps.resolve_ids_for_finish(data, topic_id) is None:
                return None, None, "unknown_id"
            return pid, data, "ok"
        # Default to the user's active path when it contains the id.
        if user_id is not None:
            hit = _user_active_path(paths, user_id)
            if hit is not None:
                pid, data = hit
                if roadmaps.resolve_ids_for_finish(data, topic_id) is not None:
                    return pid, data, "ok"
        owners = roadmaps.find_owner_paths(paths, topic_id)
        if not owners:
            return None, None, "unknown_id"
        if len(owners) > 1:
            return None, None, "ambiguous"
        pid = owners[0]
        return pid, paths[pid], "ok"

    @commands.command(name="finish")
    async def finish(self, ctx: commands.Context, topic_id: str, path_name: str | None = None) -> None:
        """Mark a subtopic (or whole parent like '05') done."""
        topic_id = topic_id.strip()
        pid, data, status = self._resolve_for_topic(topic_id, path_name, ctx.author.id)
        if status == "unknown_path":
            await ctx.send(f"Unknown path `{path_name}`. Use `{ctx.prefix}paths`.")
            return
        if status == "unknown_id":
            await ctx.send(f"Unknown ID `{topic_id}`. Check `{ctx.prefix}path` for valid IDs.")
            return
        if status == "ambiguous":
            await ctx.send(f"ID `{topic_id}` exists in multiple paths. Specify one: `{ctx.prefix}finish {topic_id} <path>`.")
            return
        leaf_ids = roadmaps.resolve_ids_for_finish(data, topic_id)
        assert leaf_ids is not None
        new, already = db.mark_many(ctx.author.id, pid, leaf_ids)
        if len(leaf_ids) == 1:
            if already:
                await ctx.send(f"`{topic_id}` is already done. ✅")
            else:
                await ctx.send(f"Marked `{topic_id}` done. ✅")
        else:
            await ctx.send(f"Marked `{topic_id}` done: {new} new, {already} already done. ✅ ({len(leaf_ids)} items)")

    @commands.command(name="unfinish")
    async def unfinish(self, ctx: commands.Context, topic_id: str, path_name: str | None = None) -> None:
        """Unmark a subtopic (or whole parent)."""
        topic_id = topic_id.strip()
        pid, data, status = self._resolve_for_topic(topic_id, path_name, ctx.author.id)
        if status != "ok":
            await ctx.send(f"Unknown ID `{topic_id}`. Check `{ctx.prefix}path` for valid IDs.")
            return
        leaf_ids = roadmaps.resolve_ids_for_finish(data, topic_id)
        assert leaf_ids is not None
        removed = db.unmark_many(ctx.author.id, pid, leaf_ids)
        if removed == 0:
            await ctx.send(f"`{topic_id}` wasn't marked done.")
        elif len(leaf_ids) == 1:
            await ctx.send(f"Unmarked `{topic_id}`.")
        else:
            await ctx.send(f"Unmarked `{topic_id}` ({removed}/{len(leaf_ids)} removed).")

    @commands.command(name="progress")
    async def progress(self, ctx: commands.Context, *, name: str | None = None) -> None:
        """Show progress percent + bar. Defaults to your active path."""
        paths = roadmaps.load_roadmaps()
        if name:
            hit = roadmaps.find_path(paths, name)
            if hit is None:
                await ctx.send(f"Unknown path `{name}`. Use `{ctx.prefix}paths`.")
                return
            targets = [hit]
        elif (active := _user_active_path(paths, ctx.author.id)) is not None:
            targets = [active]
        else:
            targets = [(pid, data) for pid, data in paths.items()]
        if not targets:
            await ctx.send("No roadmap paths found.")
            return
        current, longest = db.get_streaks(ctx.author.id)
        embed = discord.Embed(title="Your progress", color=discord.Color.green())
        for pid, data in targets:
            total = roadmaps.total_count(data)
            done_n = len(db.get_done_ids(ctx.author.id, pid))
            pct = round(done_n / total * 100) if total else 0
            embed.add_field(
                name=str(data.get("title", pid)),
                value=f"`{_bar(pct)}` {pct}% ({done_n}/{total})",
                inline=False,
            )
        embed.set_footer(text=f"🔥 Current streak: {current}d · Longest: {longest}d")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Roadmap(bot))
