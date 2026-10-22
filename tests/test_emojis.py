"""Emoji single source of truth: exact strings, fallbacks, helpers."""
import emojis


def test_custom_rank_emoji_exact_strings():
    assert emojis.rank_emoji("acolyte") == "<:acolyte:1559491379913039922>"
    assert emojis.rank_emoji("sentinel") == "<:sentinel:1559492393852473424>"
    assert emojis.rank_emoji("warden") == "<:warden:1559491707932774471>"
    assert emojis.rank_emoji("centurion") == "<:centurion:1559492347576717353>"
    assert emojis.rank_emoji("archon") == "<:archon:1559492067150012456>"
    assert emojis.rank_emoji("titan") == "<:titan:1559492244959006790>"


def test_custom_platform_emoji_exact_strings():
    assert emojis.platform_emoji("leetcode") == "<:lc:1559216834090238072>"
    assert emojis.platform_emoji("codeforces") == "<:cf:1559216598038876250>"
    assert emojis.platform_emoji("atcoder") == "<:atcoder:1559217005216742828>"


def test_custom_streak_emoji_exact_string():
    assert emojis.STREAK == "<:streak:1559216255498452992>"


def test_helpers_are_case_insensitive():
    assert emojis.rank_emoji("Titan") == emojis.rank_emoji("titan")
    assert emojis.platform_emoji("CodeForces") == emojis.platform_emoji("codeforces")


def test_unknown_ids_fall_back_to_brackets():
    assert emojis.platform_emoji("hackerrank") == "[hackerrank]"
    assert emojis.rank_emoji("peasant") == "[peasant]"
    assert emojis.podium_medal(9) == "#10"


def test_podium_medals():
    assert [emojis.podium_medal(i) for i in range(3)] == ["🥇", "🥈", "🥉"]


def test_fallback_mode(monkeypatch):
    monkeypatch.setattr(emojis, "USE_CUSTOM_EMOJIS", False)
    assert emojis.rank_emoji("titan") == "★"
    assert emojis.rank_emoji("acolyte") == "○"
    assert emojis.platform_emoji("codeforces") == "[CF]"
    assert emojis.platform_emoji("leetcode") == "[LC]"
    assert emojis.platform_emoji("atcoder") == "[AC]"
    assert emojis.STREAK == "streak"
    assert emojis.podium_medal(0) == "1st"


def test_fallback_covers_every_rank(monkeypatch):
    monkeypatch.setattr(emojis, "USE_CUSTOM_EMOJIS", False)
    for name in ("acolyte", "sentinel", "warden", "centurion", "archon", "titan"):
        symbol = emojis.rank_emoji(name)
        assert symbol and not symbol.startswith("<:"), name
