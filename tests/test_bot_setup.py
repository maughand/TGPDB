from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import database
import settings
from cogs._message_templates import choose_message, format_message, validate_template
from cogs._starboard import emoji_key, validate_emoji
from cogs.bot_setup import BlockedWordsModal, MessageTemplateModal, SETUP_PAGES, SetupPanel, StarboardSettingsModal
from cogs.community import Community


@pytest.fixture
async def setup_guild(tmp_path, monkeypatch):
    await database.close_database()
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "setup.sqlite3")
    monkeypatch.setattr(settings, "OWNER_USER_ID", None)
    await database.setup_database()
    guild = SimpleNamespace(id=42, name="Test Guild", owner_id=7, member_count=3, members=[])
    guild.me = SimpleNamespace(guild_permissions=discord.Permissions(manage_roles=True), top_role=100)
    channels = {}
    for channel_id in (101, 102):
        channel = Mock(spec=discord.TextChannel)
        channel.id, channel.mention, channel.guild = channel_id, f"<#{channel_id}>", guild
        channel.permissions_for.return_value = discord.Permissions(view_channel=True, send_messages=True, manage_channels=True)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=999))
        channels[channel_id] = channel
    guild.get_channel = channels.get
    guild.get_role = lambda role_id: None
    guild.get_emoji = lambda emoji_id: None
    yield guild
    await database.close_database()


def interaction(guild, *, user_id=7, manage_guild=True):
    response = Mock()
    response.is_done.return_value = False

    async def defer():
        response.is_done.return_value = True

    response.defer = AsyncMock(side_effect=defer)
    response.send_message = AsyncMock()
    response.edit_message = AsyncMock()
    response.send_modal = AsyncMock()
    return SimpleNamespace(
        user=SimpleNamespace(
            id=user_id, mention=f"<@{user_id}>", display_name="Test member", guild=guild,
            guild_permissions=discord.Permissions(manage_guild=manage_guild, manage_roles=True), top_role=50,
        ),
        client=SimpleNamespace(maintenance_mode=False), response=response,
        edit_original_response=AsyncMock(),
    )


async def feature_panel(guild, feature):
    panel = SetupPanel(guild, 7)
    panel.page = list(SETUP_PAGES).index(feature)
    await panel.load()
    return panel


@pytest.mark.parametrize("template", ["@(user.name)", "@(missing)", "{user.__class__}", "{user!r}", "{user:>1000000}", "{server", "", "a" * 2001])
def test_message_templates_reject_invalid_placeholders(template):
    assert validate_template(template)


def test_both_template_formats_and_literal_braces_work():
    member = SimpleNamespace(mention="<@7>", display_name="Name @(server)", guild=SimpleNamespace(name="TGPDB", member_count=42))
    template = "@(user) / {user}: @(username) joined @(server). {member_count} members. {{literal}}"
    assert validate_template(template) is None
    assert format_message(template, member) == "<@7> / <@7>: Name @(server) joined TGPDB. 42 members. {literal}"
    member.display_name = "x" * 1000
    assert len(format_message("@(username)" * 4, member)) == 2000


async def test_each_feature_has_only_its_own_controls(setup_guild):
    await database.set_setting(42, "welcome_message", [f"Message {i}: @(user)" for i in range(25)])
    await database.set_setting(42, "farewell_message", "Goodbye {username}")
    await database.set_channel(42, "welcome", 101)
    await database.set_setting(42, "starboard_emoji", "<:custom:123456789012345678>")
    for feature in SETUP_PAGES:
        panel = await feature_panel(setup_guild, feature)
        components = panel.to_components()
        assert len(components) <= 5
        assert len(panel._embed()) < 6000
        assert all(len(field.value) <= 1024 for field in panel._embed().fields)
        assert all(len(row["components"]) <= 5 for row in components)
        assert sum(isinstance(item, discord.ui.ChannelSelect) for item in panel.children) == bool(panel.channel_feature)
        if feature in {"welcome", "farewell"}:
            assert any(isinstance(item, discord.ui.Select) and item.row == 2 for item in panel.children)
        else:
            assert "Add message" not in [getattr(item, "label", None) for item in panel.children]
        if feature == "welcome":
            assert next(item for item in panel.children if getattr(item, "label", None) == "Add message").disabled
        if feature == "starboard":
            assert any(option.value == "custom" for option in panel.emoji_select.options)


