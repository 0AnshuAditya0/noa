"""Ping latency helpers: formatting, thresholds, constants."""
import math

import discord
from discord.ext import commands

from cogs.ping import PING_GREEN_MS, PING_YELLOW_MS, VERSION, format_ms, latency_color


def test_format_ms_whole_numbers():
    assert format_ms(12.4) == "12 ms"
    assert format_ms(12.6) == "13 ms"
    assert format_ms(0) == "0 ms"


def test_format_ms_missing_readings():
    assert format_ms(None) == "n/a"
    assert format_ms(float("inf")) == "n/a"
    assert format_ms(float("nan")) == "n/a"


def test_threshold_constants_sane():
    assert 0 < PING_GREEN_MS < PING_YELLOW_MS


def test_latency_color_green_yellow_red():
    assert latency_color(10, 20, 30) == discord.Color.green()
    assert latency_color(10, 200, 30) == discord.Color.yellow()
    assert latency_color(10, 200, 900) == discord.Color.red()


def test_latency_color_boundary():
    assert latency_color(PING_GREEN_MS - 1) == discord.Color.green()
    assert latency_color(PING_GREEN_MS) == discord.Color.green()
    assert latency_color(PING_YELLOW_MS) == discord.Color.yellow()
    assert latency_color(PING_YELLOW_MS + 1) == discord.Color.red()


def test_latency_color_ignores_missing():
    assert latency_color(None, float("nan"), 10) == discord.Color.green()
    assert latency_color(None, None) == discord.Color.dark_grey()


def test_version_present():
    assert isinstance(VERSION, str) and len(VERSION.split(".")) >= 2


def test_ping_has_per_user_cooldown_and_about_exists():
    from cogs.ping import Ping

    assert Ping.ping._buckets._cooldown.per == 5
    assert isinstance(Ping.about, commands.Command)
    assert Ping.about.name == "about"
