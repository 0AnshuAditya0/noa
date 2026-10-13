import difflib
import os

import discord
from discord.ext import commands

import db

TIMEOUT = 120
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)

CATEGORIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Account", "Join, personalize and manage your account.", ("Account",)),
    ("Roadmap", "Follow a learning path and track topics.", ("Roadmap",)),
    ("Social", "Profiles, streaks and leaderboards.", ("Profile",)),
    ("Platforms", "Link Codeforces, LeetCode and AtCoder.", ("Platforms",)),
    ("DSA Library", "Patterns, algorithms and contest tips.", ("Dsa",)),
    ("Privacy", "See, export or erase your data.", ("Privacy",)),
    ("Server admin", "Weekly podium announcements.", ("Announce",)),
    ("General", "Bot info and this help.", ("Ping", "Help", "Status")),
]

# Per-command metadata. The command list itself always comes from the live
# bot registry; this only adds hand-written briefs, examples and permissions.
COMMAND_META: dict[str, dict] = {
    "register": {"examples": ["register"]},
    "switchpath": {"examples": ["switchpath"]},
    "agree": {"examples": ["agree"]},
    "bio": {"usage": "[text]", "examples": ["bio Likes graphs and coffee", "bio clear"]},
    "paths": {"examples": ["paths"]},
    "path": {"usage": "[name]", "examples": ["path", "path swe"]},
    "finish": {"usage": "<id> [path]", "examples": ["finish 05.2", "finish 05"]},
    "unfinish": {"usage": "<id> [path]", "examples": ["unfinish 05.2"]},
    "progress": {"usage": "[path]", "examples": ["progress"]},
    "profile": {"usage": "[@user]", "examples": ["profile", "profile @Noa"]},
    "leaderboard": {"examples": ["leaderboard"]},
    "leaderboard podium": {"examples": ["leaderboard podium"]},
    "link": {"usage": "<platform> <handle>", "examples": ["link codeforces tourist"]},
    "unlink": {"usage": "<platform>", "examples": ["unlink codeforces"]},
    "accounts": {"examples": ["accounts"]},
    "sync": {"examples": ["sync"]},
    "dsa": {"examples": ["dsa"]},
    "dsa search": {"usage": "<term>", "examples": ["dsa search sliding window"]},
    "tip": {"examples": ["tip"]},
    "privacy": {"examples": ["privacy"]},
    "deleteme": {"examples": ["deleteme"]},
    "export": {"examples": ["export"]},
    "setchannel": {"usage": "[#channel]", "examples": ["setchannel", "setchannel #general"],
                   "permission": "Manage Server"},
    "unsetchannel": {"examples": ["unsetchannel"], "permission": "Manage Server"},
    "admin": {"examples": ["admin stats"], "permission": "Bot owner"},
    "admin stats": {"examples": ["admin stats"], "permission": "Bot owner"},
    "admin sync": {"usage": "<@user>", "examples": ["admin sync @Noa"], "permission": "Bot owner"},
    "admin announce-now": {"examples": ["admin announce-now"], "permission": "Bot owner"},
    "ping": {"examples": ["ping"]},
    "about": {"examples": ["about"]},
    "help": {"usage": "[category|command]", "examples": ["help", "help Roadmap", "help finish"]},
}


def iter_bot_commands(bot: commands.Bot):
    """Yield (command, qualified_name) for top-level commands and subcommands."""
    for cmd in bot.commands:
        yield cmd, cmd.qualified_name
        if isinstance(cmd, commands.Group):
            for sub in cmd.commands:
                yield sub, sub.qualified_name


def visible_commands(bot: commands.Bot, is_owner: bool) -> list:
    """All commands the caller may see. Admin stays hidden unless owner."""
    out = []
    for cmd, _ in iter_bot_commands(bot):
        if cmd.hidden:
            continue
        cog = cmd.cog
        if cog is not None and cog.__class__.__name__ == "Admin" and not is_owner:
            continue
        out.append(cmd)
    return out


def category_of(cmd) -> str:
    cog = cmd.cog
    name = cog.__class__.__name__ if cog is not None else ""
    for label, _desc, cog_names in CATEGORIES:
        if name in cog_names:
            return label
    return "General"


