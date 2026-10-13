"""Privacy policy constants. No bot or database imports here on purpose,
so any module (and tests) can use these safely."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

load_dotenv()

POLICY_VERSION = "2026-10-1"
CONTACT = os.getenv("CONTACT", "")
PRIVACY_URL = os.getenv("PRIVACY_URL", "")
MIN_ACCOUNT_AGE_DAYS = int(os.getenv("MIN_ACCOUNT_AGE_DAYS", "0") or 0)

PRIVACY_TEXT = """Noa stores only what it needs to track your learning:

• Your Discord ID and username, learning path, experience, weekly hours, goal and bio.
• Which roadmap topics and DSA entries you marked done, and when.
• Linked coding accounts (platform + handle) and their synced public stats.
• Daily activity used for streaks and leaderboards.

Public stats come from Codeforces, LeetCode and AtCoder. Nothing is ever
sold or shared. Run >deleteme to erase everything, or >export for a copy
of your data."""


def contact_line() -> str:
    if CONTACT:
        return f"Questions? Contact: {CONTACT}"
    return "Questions? No contact is configured for this bot yet."


def full_policy_text() -> str:
    text = PRIVACY_TEXT
    if PRIVACY_URL:
        text += f"\n\nFull policy: {PRIVACY_URL}"
    return text + f"\n\n{contact_line()}"


def build_consent_embed(prefix: str) -> discord.Embed:
    """Consent prompt shown before onboarding. Includes the age checkbox line."""
    embed = discord.Embed(title="Before you join Noa…", color=discord.Color.blurple())
    embed.description = (
        full_policy_text()
        + f"\n\n☐ I meet Discord's minimum age requirement."
          f"\n\nPress **I agree** to create your account, or **Decline** to stop here."
          f"\nYou can review this anytime with `{prefix}privacy`."
    )
    embed.set_footer(text=f"Policy version {POLICY_VERSION}")
    return embed


def meets_min_age(created_at, min_days: int, now=None) -> bool:
    """Pure check: is a Discord account at least min_days old? Off when 0."""
    if not min_days:
        return True
    now = now or datetime.now(timezone.utc)
    created = created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).days >= min_days