@pytest.mark.parametrize("feature", ["welcome", "farewell"])
async def test_legacy_message_add_edit_preview_disable_and_remove(setup_guild, monkeypatch, feature):
    key = f"{feature}_message"
    await database.set_setting(42, key, "Legacy {user}")
    assert await choose_message(42, feature) == "Legacy {user}"
    panel = await feature_panel(setup_guild, feature)
    modal = MessageTemplateModal(panel)
    modal.message._value = "Hello @(user) in @(server)"
    await modal.on_submit(interaction(setup_guild))
    assert await database.get_setting(42, key) == ["Legacy {user}", "Hello @(user) in @(server)"]
    assert panel.selected_message == 1
    modal = MessageTemplateModal(panel, editing=True)
    modal.message._value = "Edited @(username)"
    await modal.on_submit(interaction(setup_guild))
    pool = ["Legacy {user}", "Edited @(username)"]
    assert await database.get_setting(42, key) == pool
    selected_pools = []

    def select_last(messages):
        selected_pools.append(messages)
        return messages[-1]

    monkeypatch.setattr("cogs._message_templates.random.choice", select_last)
    assert await choose_message(42, feature) == pool[-1]
    assert selected_pools == [pool]
    preview = interaction(setup_guild)
    await panel._preview_message(preview)
    assert preview.response.send_message.call_args.args[0] == "Edited Test member"
    assert preview.response.send_message.call_args.kwargs["ephemeral"] is True
    await panel._toggle(interaction(setup_guild))
    assert await choose_message(42, feature) is None
    assert await database.get_setting(42, key) == pool
    assert await choose_message(42, feature, include_disabled=True) == pool[-1]
    await panel._toggle(interaction(setup_guild))
    assert await choose_message(42, feature) in pool
    await panel._remove_message(interaction(setup_guild))
    await panel._remove_message(interaction(setup_guild))
    assert await database.get_setting(42, key) == []
    assert await database.get_setting(42, f"{feature}_enabled") is False
    assert await choose_message(42, feature) is None


@pytest.mark.parametrize("feature", ["welcome", "farewell"])
async def test_first_message_enables_feature_and_invalid_or_excess_messages_are_not_saved(setup_guild, feature):
    panel = await feature_panel(setup_guild, feature)
    modal = MessageTemplateModal(panel)
    modal.message._value = "Hello @(missing)"
    rejected = interaction(setup_guild)
    await modal.on_submit(rejected)
    rejected.response.send_message.assert_awaited_once()
    assert await database.get_setting(42, f"{feature}_message") is None
    modal.message._value = "Hello @(user)"
    await modal.on_submit(interaction(setup_guild))
    assert await database.get_setting(42, f"{feature}_enabled") is True
    pool = [f"Message {i}" for i in range(25)]
    await database.set_setting(42, f"{feature}_message", pool)
    await panel.load()
    modal = MessageTemplateModal(panel)
    modal.message._value = "One too many"
    await modal.on_submit(interaction(setup_guild))
    assert await database.get_setting(42, f"{feature}_message") == pool


async def test_large_blocked_word_collection_is_not_truncated_in_editor(setup_guild):
    words = [f"{index:03}" + "x" * 97 for index in range(100)]
    await database.set_setting(42, "automod_words", words)
    panel = await feature_panel(setup_guild, "automod")
    modal = BlockedWordsModal(panel)
    for field in modal.word_inputs:
        assert len(field.default) <= 4000
        field._value = field.default
    await modal.on_submit(interaction(setup_guild))
    assert await database.get_setting(42, "automod_words") == words


async def test_role_selection_and_clear_require_manage_roles(setup_guild):
    role = Mock(spec=discord.Role)
    role.id, role.mention, role.managed = 30, "<@&30>", False
    role.is_default.return_value = False
    role.__ge__ = Mock(return_value=False)
    setup_guild.get_role = lambda role_id: role if role_id == role.id else None
    panel = await feature_panel(setup_guild, "autorole")
    panel.role_select._values = [role]
    await panel._role_selected(interaction(setup_guild))
    assert await database.get_setting(42, "autorole_id") == role.id
    rejected = interaction(setup_guild)
    rejected.user.guild_permissions.manage_roles = False
    await panel._clear_role(rejected)
    assert await database.get_setting(42, "autorole_id") == role.id
    rejected.response.send_message.assert_awaited_once()
    await panel._clear_role(interaction(setup_guild))
    assert await database.get_setting(42, "autorole_id") is None


async def test_message_modal_does_not_overwrite_another_sessions_edit(setup_guild):
    await database.set_setting(42, "welcome_message", "Original")
    panel = await feature_panel(setup_guild, "welcome")
    modal = MessageTemplateModal(panel, editing=True)
    modal.message._value = "Old session edit"
    await database.set_setting(42, "welcome_message", ["New session edit"])
    await modal.on_submit(interaction(setup_guild))
    assert await database.get_setting(42, "welcome_message") == ["New session edit"]
    assert "another session" in panel.status


