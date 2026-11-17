"""Shared welcome/farewell templates, including legacy single-message settings."""

from __future__ import annotations

import random
import re
import string

import discord

import database


TEMPLATE_FIELDS = {"user", "username", "server", "member_count"}
TEMPLATE_HELP = (
    "`@(user)` mentions the member · `@(username)` is their display name\n"
    "`@(server)` is the server name · `@(member_count)` is the member count\n"
    "The existing `{user}`, `{username}`, `{server}`, and `{member_count}` also work."
)
MAX_MESSAGE_TEMPLATES = 25


def _normalize_template(template: str) -> str:
    return re.sub(r"@\(([^()]*)\)", lambda match: "{" + match[1] + "}", template)


def validate_template(template: str) -> str | None:
    if not template.strip():
        return "Enter a message before saving."
    if len(template) > 2000:
        return "Messages can contain at most 2,000 characters."
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(_normalize_template(template)):
            if field_name is not None and field_name not in TEMPLATE_FIELDS:
                return f"Unsupported placeholder `{field_name}`. Use user, username, server, or member_count."
            if format_spec or conversion:
                return "Use simple placeholders such as `@(user)` or `{user}`."
    except ValueError:
        return "The template contains unmatched braces. Use `{{` and `}}` for literal braces."
    return None


def format_message(template: str, member: discord.Member) -> str:
    try:
        return _normalize_template(template).format(
            user=member.mention,
            username=member.display_name,
            server=member.guild.name,
            member_count=member.guild.member_count or len(member.guild.members),
        )[:2000]
    except (AttributeError, IndexError, KeyError, ValueError):
        return template[:2000]


def message_templates(value: object) -> list[str]:
    """Read both existing strings and message pools without a database migration."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


async def choose_message(guild_id: int, feature: str, *, include_disabled: bool = False) -> str | None:
    templates = message_templates(await database.get_setting(guild_id, f"{feature}_message"))
    enabled = await database.get_setting(guild_id, f"{feature}_enabled", bool(templates))
    return random.choice(templates) if (enabled or include_disabled) and templates else None