def ordered_categories(cmds: list) -> list[str]:
    present = {category_of(c) for c in cmds}
    ordered = [label for label, _, _ in CATEGORIES if label in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def meta_for(cmd) -> dict:
    return COMMAND_META.get(cmd.qualified_name, {})


def command_brief(cmd) -> str:
    meta = meta_for(cmd)
    if meta.get("brief"):
        return str(meta["brief"])
    if cmd.brief:
        return str(cmd.brief)
    text = cmd.help or (cmd.callback.__doc__ if cmd.callback else "") or ""
    return text.strip().splitlines()[0] if text.strip() else "No description."


def command_usage(prefix: str, cmd) -> str:
    meta = meta_for(cmd)
    usage = meta.get("usage") or cmd.usage or cmd.signature or ""
    usage = str(usage).strip()
    base = f"{prefix}{cmd.qualified_name}"
    return f"{base} {usage}".rstrip() if usage else base


def command_examples(cmd) -> list[str]:
    meta = meta_for(cmd)
    examples = meta.get("examples", [])
    return [str(e) for e in examples]


def cooldown_text(cmd) -> str:
    try:
        cd = getattr(getattr(cmd, "_buckets", None), "_cooldown", None)
        if cd is None:
            return "none"
        return f"{cd.rate} per {cd.per:g}s per user"
    except Exception:
        return "none"


def permission_text(cmd) -> str:
    meta = meta_for(cmd)
    if meta.get("permission"):
        return str(meta["permission"])
    return "—"


def suggest_names(query: str, candidates: list[str]) -> list[str]:
    """Did-you-mean matches for unknown help topics."""
    return difflib.get_close_matches(query, candidates, n=3, cutoff=0.6)


class HelpNavView(discord.ui.View):
    """Category select to jump around help. Invoker-only, 120s timeout."""

    def __init__(self, bot: commands.Bot, owner_id: int, prefix: str, is_owner: bool) -> None:
        super().__init__(timeout=TIMEOUT)
        self.bot = bot
        self.owner_id = owner_id
        self.prefix = prefix
        self.is_owner = is_owner
        self.message: discord.Message | None = None
        cmds = visible_commands(bot, is_owner)
        options = [
            discord.SelectOption(label=label, description=desc[:100], value=label)
            for label, desc, _ in CATEGORIES
            if label in {category_of(c) for c in cmds}
        ][:25]
        select = discord.ui.Select(placeholder="Jump to a category…", options=options)

        async def jump(interaction: discord.Interaction) -> None:
            embed = category_embed(self.bot, self.prefix, self.is_owner, select.values[0])
            await interaction.response.edit_message(embed=embed, view=self)
            self.message = interaction.message

        select.callback = jump
        self.add_item(select)

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


def overview_embed(bot: commands.Bot, prefix: str, is_owner: bool, registered: bool) -> discord.Embed:
    cmds = visible_commands(bot, is_owner)
    desc = "Every command and what it's for."
    if not registered:
        desc = f"Run `{prefix}register` first — most commands need an account.\n\n" + desc
    embed = discord.Embed(title="Noa commands", description=desc, color=discord.Color.blurple())
    for label in ordered_categories(cmds):
        names = sorted(f"`{prefix}{c.qualified_name}`" for c in cmds if category_of(c) == label)
        embed.add_field(name=label, value=" · ".join(names)[:1024] or "—", inline=False)
    return embed


def category_embed(bot: commands.Bot, prefix: str, is_owner: bool, label: str) -> discord.Embed:
    cmds = [c for c in visible_commands(bot, is_owner) if category_of(c) == label]
    blurb = next((d for lab, d, _ in CATEGORIES if lab == label), "")
    embed = discord.Embed(title=f"{label} commands", description=blurb, color=discord.Color.blurple())
    for cmd in sorted(cmds, key=lambda c: c.qualified_name):
        embed.add_field(
            name=f"{prefix}{cmd.qualified_name}",
            value=command_brief(cmd)[:1024],
            inline=False,
        )
    return embed


def command_embed(prefix: str, cmd) -> discord.Embed:
    embed = discord.Embed(
        title=f"{prefix}{cmd.qualified_name}",
        description=command_brief(cmd)[:2048],
        color=discord.Color.blurple(),
    )
    long_help = (cmd.help or "").strip()
    if long_help and long_help not in embed.description:
        embed.add_field(name="About", value=long_help[:1024], inline=False)
    embed.add_field(name="Usage", value=f"`{command_usage(prefix, cmd)}`", inline=False)
    examples = command_examples(cmd)
    embed.add_field(
        name="Examples",
        value="\n".join(f"`{prefix}{e}`" for e in examples)[:1024] if examples else "—",
        inline=False,
    )
    aliases = ", ".join(f"`{a}`" for a in (cmd.aliases or [])) or "—"
    embed.add_field(name="Aliases", value=aliases[:1024], inline=True)
    embed.add_field(name="Cooldown", value=cooldown_text(cmd)[:256], inline=True)
    embed.add_field(name="Permission", value=permission_text(cmd)[:256], inline=True)
    return embed


def did_you_mean_embed(query: str, candidates: list[str]) -> discord.Embed:
    suggestions = suggest_names(query, candidates)
    embed = discord.Embed(title="No such topic", color=discord.Color.orange())
    if suggestions:
        quoted = ", ".join(f"`{s}`" for s in suggestions)
        embed.description = f"Did you mean {quoted}?"
    else:
        embed.description = "No matching category or command. Browse with the menu below."
    return embed


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context, *, query: str | None = None) -> None:
        """Show help: overview, categories and per-command details."""
        is_owner = bool(OWNER_ID) and ctx.author.id == OWNER_ID
        cmds = visible_commands(self.bot, is_owner)
        registered = db.is_registered(ctx.author.id)
        if query is None:
            view = HelpNavView(self.bot, ctx.author.id, ctx.prefix, is_owner)
            msg = await ctx.send(embed=overview_embed(self.bot, ctx.prefix, is_owner, registered), view=view)
            view.message = msg
            return
        view = HelpNavView(self.bot, ctx.author.id, ctx.prefix, is_owner)
        want = query.strip()
        for label, _, _ in CATEGORIES:
            if label.lower() == want.lower() and label in {category_of(c) for c in cmds}:
                msg = await ctx.send(embed=category_embed(self.bot, ctx.prefix, is_owner, label), view=view)
                view.message = msg
                return
        lowered = want.lower()
        for cmd in cmds:
            names = {cmd.qualified_name.lower(), cmd.name.lower()} | {a.lower() for a in (cmd.aliases or [])}
            if lowered in names:
                msg = await ctx.send(embed=command_embed(ctx.prefix, cmd), view=view)
                view.message = msg
                return
        candidates = sorted({c.qualified_name for c in cmds} | {label for label, _, _ in CATEGORIES})
        msg = await ctx.send(embed=did_you_mean_embed(want, candidates), view=view)
        view.message = msg


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
