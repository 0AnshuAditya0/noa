"""Profile polish: progress bars, rank tiers, avatar, compact overview."""
import discord
import pytest

import db
from cogs.profile import (
    _PROFILE_CACHE,
    KNOWN_PLATFORMS,
    ProfileView,
    progress_bar,
    tier_color,
)


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    _PROFILE_CACHE.clear()
    yield
    _PROFILE_CACHE.clear()


def test_progress_bar_edges():
    assert progress_bar(0, 0) == "░" * 10
    assert progress_bar(5, 0) == "░" * 10
    assert progress_bar(10, 10) == "█" * 10
    assert progress_bar(0, 10) == "░" * 10
    assert progress_bar(1, 2) == "█" * 5 + "░" * 5
    assert progress_bar(3, 4, width=8) == "█" * 6 + "░" * 2
    assert len(progress_bar(7, 9, width=12)) == 12


def test_tier_colors():
    assert tier_color(None) == discord.Color.dark_grey()
    assert tier_color(1) == discord.Color.gold()
    assert tier_color(3) == discord.Color.gold()
    assert tier_color(4) == discord.Color.light_grey()
    assert tier_color(10) == discord.Color.light_grey()
    assert tier_color(11) == discord.Color.blurple()
    assert tier_color(500) == discord.Color.blurple()


def _render_overview(uid=81, bio="Hi"):
    db.create_user(uid, f"u{uid}", active_path="swe")
    if bio is not None:
        db.update_user(uid, bio=bio)
    view = ProfileView(uid, ">", None, uid, f"user{uid}", avatar_url="https://cdn.example/a.png")
    return view.render()


def test_overview_compact_and_shows_bio():
    embed = _render_overview()
    assert "Hi" in (embed.description or "")
    assert "user81" in (embed.description or "")
    lines = (embed.description or "").splitlines()
    lines += [line for f in embed.fields for line in f.value.splitlines()]
    assert len(lines) <= 10, lines
    names = [f.name for f in embed.fields]
    assert names == ["This week", "Coding streak", "Roadmap", "DSA", "Badges"]


def test_overview_uses_bars_and_caps_badges():
    db.create_user(82, "u82", active_path="swe")
    view = ProfileView(82, ">", None, 82, "user82")
    embed = view.render()
    by_name = {f.name: f.value for f in embed.fields}
    assert "█" in by_name["Roadmap"] or "░" in by_name["Roadmap"]
    assert "█" in by_name["DSA"] or "░" in by_name["DSA"]
    assert by_name["Badges"].count("·") <= 2  # at most 3 badges


def test_thumbnail_and_grey_when_unranked():
    embed = _render_overview()
    assert embed.thumbnail.url == "https://cdn.example/a.png"
    assert embed.color == discord.Color.dark_grey()


def test_platforms_tab_keeps_all_states():
    from platforms import ADAPTERS

    db.create_user(83, "u83", active_path="swe")
    db.create_pending_link(83, "codeforces", "h83", "t")
    view = ProfileView(83, ">", None, 83, "user83", tab="platforms")
    embed = view.render()
    field = next(f for f in embed.fields if f.name == "Coding Platforms")
    assert "linked, not verified yet" in field.value
    assert "Total:" in field.value
    without_adapter = [label for plat, label in KNOWN_PLATFORMS if plat not in ADAPTERS]
    for label in without_adapter:
        assert "coming soon" in field.value


def test_roadmap_and_dsa_tabs_keep_info():
    db.create_user(84, "u84", active_path="swe")
    db.mark_done(84, "swe", "05.2")
    db.mark_learned(84, "pat.sliding-window")
    roadmap = ProfileView(84, ">", None, 84, "user84", tab="roadmap").render()
    names = [f.name for f in roadmap.fields]
    assert names == ["Active path", "Experience", "Hours/week", "Goal", "Registered",
                     "Items completed", "Roadmap streak"]
    dsa = ProfileView(84, ">", None, 84, "user84", tab="dsa").render()
    assert "1/" in dsa.fields[0].value  # learned count kept
