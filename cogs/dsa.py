import random

import discord
from discord.ext import commands

import db
import dsa_library as dsa

TIMEOUT = 180
OPTIONS_PER_PAGE = 25
DESC_LIMIT = 4000
FIELD_LIMIT = 1000


def _chunks(text: str, limit: int = DESC_LIMIT) -> list[str]:
    """Split text on line boundaries so embeds never silently truncate."""
    text = text or "—"
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        piece = line + "\n"
        while len(piece) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(piece[:limit])
            piece = piece[limit:]
        if len(cur) + len(piece) > limit and cur:
            parts.append(cur)
            cur = ""
        cur += piece
    if cur.strip():
        parts.append(cur)
    return parts or ["—"]


def _field_chunks(items: list[str], limit: int = FIELD_LIMIT) -> list[str]:
    """Pack list items into field-sized strings (Discord field cap is 1024)."""
    pages, cur = [], ""
    for item in items:
        line = f"• {item}\n"
        if len(cur) + len(line) > limit and cur:
            pages.append(cur)
            cur = ""
        cur += line
    if cur:
        pages.append(cur)
    return pages or ["—"]


def _progress_text(user_id: int, category: str) -> str:
    library = dsa.load_dsa()
    entries = dsa.entries_of(library.get(category, {}))
    done = db.learned_ids(user_id) & {str(e.get("id")) for e in entries if isinstance(e, dict)}
    title = str(library.get(category, {}).get("title", category))
    return f"{title}: {len(done)}/{len(entries)} learned"


class _OwnerView(discord.ui.View):
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
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def _option_label(entry: dict, learned: bool) -> str:
    mark = "✅ " if learned else ""
    return (mark + str(entry.get("title", entry.get("id"))))[:100]


def _option_desc(entry: dict) -> str:
    return f"{entry.get('level', '')} · {str(entry.get('summary', ''))[:80]}"[:100]


async def _show_entry(
    interaction: discord.Interaction,
    owner_id: int,
    prefix: str,
    entry_id: str,
    tab: str = "overview",
    chunk: int = 0,
    return_to: tuple[str, int] | None = None,
) -> None:
    """Render one entry tab from fresh library data (handles JSON edits mid-session)."""
    hit = dsa.get_entry(entry_id)
    if hit is None:
        await interaction.response.edit_message(
            content="That entry is no longer available.", embed=None, view=None
        )
        return
    category, entry = hit
    view = EntryView(owner_id, prefix, entry_id, tab, chunk, return_to)
    embed = view.render()
    await interaction.response.edit_message(content=None, embed=embed, view=view)
    view.message = interaction.message


