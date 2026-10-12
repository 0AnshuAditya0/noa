import discord
from discord.ext import commands

import db
import roadmaps

BAR_LEN = 12


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
        """Show topics in a path. Defaults to your active path."""
        paths = roadmaps.load_roadmaps()
        resolved = _resolve_single_path(paths, name, ctx.author.id)
        if resolved is None:
            await ctx.send(f"Path not found. Use `{ctx.prefix}paths` to list them.")
            return
        pid, data = resolved
        done = db.get_done_ids(ctx.author.id, pid)
        embed = discord.Embed(
            title=str(data.get("title", pid)),
            description=str(data.get("description", ""))[:400],
            color=discord.Color.blurple(),
        )
        lines: list[str] = []
        for topic in data.get("topics", []):
            subs = topic.get("subtopics", [])
            done_n = sum(1 for s in subs if str(s["id"]) in done)
            mark = "✅" if subs and done_n == len(subs) else f"{done_n}/{len(subs)}"
            lines.append(f"`{topic['id']}` **{topic['title']}** — {mark}")
        chunk = "\n".join(lines)
        if len(chunk) > 4000:
            chunk = chunk[:4000] + "\n…"
        embed.description = (embed.description + "\n\n" + chunk) if embed.description else chunk
        total = roadmaps.total_count(data)
        pct = round(len(done) / total * 100) if total else 0
        embed.set_footer(text=f"{_bar(pct)} {pct}% ({len(done)}/{total}) · {ctx.prefix}finish <id> to track")
        await ctx.send(embed=embed)

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
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Roadmap(bot))
