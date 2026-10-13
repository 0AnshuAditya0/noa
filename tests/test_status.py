"""Status rotation: placeholder rendering from the top-of-file list."""
import discord

from cogs.status import STATUSES, render_statuses


def test_status_list_has_five_entries():
    assert len(STATUSES) == 5


def test_placeholders_filled():
    rendered = render_statuses(">", 42)
    values = [v for _, v in rendered]
    assert "Watching 42 learners grind" in values
    assert "Playing >help" in values


def test_custom_prefix_used_not_hardcoded():
    rendered = render_statuses("!", 7)
    assert any(v == "Playing !help" for _, v in rendered)
    assert not any(">help" in v for _, v in rendered)


def test_learner_count_used():
    rendered = render_statuses(">", 1234)
    assert any("1234" in v for _, v in rendered)


def test_activity_types_valid():
    for kind, text in render_statuses(">", 1):
        assert isinstance(kind, discord.ActivityType)
        assert isinstance(text, str) and text
        assert "{" not in text and "}" not in text