class CategorySelect(discord.ui.Select):
    def __init__(self, owner_id: int, prefix: str, learned: set[str]) -> None:
        self._owner_id = owner_id
        self._prefix = prefix
        options = []
        for category, data in dsa.load_dsa().items():
            entries = dsa.entries_of(data)
            done = sum(1 for e in entries if str(e.get("id")) in learned)
            options.append(
                discord.SelectOption(
                    label=str(data.get("title", category))[:100],
                    value=category,
                    description=f"{done}/{len(entries)} learned"[:100],
                )
            )
        super().__init__(placeholder="Pick a category…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = EntryListView(self._owner_id, self._prefix, self.values[0], 0)
        await interaction.response.edit_message(embed=view.render(), view=view)
        view.message = interaction.message


class HubView(_OwnerView):
    def __init__(self, owner_id: int, prefix: str) -> None:
        super().__init__(owner_id, prefix)
        self.add_item(CategorySelect(owner_id, prefix, db.learned_ids(owner_id)))

    def render(self) -> discord.Embed:
        embed = discord.Embed(title="DSA Library", color=discord.Color.blurple())
        learned = db.learned_ids(self.owner_id)
        for category, data in dsa.load_dsa().items():
            entries = dsa.entries_of(data)
            done = sum(1 for e in entries if str(e.get("id")) in learned)
            embed.add_field(
                name=str(data.get("title", category)),
                value=f"{str(data.get('description', ''))[:150]}\n{done}/{len(entries)} learned",
                inline=False,
            )
        return embed


class EntryOptionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, prefix: str, category: str, page: int, learned: set[str]) -> None:
        self._owner_id = owner_id
        self._prefix = prefix
        self._category = category
        self._page = page
        entries = dsa.entries_of(dsa.load_dsa().get(category, {}))
        window = entries[page * OPTIONS_PER_PAGE:(page + 1) * OPTIONS_PER_PAGE]
        super().__init__(
            placeholder=f"Pick an entry… (page {page + 1})",
            options=[
                discord.SelectOption(
                    label=_option_label(e, str(e.get("id")) in learned),
                    value=str(e.get("id")),
                    description=_option_desc(e),
                )
                for e in window if isinstance(e, dict)
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _show_entry(
            interaction, self._owner_id, self._prefix, self.values[0],
            return_to=(self._category, self._page),
        )


class EntryListView(_OwnerView):
    def __init__(self, owner_id: int, prefix: str, category: str, page: int = 0) -> None:
        super().__init__(owner_id, prefix)
        self.category = category
        self.page = page
        entries = dsa.entries_of(dsa.load_dsa().get(category, {}))
        self.pages = max(1, (len(entries) + OPTIONS_PER_PAGE - 1) // OPTIONS_PER_PAGE)
        self.page = max(0, min(page, self.pages - 1))
        learned = db.learned_ids(owner_id)
        if entries:
            self.add_item(EntryOptionSelect(owner_id, prefix, category, self.page, learned))
        prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page <= 0)
        next_btn = discord.ui.Button(
            label="Next ▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.pages - 1
        )
        back_btn = discord.ui.Button(label="Categories", style=discord.ButtonStyle.secondary)

        async def go(interaction: discord.Interaction, step: int) -> None:
            view = EntryListView(self.owner_id, self.prefix, self.category, self.page + step)
            await interaction.response.edit_message(embed=view.render(), view=view)
            view.message = interaction.message

        async def back(interaction: discord.Interaction) -> None:
            view = HubView(self.owner_id, self.prefix)
            await interaction.response.edit_message(embed=view.render(), view=view)
            view.message = interaction.message

        prev_btn.callback = lambda inter: go(inter, -1)
        next_btn.callback = lambda inter: go(inter, 1)
        back_btn.callback = back
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(back_btn)

    def render(self) -> discord.Embed:
        library = dsa.load_dsa()
        data = library.get(self.category, {})
        learned = db.learned_ids(self.owner_id)
        entries = dsa.entries_of(data)
        embed = discord.Embed(
            title=str(data.get("title", self.category)), color=discord.Color.blurple()
        )
        window = entries[self.page * OPTIONS_PER_PAGE:(self.page + 1) * OPTIONS_PER_PAGE]
        lines = []
        for e in window:
            mark = "✅" if str(e.get("id")) in learned else "⬜"
            lines.append(f"{mark} `{e.get('id')}` **{e.get('title')}** ({e.get('level')})")
        embed.description = "\n".join(lines)[:DESC_LIMIT] or "No entries."
        done = sum(1 for e in entries if str(e.get("id")) in learned)
        embed.set_footer(text=f"{done}/{len(entries)} learned · page {self.page + 1}/{self.pages}")
        return embed


class RelatedButton(discord.ui.Button):
    def __init__(self, owner_id: int, prefix: str, entry_id: str, return_to: tuple[str, int] | None) -> None:
        hit = dsa.get_entry(entry_id)
        label = str(hit[1].get("title", entry_id))[:80] if hit else entry_id[:80]
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self._owner_id = owner_id
        self._prefix = prefix
        self._entry_id = entry_id
        self._return_to = return_to

    async def callback(self, interaction: discord.Interaction) -> None:
        await _show_entry(interaction, self._owner_id, self._prefix, self._entry_id, return_to=self._return_to)


class EntryView(_OwnerView):
    TABS = ("overview", "how", "pitfalls", "related")
    TAB_LABELS = {"overview": "Overview", "how": "How it works", "pitfalls": "Pitfalls & tips", "related": "Related"}

    def __init__(self, owner_id: int, prefix: str, entry_id: str, tab: str = "overview",
                 chunk: int = 0, return_to: tuple[str, int] | None = None) -> None:
        super().__init__(owner_id, prefix)
        self.entry_id = entry_id
        self.tab = tab if tab in self.TABS else "overview"
        self.chunk = chunk
        self.return_to = return_to
        for key in self.TABS:
            btn = discord.ui.Button(
                label=self.TAB_LABELS[key], style=discord.ButtonStyle.secondary,
                disabled=(key == self.tab),
            )
            btn.callback = self._make_tab_cb(key)
            self.add_item(btn)
        for rid in self._related_ids()[:5]:
            self.add_item(RelatedButton(owner_id, prefix, rid, return_to))
        prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
        next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
        learned = db.is_learned(owner_id, entry_id)
        toggle_btn = discord.ui.Button(
            label="Learned ✅ (tap to unmark)" if learned else "Mark learned",
            style=discord.ButtonStyle.success if not learned else discord.ButtonStyle.secondary,
        )
        back_btn = discord.ui.Button(label="◀ Entries", style=discord.ButtonStyle.secondary)

        async def step(interaction: discord.Interaction, delta: int) -> None:
            chunks = self._tab_chunks()
            new_chunk = max(0, min(self.chunk + delta, len(chunks) - 1))
            view = EntryView(owner_id, prefix, entry_id, self.tab, new_chunk, return_to)
            await interaction.response.edit_message(embed=view.render(), view=view)
            view.message = interaction.message

        async def toggle(interaction: discord.Interaction) -> None:
            db.toggle_learned(owner_id, entry_id)
            view = EntryView(owner_id, prefix, entry_id, self.tab, 0, return_to)
            await interaction.response.edit_message(embed=view.render(), view=view)
            view.message = interaction.message

        async def back(interaction: discord.Interaction) -> None:
            if return_to is not None:
                view: _OwnerView = EntryListView(owner_id, prefix, return_to[0], return_to[1])
                embed = view.render()  # type: ignore[attr-defined]
            else:
                view = HubView(owner_id, prefix)
                embed = view.render()
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message

        prev_btn.callback = lambda inter: step(inter, -1)
        next_btn.callback = lambda inter: step(inter, 1)
        toggle_btn.callback = toggle
        back_btn.callback = back
        # Disable chunk nav when a single chunk exists.
        if len(self._tab_chunks()) <= 1:
            prev_btn.disabled = True
            next_btn.disabled = True
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(toggle_btn)
        self.add_item(back_btn)

    def _make_tab_cb(self, key: str):
        async def go(interaction: discord.Interaction) -> None:
            view = EntryView(self.owner_id, self.prefix, self.entry_id, key, 0, self.return_to)
            await interaction.response.edit_message(embed=view.render(), view=view)
            view.message = interaction.message
        return go

    def _entry(self) -> tuple[str, dict] | None:
        return dsa.get_entry(self.entry_id)

    def _related_ids(self) -> list[str]:
        hit = self._entry()
        if not hit:
            return []
        related = hit[1].get("related", [])
        return [str(r) for r in related if isinstance(related, list)][:5]

    def _tab_chunks(self) -> list[str]:
        hit = self._entry()
        if not hit:
            return ["Entry no longer available."]
        _, entry = hit
        if self.tab == "overview":
            complexity = entry.get("complexity", {})
            text = (
                f"**{entry.get('title')}** · `{entry.get('id')}` · {entry.get('level')}\n\n"
                f"{entry.get('summary', '')}\n\n"
                f"Time: {complexity.get('time', '?')} · Space: {complexity.get('space', '?')}\n"
                f"Tags: {', '.join(str(t) for t in entry.get('tags', []))}"
            )
            return _chunks(text)
        if self.tab == "how":
            example = str(entry.get("example", ""))
            fence = "text" if example.lstrip().startswith("PSEUDOCODE") else "python"
            text = f"{entry.get('how_it_works', '')}\n\n```{fence}\n{example}\n```"
            return _chunks(text)
        if self.tab == "pitfalls":
            text = "## Pitfalls\n" + "\n".join(f"• {p}" for p in entry.get("pitfalls", []))
            text += "\n\n## Tips\n" + "\n".join(f"• {t}" for t in entry.get("tips", []))
            return _chunks(text)
        lines = []
        for rid in entry.get("related", []) if isinstance(entry.get("related"), list) else []:
            hit2 = dsa.get_entry(str(rid))
            if hit2:
                _, rel = hit2
                lines.append(f"• **{rel.get('title')}** (`{rid}`, {rel.get('level')}) — tap the button below")
            else:
                lines.append(f"• `{rid}` (no longer available)")
        return _chunks("## Related entries\n" + "\n".join(lines))

    def render(self) -> discord.Embed:
        hit = self._entry()
        title = str(hit[1].get("title", self.entry_id)) if hit else self.entry_id
        category = hit[0] if hit else ""
        chunks = self._tab_chunks()
        self.chunk = max(0, min(self.chunk, len(chunks) - 1))
        embed = discord.Embed(
            title=f"{title} — {self.TAB_LABELS[self.tab]}",
            description=chunks[self.chunk],
            color=discord.Color.blurple(),
        )
        learned = db.is_learned(self.owner_id, self.entry_id)
        mark = "✅ learned" if learned else "⬜ not learned"
        if category:
            library = dsa.load_dsa()
            entries = dsa.entries_of(library.get(category, {}))
            done = db.learned_ids(self.owner_id) & {str(e.get("id")) for e in entries}
            footer = f"{mark} · {len(done)}/{len(entries)} learned in {category}"
        else:
            footer = mark
        if len(chunks) > 1:
            footer += f" · part {self.chunk + 1}/{len(chunks)}"
        embed.set_footer(text=footer[:2048])
        return embed


class Dsa(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        db.init_db()

    @commands.group(name="dsa", invoke_without_command=True)
    async def dsa(self, ctx: commands.Context) -> None:
        """Interactive DSA reference hub."""
        view = HubView(ctx.author.id, ctx.prefix)
        msg = await ctx.send(embed=view.render(), view=view)
        view.message = msg

    @dsa.command(name="search")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def dsa_search(self, ctx: commands.Context, *, term: str) -> None:
        """Search entries by title, summary and tags. Usage: >dsa search <term>."""
        results = dsa.search_entries(term)
        if not results:
            await ctx.send(f"No entries match `{term}`. Try `>dsa` to browse.")
            return
        learned = db.learned_ids(ctx.author.id)
        options = [
            discord.SelectOption(
                label=_option_label(entry, str(entry.get("id")) in learned),
                value=str(entry.get("id")),
                description=f"{category} · {_option_desc(entry)}"[:100],
            )
            for category, entry in results[:OPTIONS_PER_PAGE]
        ]
        view = SearchView(ctx.author.id, ctx.prefix, options)
        embed = discord.Embed(
            title=f"Search: {term}",
            description=f"Top {len(options)} of {len(results)} matches — pick one to open it.",
            color=discord.Color.blurple(),
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.command(name="tip")
    async def tip(self, ctx: commands.Context) -> None:
        """Show a random pitfall or tip from the library."""
        pool: list[tuple[str, str, str, str]] = []
        for category, entry in dsa.index_by_id().values():
            eid = str(entry.get("id"))
            title = str(entry.get("title", eid))
            for kind, key in (("Pitfall", "pitfalls"), ("Tip", "tips")):
                items = entry.get(key, [])
                if isinstance(items, list):
                    for text in items:
                        pool.append((eid, title, kind, str(text)))
        if not pool:
            await ctx.send("The library is empty right now.")
            return
        eid, title, kind, text = random.choice(pool)
        embed = discord.Embed(
            title=f"💡 {kind} — {title}",
            description=text[:DESC_LIMIT],
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{eid} · use `>dsa` to browse the library")
        await ctx.send(embed=embed)


class SearchView(_OwnerView):
    def __init__(self, owner_id: int, prefix: str, options: list[discord.SelectOption]) -> None:
        super().__init__(owner_id, prefix)
        select = discord.ui.Select(placeholder="Pick an entry…", options=options)

        async def open_entry(interaction: discord.Interaction) -> None:
            await _show_entry(interaction, self.owner_id, self.prefix, select.values[0])

        select.callback = open_entry
        self.add_item(select)


async def setup(bot: commands.Bot) -> None:
    db.init_db()
    await bot.add_cog(Dsa(bot))
