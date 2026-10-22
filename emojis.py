"""Single source of truth for rank, platform and streak emojis.

No emoji strings for these three categories anywhere else in the codebase;
everything goes through the helpers below. Set USE_CUSTOM_EMOJIS=false in
.env to fall back to plain text (useful where custom emojis don't render).
"""
import os

from dotenv import load_dotenv

load_dotenv()

USE_CUSTOM_EMOJIS = os.getenv("USE_CUSTOM_EMOJIS", "true").strip().lower() in ("1", "true", "yes", "on")

RANK_EMOJIS = {
    "acolyte": "<:acolyte:1559491379913039922>",
    "sentinel": "<:sentinel:1559492393852473424>",
    "warden": "<:warden:1559491707932774471>",
    "centurion": "<:centurion:1559492347576717353>",
    "archon": "<:archon:1559492067150012456>",
    "titan": "<:titan:1559492244959006790>",
}

RANK_FALLBACKS = {
    "acolyte": "○",
    "sentinel": "◔",
    "warden": "◑",
    "centurion": "◕",
    "archon": "⬢",
    "titan": "★",
}

PLATFORM_EMOJIS = {
    "codeforces": "<:cf:1559216598038876250>",
    "leetcode": "<:lc:1559216834090238072>",
    "atcoder": "<:atcoder:1559217005216742828>",
}

PLATFORM_FALLBACKS = {
    "codeforces": "[CF]",
    "leetcode": "[LC]",
    "atcoder": "[AC]",
}

STREAK_EMOJI = "<:streak:1559216255498452992>"
STREAK_FALLBACK = "streak"

PODIUM_MEDALS = ["🥇", "🥈", "🥉"]
PODIUM_FALLBACKS = ["1st", "2nd", "3rd"]


def rank_emoji(rank_name: str) -> str:
    """Custom rank emoji, or a unicode symbol per rank when disabled/unknown."""
    key = (rank_name or "").strip().lower()
    if USE_CUSTOM_EMOJIS:
        return RANK_EMOJIS.get(key, RANK_FALLBACKS.get(key, f"[{rank_name}]"))
    return RANK_FALLBACKS.get(key, f"[{rank_name}]")


def platform_emoji(platform_id: str) -> str:
    """Custom platform emoji, or a plain-text tag when disabled/unknown."""
    key = (platform_id or "").strip().lower()
    if USE_CUSTOM_EMOJIS:
        return PLATFORM_EMOJIS.get(key, f"[{key}]")
    return PLATFORM_FALLBACKS.get(key, f"[{key}]")


def podium_medal(position: int) -> str:
    """Medal for a 0-based podium position, or plain text when disabled."""
    medals = PODIUM_MEDALS if USE_CUSTOM_EMOJIS else PODIUM_FALLBACKS
    if 0 <= position < len(medals):
        return medals[position]
    return f"#{position + 1}"


def __getattr__(name: str) -> str:
    # STREAK stays a live lookup so USE_CUSTOM_EMOJIS toggles take effect
    # without re-importing. Always access as emojis.STREAK, never from-import.
    if name == "STREAK":
        return STREAK_EMOJI if USE_CUSTOM_EMOJIS else STREAK_FALLBACK
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
