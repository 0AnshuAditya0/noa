"""Help discovery: pure helpers plus live consistency against a real cog set."""
import asyncio
from types import SimpleNamespace

import discord
from discord.ext import commands

import db
from cogs import help as help_mod
from cogs.help import (
    COMMAND_META,
    category_of,
    command_examples,
    command_usage,
    cooldown_text,
    ordered_categories,
    permission_text,
    suggest_names,
    visible_commands,
)


def _fake_cmd(qname, cog_name="Ping", **kw):
    cog_cls = type(cog_name, (), {})
    cmd = SimpleNamespace(
        qualified_name=qname,
        name=qname.split()[-1],
        aliases=kw.get("aliases", []),
        brief=kw.get("brief", ""),
        help=kw.get("help", ""),
        usage=kw.get("usage", None),
        signature=kw.get("signature", ""),
        callback=kw.get("callback", None),
        hidden=kw.get("hidden", False),
        cog=cog_cls(),
    )
    if "buckets" in kw:
        cmd._buckets = kw["buckets"]
    return cmd


def test_suggest_names_close_match():
    got = suggest_names("finsh", ["finish", "unfinish", "path", "ping"])
    assert "finish" in got
    assert suggest_names("zzzqqq", ["finish", "path"]) == []


def test_command_usage_prefers_override_then_signature():
    cmd = _fake_cmd("finish", signature="<id> [path]")
    assert help_mod.COMMAND_META["finish"]["usage"] == "<id> [path]"
    assert command_usage(">", cmd) == ">finish <id> [path]"
    bare = _fake_cmd("ping", signature="")
    assert command_usage(">", bare) == ">ping"


def test_examples_come_from_registry():
    cmd = _fake_cmd("finish")
    assert command_examples(cmd) == ["finish 05.2", "finish 05"]
    assert command_examples(_fake_cmd("nosuchcmd")) == []


def test_cooldown_text_live_and_missing():
    mapping = commands.CooldownMapping.from_cooldown(1, 5, commands.BucketType.user)
    cmd = _fake_cmd("ping", buckets=mapping)
    assert cooldown_text(cmd) == "1 per 5s per user"
    assert cooldown_text(_fake_cmd("ping")) == "none"


def test_permission_text():
    assert permission_text(_fake_cmd("setchannel")) == "Manage Server"
    assert permission_text(_fake_cmd("ping")) == "—"


def test_category_mapping_and_order():
    assert category_of(_fake_cmd("finish", cog_name="Roadmap")) == "Roadmap"
    assert category_of(_fake_cmd("weird", cog_name="Nope")) == "General"
    cmds = [_fake_cmd("tip", cog_name="Dsa"), _fake_cmd("ping", cog_name="Ping")]
    assert ordered_categories(cmds)[0] in {"DSA Library", "General"}


def test_admin_hidden_unless_owner():
    admin_cmd = _fake_cmd("admin stats", cog_name="Admin")
    bot = SimpleNamespace(commands=[admin_cmd, _fake_cmd("ping", cog_name="Ping")])
    assert admin_cmd not in visible_commands(bot, is_owner=False)
    assert admin_cmd in visible_commands(bot, is_owner=True)
    hidden = _fake_cmd("secret", hidden=True)
    bot2 = SimpleNamespace(commands=[hidden])
    assert visible_commands(bot2, is_owner=True) == []


def _load_all_cogs(bot):
    import cogs.account
    import cogs.admin
    import cogs.announce
    import cogs.dsa
    import cogs.help
    import cogs.ping
    import cogs.platforms
    import cogs.privacy
    import cogs.profile
    import cogs.roadmap
    import cogs.status

    async def go():
        for mod in (cogs.account, cogs.admin, cogs.announce, cogs.dsa,
                    cogs.help, cogs.ping, cogs.platforms, cogs.privacy, cogs.profile,
                    cogs.roadmap, cogs.status):
            try:
                await bot.add_cog(mod.__dict__[next(
                    k for k, v in mod.__dict__.items()
                    if isinstance(v, type) and issubclass(v, commands.Cog) and v is not commands.Cog
                )](bot))
            except Exception:
                pass
        for cog in list(bot.cogs.values()):
            for attr in ("announce_loop", "status_loop", "platform_sync_loop",
                         "host_reminder_loop", "backup_loop"):
                loop = getattr(cog, attr, None)
                if loop is not None:
                    try:
                        loop.cancel()
                    except Exception:
                        pass

    asyncio.run(go())


def test_every_visible_command_has_metadata():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix=">", intents=intents, help_command=None)
    _load_all_cogs(bot)
    cmds = visible_commands(bot, is_owner=True)
    assert len(cmds) >= 25, [c.qualified_name for c in cmds]
    missing = [c.qualified_name for c in cmds if c.qualified_name not in COMMAND_META]
    assert missing == [], f"commands without metadata: {missing}"
    # No orphaned metadata either.
    live = {c.qualified_name for c in cmds}
    orphans = [k for k in COMMAND_META if k not in live]
    assert orphans == [], f"stale metadata entries: {orphans}"


def test_embeds_build_without_error():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix=">", intents=intents, help_command=None)
    _load_all_cogs(bot)
    cmds = visible_commands(bot, is_owner=False)
    cat = help_mod.category_embed(bot, ">", False, "Roadmap")
    assert "finish" in cat.fields[0].name
    some = next(c for c in cmds if c.qualified_name == "finish")
    det = help_mod.command_embed(">", some)
    assert det.title == ">finish"
    assert "Usage" in [f.name for f in det.fields]
