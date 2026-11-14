from __future__ import annotations

from types import SimpleNamespace

from cogs.bot_setup import CHANNEL_FEATURES, SETTING_OPTIONS
from cogs.community import _format_message
from cogs.cs2_updates import CounterStrikeUpdateChecker
from cogs.reaction_roles import _message_reference


def test_bot_setup_contains_all_configurable_channels_and_features():
    assert {"welcome", "farewell", "analytics", "starboard", "cs2_updates"} <= CHANNEL_FEATURES.keys()
    assert {"automod_enabled", "antiraid_enabled", "health_dashboard_enabled"} <= SETTING_OPTIONS.keys()


def test_welcome_template_replaces_supported_values():
    member = SimpleNamespace(
        mention="<@123>",
        display_name="Maughan",
        guild=SimpleNamespace(name="TGPDB", member_count=42),
    )
    result = _format_message("Welcome {user} ({username}) to {server}! Members: {member_count}", member)
    assert result == "Welcome <@123> (Maughan) to TGPDB! Members: 42"


def test_message_reference_accepts_ids_and_links():
    assert _message_reference("123456789012345678") == (None, 123456789012345678)
    assert _message_reference("https://discord.com/channels/1/2/3") == (2, 3)
    assert _message_reference("not-a-message") == (None, None)


def test_counter_strike_parser_removes_forum_markup():
    parsed = CounterStrikeUpdateChecker.parse_patch_note_body("[list][*]First change[*]Second change[/list]")
    assert "[list]" not in parsed
    assert "First change" in parsed
    assert "Second change" in parsed