async def test_channel_selection_saves_on_current_page_and_rejects_categories(setup_guild):
    panel = await feature_panel(setup_guild, "welcome")
    panel.channel_select._values = [setup_guild.get_channel(101)]
    await panel._channel_selected(interaction(setup_guild))
    assert await database.get_channel(42, "welcome") == 101
    assert panel.feature == "welcome"
    assert panel.channel_select.default_values[0].id == 101
    category = Mock(spec=discord.CategoryChannel)
    category.id = 103
    original_get = setup_guild.get_channel
    setup_guild.get_channel = lambda channel_id: category if channel_id == 103 else original_get(channel_id)
    panel.channel_select._values = [category]
    rejected = interaction(setup_guild)
    await panel._channel_selected(rejected)
    rejected.response.send_message.assert_awaited_once()
    assert await database.get_channel(42, "welcome") == 101
    await panel._clear_channel(interaction(setup_guild))
    assert await database.get_channel(42, "welcome") is None


async def test_message_modal_rechecks_owner_and_server_permissions(setup_guild):
    panel = await feature_panel(setup_guild, "welcome")
    for rejected in (interaction(setup_guild, user_id=8), interaction(setup_guild, manage_guild=False)):
        setup_guild.owner_id = 9
        modal = MessageTemplateModal(panel)
        modal.message._value = "Not allowed"
        await modal.on_submit(rejected)
        rejected.response.send_message.assert_awaited_once()
    assert await database.get_setting(42, "welcome_message") is None


async def test_starboard_dropdown_modal_and_threshold_validation(setup_guild):
    panel = await feature_panel(setup_guild, "starboard")
    panel.emoji_select._values = ["🔥"]
    await panel._emoji_selected(interaction(setup_guild))
    assert await database.get_setting(42, "starboard_emoji") == "🔥"
    modal = StarboardSettingsModal(panel)
    modal.emoji._value, modal.threshold._value = "❤️", "0"
    rejected = interaction(setup_guild)
    await modal.on_submit(rejected)
    rejected.response.send_message.assert_awaited_once()
    assert await database.get_setting(42, "starboard_emoji") == "🔥"
    modal.threshold._value = "5"
    await modal.on_submit(interaction(setup_guild))
    assert await database.get_setting(42, "starboard_emoji") == "❤️"
    assert await database.get_setting(42, "starboard_threshold") == 5
    assert validate_emoji("not an emoji", setup_guild)
    assert validate_emoji("⭐⭐", setup_guild)
    assert validate_emoji("<:missing:123456789012345678>", setup_guild)
    assert emoji_key("<a:renamed:123456789012345678>") == emoji_key("<:original:123456789012345678>")


@pytest.mark.parametrize("feature,event", [("welcome", "on_member_join"), ("farewell", "on_member_remove")])
async def test_member_events_send_formatted_message_from_pool(setup_guild, monkeypatch, feature, event):
    await database.set_setting(42, f"{feature}_message", ["First @(user)", "Second @(username)"])
    await database.set_channel(42, feature, 101)
    monkeypatch.setattr("cogs._message_templates.random.choice", lambda pool: pool[-1])
    cog = Community.__new__(Community)
    cog.raid_joins = defaultdict(deque)
    cog._log = AsyncMock()
    member = interaction(setup_guild).user
    await getattr(cog, event)(member)
    channel = setup_guild.get_channel(101)
    assert channel.send.call_args.args[0] == "Second Test member"
    mentions = channel.send.call_args.kwargs["allowed_mentions"].to_dict()
    assert mentions["users"] == [member.id]
    assert "everyone" not in mentions["parse"]
    await database.set_setting(42, f"{feature}_enabled", False)
    channel.send.reset_mock()
    await getattr(cog, event)(member)
    channel.send.assert_not_awaited()


@pytest.mark.parametrize("configured,reaction", [("🔥", "🔥"), ("❤️", "❤"), ("<:oldname:123456789012345678>", "<a:newname:123456789012345678>")])
async def test_starboard_posts_only_configured_reaction_once(setup_guild, configured, reaction):
    await database.set_channel(42, "starboard", 102)
    await database.set_setting(42, "starboard_emoji", configured)
    source_channel, destination = setup_guild.get_channel(101), setup_guild.get_channel(102)
    source_channel.fetch_message = AsyncMock(return_value=SimpleNamespace(
        id=50, content="A highlight", channel=source_channel,
        created_at=datetime.now(timezone.utc), attachments=[],
        author=SimpleNamespace(bot=False, display_avatar=SimpleNamespace(url="https://example.com/avatar.png")),
        reactions=[SimpleNamespace(emoji=reaction, count=3)],
    ))
    cog = Community.__new__(Community)
    cog.bot = SimpleNamespace(get_channel=setup_guild.get_channel)
    payload = SimpleNamespace(guild_id=42, channel_id=101, message_id=50, emoji="👍")
    await cog.on_raw_reaction_add(payload)
    source_channel.fetch_message.assert_not_awaited()
    payload.emoji = reaction
    await cog.on_raw_reaction_add(payload)
    await cog.on_raw_reaction_add(payload)
    destination.send.assert_awaited_once()
    assert destination.send.call_args.kwargs["content"].startswith(configured)
