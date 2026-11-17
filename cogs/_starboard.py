"""Validation and comparison for configurable starboard reactions."""

from __future__ import annotations

import re

import discord


EMOJI_CHOICES = {"⭐": "Star", "🌟": "Glowing star", "✨": "Sparkles", "🔥": "Fire", "❤️": "Heart", "👍": "Thumbs up", "🏆": "Trophy"}
_EMOJI_BASE = r"[\u203c\u2049\u2122\u2139\u2194-\u21ff\u2300-\u23ff\u24c2\u25aa-\u27ff\u2934\u2935\u2b00-\u2bff\u3030\u303d\u3297\u3299\U0001f000-\U0001faff]"
_EMOJI_PART = _EMOJI_BASE + r"[\ufe0e\ufe0f]?[\U0001f3fb-\U0001f3ff]?"
_UNICODE_EMOJI = re.compile(r"(?:[0-9#*]\ufe0f?\u20e3|[\U0001f1e6-\U0001f1ff]{2}|" + _EMOJI_PART + r"(?:\u200d" + _EMOJI_PART + r")*)")


def emoji_key(value: str) -> int | str:
    emoji = discord.PartialEmoji.from_str(value)
    return emoji.id if emoji.id is not None else emoji.name.replace("\ufe0f", "").replace("\ufe0e", "")


def validate_emoji(value: str, guild: discord.Guild) -> str | None:
    if re.fullmatch(r"<a?:[A-Za-z0-9_]{2,32}:\d+>", value):
        emoji = guild.get_emoji(discord.PartialEmoji.from_str(value).id)
        if emoji is None or not emoji.is_usable():
            return "Choose a custom emoji from this server that the bot can use."
        return None
    if not _UNICODE_EMOJI.fullmatch(value):
        return "Enter one emoji, such as ⭐ or 🔥, or paste a custom server emoji."
    return None
