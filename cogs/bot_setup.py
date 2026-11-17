"""Per-server feature setup and guild data synchronization commands."""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands

import database
import settings
from cogs._message_templates import (
    MAX_MESSAGE_TEMPLATES, TEMPLATE_HELP, format_message, message_templates,
    validate_template as _validate_template,
)
from cogs._starboard import EMOJI_CHOICES, validate_emoji


logger = logging.getLogger(__name__)
CHANNEL_FEATURES = {
    "moderation_log": "Moderation action logs",
    "welcome": "Welcome messages",
    "farewell": "Farewell messages",
    "member_count": "Member count updates",
    "announcements": "Bot announcements",
    "cs2_updates": "Counter-Strike 2 updates",
    "analytics": "Daily analytics",
    "starboard": "Starboard highlights",
}
MEMBER_COUNT_LABEL_SETTING = "member_count_label"
DEFAULT_MEMBER_COUNT_LABEL = "Members"
_member_count_updates: dict[int, float] = {}
SETTING_OPTIONS = {
    "welcome_message": "Welcome messages",
    "farewell_message": "Farewell messages",
    "welcome_enabled": "Welcome messages",
    "farewell_enabled": "Farewell messages",
    "autorole_id": "Autorole",
    "automod_enabled": "Automod",
    "automod_words": "Blocked words",
    "antiraid_enabled": "Anti-raid",
    "starboard_threshold": "Starboard threshold",
    "starboard_emoji": "Starboard emoji",
    "levels_enabled": "XP levels",
    "health_dashboard_enabled": "Server health dashboard",
}
SETUP_PAGES = {
    "overview": "Overview",
    "welcome": "Welcome messages",
    "farewell": "Farewell messages",
    "moderation_log": "Moderation action logs",
    "autorole": "Automatic roles",
    "automod": "Automod",
    "antiraid": "Anti-raid",
    "starboard": "Starboard highlights",
    "levels": "XP levels",
    "member_count": "Member count",
    "announcements": "Bot announcements",
    "cs2_updates": "Counter-Strike 2 updates",
    "analytics": "Daily analytics",
    "health": "Server health dashboard",
    "guild_data": "Guild data",
}
PAGE_DESCRIPTIONS = {
    "overview": "Choose a feature below. Each feature has its own page, and changes save immediately.",
    "welcome": "Greet new members with a randomly selected message from your collection.",
    "farewell": "Send a randomly selected farewell when someone leaves the server.",
    "moderation_log": "Choose where the bot sends moderation actions and server activity logs.",
    "autorole": "Choose the role that new members receive automatically.",
    "automod": "Filter spam, Discord invites, and your blocked words or phrases.",
    "antiraid": "Protect against join bursts. Five joins within 30 seconds trigger temporary timeouts.",
    "starboard": "Highlight messages that reach the reaction threshold. Choose the channel and emoji to watch.",
    "levels": "Let members earn XP by chatting. Configure level role rewards with `/levels reward`.",
    "member_count": "Keep a channel or category name up to date with the server's member count.",
    "announcements": "Choose the channel for bot announcements.",
    "cs2_updates": "Choose where new Counter-Strike 2 patch notes are posted.",
    "analytics": "Choose where daily summaries of messages, joins, leaves, and commands are posted.",
    "health": "Post the daily server health dashboard to the analytics channel.",
    "guild_data": "Refresh the saved server information and member list.",
}
TOGGLE_SETTINGS = {
    "welcome": "welcome_enabled", "farewell": "farewell_enabled",
    "automod": "automod_enabled", "antiraid": "antiraid_enabled",
    "levels": "levels_enabled", "health": "health_dashboard_enabled",
}


async def update_member_count_channel(guild: discord.Guild, *, force: bool = True) -> None:
    """Keep the configured member-count channel name in sync with the guild."""
    now = time.monotonic()
    if not force and now - _member_count_updates.get(guild.id, 0.0) < 300:
        return
    channel_id = await database.get_channel(guild.id, "member_count")
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, (discord.TextChannel, discord.CategoryChannel)):
        return
    label = await database.get_setting(guild.id, MEMBER_COUNT_LABEL_SETTING, DEFAULT_MEMBER_COUNT_LABEL)
    label = str(label or DEFAULT_MEMBER_COUNT_LABEL).strip()[:80]
    count = guild.member_count or len(guild.members)
    try:
        await channel.edit(name=f"{label}: {count}"[:100], reason="Update configured member count")
        _member_count_updates[guild.id] = now
    except discord.HTTPException:
        logger.exception("Could not update member count channel for guild %s", guild.id)


class MemberCountModal(discord.ui.Modal, title="Member count text"):
    label = discord.ui.TextInput(label="Text before the count", placeholder="For example: Community members", max_length=80)

    def __init__(self, panel: "SetupPanel") -> None:
        super().__init__()
        self.panel = panel
        self.label.default = panel.member_count_label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        await interaction.response.defer()
        await database.set_setting(self.panel.guild.id, MEMBER_COUNT_LABEL_SETTING, self.label.value.strip() or DEFAULT_MEMBER_COUNT_LABEL)
        await update_member_count_channel(self.panel.guild, force=True)
        await self.panel.reload(interaction, "Member count text saved.")


class MessageTemplateModal(discord.ui.Modal):
    message = discord.ui.TextInput(
        label="Message · @(user), @(server), @(username)",
        style=discord.TextStyle.paragraph,
        placeholder="Welcome @(user) to @(server)! We now have @(member_count) members.",
        max_length=2000,
    )

    def __init__(self, panel: "SetupPanel", *, editing: bool = False) -> None:
        self.feature = panel.feature
        super().__init__(title=f"{'Edit' if editing else 'Add'} {self.feature} message")
        self.panel = panel
        self.original_templates = list(panel.templates)
        self.index = panel.selected_message if editing else None
        if self.index is not None:
            self.message.default = self.original_templates[self.index]
        if self.feature == "farewell":
            self.message.placeholder = "Goodbye @(username)! Thanks for being part of @(server)."

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        message = self.message.value.strip()
        error = _validate_template(message)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer()
        current = message_templates(await database.get_setting(self.panel.guild.id, f"{self.feature}_message"))
        if current != self.original_templates:
            await self.panel.reload(interaction, "Messages changed in another session. Please select the message again.")
            return
        if self.index is None:
            if len(current) >= MAX_MESSAGE_TEMPLATES:
                await self.panel.reload(interaction, "You can save up to 25 messages. Remove one before adding another.")
                return
            current.append(message)
            selected = len(current) - 1
        else:
            current[self.index] = message
            selected = self.index
        await database.set_setting(self.panel.guild.id, f"{self.feature}_message", current)
        # Match the old first-message behavior, while preserving an explicitly disabled collection.
        if not self.original_templates:
            await database.set_setting(self.panel.guild.id, f"{self.feature}_enabled", True)
        if self.panel.feature == self.feature:
            self.panel.selected_message = selected
        await self.panel.reload(interaction, "Message saved. Each send randomly chooses from the collection.")


class StarboardSettingsModal(discord.ui.Modal, title="Starboard settings"):
    emoji = discord.ui.TextInput(label="Reaction emoji", placeholder="⭐ or a custom server emoji", max_length=100)
    threshold = discord.ui.TextInput(label="Reactions needed (1–100)", max_length=3)

    def __init__(self, panel: "SetupPanel") -> None:
        super().__init__()
        self.panel = panel
        self.emoji.default = str(panel.settings.get("starboard_emoji") or "⭐")
        self.threshold.default = str(panel.settings.get("starboard_threshold") or 3)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        emoji = self.emoji.value.strip()
        error = validate_emoji(emoji, self.panel.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        threshold = self.threshold.value.strip()
        if not threshold.isascii() or not threshold.isdigit() or not 1 <= int(threshold) <= 100:
            await interaction.response.send_message("Choose a reaction threshold from 1 to 100.", ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_setting(self.panel.guild.id, "starboard_emoji", emoji)
        await database.set_setting(self.panel.guild.id, "starboard_threshold", int(threshold))
        await self.panel.reload(interaction, "Starboard emoji and reaction threshold saved.")


class BlockedWordsModal(discord.ui.Modal, title="Automod blocked words"):
    def __init__(self, panel: "SetupPanel") -> None:
        super().__init__()
        self.panel = panel
        # Up to 100 existing 100-character phrases may need several text inputs.
        batches = [""]
        for word in panel.settings.get("automod_words") or []:
            separator = "\n" if batches[-1] else ""
            if len(batches[-1]) + len(separator) + len(word) > 4000:
                batches.append(word)
            else:
                batches[-1] += separator + word
        self.word_inputs = []
        for index, text in enumerate(batches):
            field = discord.ui.TextInput(
                label="One word or phrase per line" if index == 0 else f"More words or phrases ({index + 1})",
                style=discord.TextStyle.paragraph,
                placeholder="Leave empty to clear these words.",
                default=text, max_length=4000, required=False,
            )
            self.word_inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        raw = "\n".join(field.value for field in self.word_inputs)
        words = list(dict.fromkeys(line.strip().casefold() for line in raw.splitlines() if line.strip()))
        if len(words) > 100 or any(len(word) > 100 for word in words):
            await interaction.response.send_message("Use up to 100 words or phrases, each at most 100 characters.", ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_setting(self.panel.guild.id, "automod_words", words)
        await self.panel.reload(interaction, f"Saved {len(words)} blocked words or phrases.")


class SetupPanel(discord.ui.View):
    """One feature per page, with only the controls needed for that feature."""

    def __init__(self, guild: discord.Guild, user_id: int) -> None:
        super().__init__(timeout=600)
        self.guild = guild
        self.user_id = user_id
        self.page = 0
        self.selected_message = 0
        self.channels: dict[str, int | None] = {}
        self.settings: dict[str, object] = {}
        self.member_count_label = DEFAULT_MEMBER_COUNT_LABEL
        self.status = ""
        self._update_items()

    @property
    def feature(self) -> str:
        return list(SETUP_PAGES)[self.page]

    @property
    def channel_feature(self) -> str | None:
        if self.feature == "health":
            return "analytics"
        return self.feature if self.feature in CHANNEL_FEATURES else None

    @property
    def templates(self) -> list[str]:
        return message_templates(self.settings.get(f"{self.feature}_message"))

    def _enabled(self, feature: str) -> bool:
        fallback = bool(message_templates(self.settings.get(f"{feature}_message"))) if feature in {"welcome", "farewell"} else False
        value = self.settings.get(TOGGLE_SETTINGS[feature])
        return fallback if value is None else bool(value)

    async def load(self) -> None:
        self.channels = await database.get_channels(self.guild.id)
        self.member_count_label = str(await database.get_setting(self.guild.id, MEMBER_COUNT_LABEL_SETTING, DEFAULT_MEMBER_COUNT_LABEL) or DEFAULT_MEMBER_COUNT_LABEL)
        self.settings = {key: await database.get_setting(self.guild.id, key) for key in SETTING_OPTIONS}
        self.selected_message = min(self.selected_message, max(0, len(self.templates) - 1))
        self._update_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This setup panel belongs to the person who opened it.", ephemeral=True)
            return False
        is_bot_owner = settings.OWNER_USER_ID is not None and interaction.user.id == settings.OWNER_USER_ID
        if getattr(interaction.client, "maintenance_mode", False) and not is_bot_owner:
            await interaction.response.send_message("The bot is currently in maintenance mode.", ephemeral=True)
            return False
        if not (is_bot_owner or interaction.user.id == self.guild.owner_id or interaction.user.guild_permissions.manage_guild):
            await interaction.response.send_message("You need Manage Server permission to use this setup panel.", ephemeral=True)
            return False
        return True

    def _channel_display(self, feature: str) -> str:
        channel_id = self.channels.get(feature)
        channel = self.guild.get_channel(channel_id) if channel_id else None
        return channel.mention if channel else "Not assigned"

    def _embed(self) -> discord.Embed:
        feature = self.feature
        embed = discord.Embed(
            title=f"Bot setup · {SETUP_PAGES[feature]}",
            description=PAGE_DESCRIPTIONS[feature],
            colour=discord.Colour.blurple(),
        )
        if feature == "overview":
            channel_lines = [f"**{label}** · {self._channel_display(key)}" for key, label in CHANNEL_FEATURES.items()]
            embed.add_field(name="Channels", value="\n".join(channel_lines), inline=False)
            states = [f"{SETTING_OPTIONS[key]}: {'On' if self._enabled(name) else 'Off'}" for name, key in TOGGLE_SETTINGS.items()]
            embed.add_field(name="Features", value=" · ".join(states), inline=False)
        if self.channel_feature:
            embed.add_field(name="Assigned channel", value=self._channel_display(self.channel_feature), inline=True)
        if feature in TOGGLE_SETTINGS:
            embed.add_field(name="Status", value="Enabled" if self._enabled(feature) else "Disabled", inline=True)
        if feature in {"welcome", "farewell"}:
            templates = self.templates
            embed.add_field(name="Message collection", value=f"{len(templates)}/25 saved · one chosen randomly each time", inline=False)
            if templates:
                embed.add_field(name=f"Message {self.selected_message + 1}", value=templates[self.selected_message][:1024], inline=False)
            else:
                embed.add_field(name="Get started", value="Choose a channel, then use **Add message** to write your first message.", inline=False)
            embed.add_field(name="Formatting", value=TEMPLATE_HELP, inline=False)
            if self._enabled(feature) and not self.channels.get(feature):
                embed.add_field(name="One more step", value="Choose a channel below so messages can be sent.", inline=False)
        elif feature == "starboard":
            embed.add_field(name="Reaction to track", value=str(self.settings.get("starboard_emoji") or "⭐"), inline=True)
            embed.add_field(name="Reactions needed", value=str(self.settings.get("starboard_threshold") or 3), inline=True)
        elif feature == "member_count":
            count = self.guild.member_count or len(self.guild.members)
            embed.add_field(name="Channel name preview", value=f"{self.member_count_label}: {count}"[:1024], inline=False)
        elif feature == "autorole":
            role_id = self.settings.get("autorole_id")
            role = self.guild.get_role(role_id) if role_id else None
            embed.add_field(name="Assigned role", value=role.mention if role else "Not assigned", inline=False)
        elif feature == "automod":
            embed.add_field(name="Blocked words and phrases", value=str(len(self.settings.get("automod_words") or [])), inline=False)
        elif feature == "guild_data":
            embed.add_field(name="Server", value=f"{self.guild.name}\n`{self.guild.id}`", inline=True)
            embed.add_field(name="Visible members", value=str(len(self.guild.members)), inline=True)
        footer = f"Page {self.page + 1}/{len(SETUP_PAGES)} · Changes save automatically"
        if self.status:
            footer += f" · {self.status}"
        embed.set_footer(text=footer)
        return embed

    def _button(self, label: str, callback, *, row: int, style=discord.ButtonStyle.secondary, disabled: bool = False) -> None:
        button = discord.ui.Button(label=label, style=style, row=row, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    def _update_items(self) -> None:
        self.clear_items()
        feature = self.feature
        self.feature_select = discord.ui.Select(
            placeholder="Go to a feature…",
            options=[discord.SelectOption(label=label, value=key, default=key == feature) for key, label in SETUP_PAGES.items()],
            row=0,
        )
        self.feature_select.callback = self._feature_selected
        self.add_item(self.feature_select)
        if self.channel_feature:
            types = [discord.ChannelType.text, discord.ChannelType.news]
            if feature == "member_count":
                types.append(discord.ChannelType.category)
            channel_id = self.channels.get(self.channel_feature)
            channel = self.guild.get_channel(channel_id) if channel_id else None
            self.channel_select = discord.ui.ChannelSelect(
                placeholder="Choose a channel · saves immediately",
                channel_types=types,
                default_values=[channel] if channel else [],
                row=1,
            )
            self.channel_select.callback = self._channel_selected
            self.add_item(self.channel_select)
        elif feature == "autorole":
            role_id = self.settings.get("autorole_id")
            role = self.guild.get_role(role_id) if role_id else None
            self.role_select = discord.ui.RoleSelect(placeholder="Choose an automatic role…", default_values=[role] if role else [], row=1)
            self.role_select.callback = self._role_selected
            self.add_item(self.role_select)
        if feature in {"welcome", "farewell"}:
            templates = self.templates
            if templates:
                self.message_select = discord.ui.Select(
                    placeholder="Choose a message to edit or preview…",
                    options=[discord.SelectOption(
                        label=f"Message {index + 1}", value=str(index),
                        description=" ".join(template.split())[:100], default=index == self.selected_message,
                    ) for index, template in enumerate(templates[:MAX_MESSAGE_TEMPLATES])],
                    row=2,
                )
                self.message_select.callback = self._message_selected
                self.add_item(self.message_select)
            self._button("Add message", self._add_message, row=3, style=discord.ButtonStyle.primary, disabled=len(templates) >= MAX_MESSAGE_TEMPLATES)
            self._button("Edit", self._edit_message, row=3, disabled=not templates)
            self._button("Remove", self._remove_message, row=3, disabled=not templates)
            self._button("Preview", self._preview_message, row=3, disabled=not templates)
        elif feature == "starboard":
            current = str(self.settings.get("starboard_emoji") or "⭐")
            options = [discord.SelectOption(label=f"{emoji} {label}", value=emoji, default=emoji == current) for emoji, label in EMOJI_CHOICES.items()]
            if current not in EMOJI_CHOICES:
                options.append(discord.SelectOption(label="Current custom emoji", value=current, default=True))
            options.append(discord.SelectOption(label="Use a custom emoji…", value="custom"))
            self.emoji_select = discord.ui.Select(placeholder="Choose the reaction to track…", options=options, row=2)
            self.emoji_select.callback = self._emoji_selected
            self.add_item(self.emoji_select)
            self._button("Emoji & threshold", self._starboard_settings, row=3, style=discord.ButtonStyle.primary)
        elif feature == "member_count":
            self._button("Edit count text", self._member_count_settings, row=3, style=discord.ButtonStyle.primary)
        elif feature == "autorole":
            self._button("Clear role", self._clear_role, row=3, disabled=not self.settings.get("autorole_id"))
        elif feature == "automod":
            self._button("Edit blocked words", self._blocked_words, row=3, style=discord.ButtonStyle.primary)
        elif feature == "guild_data":
            self._button("Sync members", self._sync, row=3, style=discord.ButtonStyle.primary)
        if feature in TOGGLE_SETTINGS:
            enabled = self._enabled(feature)
            self._button("Disable" if enabled else "Enable", self._toggle, row=3, style=discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success)
        self._button("Back", self._back, row=4, disabled=self.page == 0)
        self._button("Next", self._next, row=4, disabled=self.page == len(SETUP_PAGES) - 1)
        self._button("Refresh", self._refresh, row=4)
        if self.channel_feature:
            self._button("Clear channel", self._clear_channel, row=4, disabled=not self.channels.get(self.channel_feature))
        self._button("Close", self._close, row=4)

    async def edit(self, interaction: discord.Interaction) -> None:
        self._update_items()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self._embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    async def reload(self, interaction: discord.Interaction, status: str) -> None:
        await self.load()
        self.status = status
        await self.edit(interaction)

    async def _feature_selected(self, interaction: discord.Interaction) -> None:
        self.page = list(SETUP_PAGES).index(self.feature_select.values[0])
        self.selected_message = 0
        self.status = ""
        await self.load()
        await self.edit(interaction)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self.selected_message = 0
        self.status = ""
        await self.edit(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(len(SETUP_PAGES) - 1, self.page + 1)
        self.selected_message = 0
        self.status = ""
        await self.edit(interaction)

    async def _channel_selected(self, interaction: discord.Interaction) -> None:
        channel = self.guild.get_channel(self.channel_select.values[0].id)
        allowed = (discord.TextChannel, discord.CategoryChannel) if self.feature == "member_count" else (discord.TextChannel,)
        if not isinstance(channel, allowed):
            await interaction.response.send_message("Choose a supported channel in this server.", ephemeral=True)
            return
        permissions = channel.permissions_for(self.guild.me) if self.guild.me else None
        renaming = self.feature == "member_count"
        if permissions is None or not permissions.view_channel or not (permissions.manage_channels if renaming else permissions.send_messages):
            needed = "Manage Channels" if renaming else "Send Messages"
            await interaction.response.send_message(f"I need View Channel and {needed} in that channel.", ephemeral=True)
            return
        feature = self.channel_feature
        await interaction.response.defer()
        await database.set_channel(self.guild.id, feature, channel.id)
        if feature == "member_count":
            await update_member_count_channel(self.guild, force=True)
        await self.reload(interaction, "Channel saved.")

    async def _clear_channel(self, interaction: discord.Interaction) -> None:
        feature = self.channel_feature
        await interaction.response.defer()
        await database.set_channel(self.guild.id, feature, None)
        await self.reload(interaction, "Channel cleared.")

    async def _toggle(self, interaction: discord.Interaction) -> None:
        feature = self.feature
        enabled = not self._enabled(feature)
        if enabled and feature in {"welcome", "farewell"} and not self.templates:
            await interaction.response.send_message("Add at least one message before enabling this feature.", ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_setting(self.guild.id, TOGGLE_SETTINGS[feature], enabled)
        await self.reload(interaction, "Feature enabled." if enabled else "Feature disabled; saved settings are kept.")

    async def _message_selected(self, interaction: discord.Interaction) -> None:
        self.selected_message = int(self.message_select.values[0])
        self.status = ""
        await self.edit(interaction)

    async def _add_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MessageTemplateModal(self))

    async def _edit_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MessageTemplateModal(self, editing=True))

    async def _remove_message(self, interaction: discord.Interaction) -> None:
        feature = self.feature
        original = self.templates
        await interaction.response.defer()
        current = message_templates(await database.get_setting(self.guild.id, f"{feature}_message"))
        if current != original or not current:
            await self.reload(interaction, "Messages changed in another session. Please select the message again.")
            return
        del current[self.selected_message]
        await database.set_setting(self.guild.id, f"{feature}_message", current)
        if not current:
            await database.set_setting(self.guild.id, f"{feature}_enabled", False)
        await self.reload(interaction, "Message removed.")

    async def _preview_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(format_message(self.templates[self.selected_message], interaction.user), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def _emoji_selected(self, interaction: discord.Interaction) -> None:
        emoji = self.emoji_select.values[0]
        if emoji == "custom":
            await self._starboard_settings(interaction)
            return
        error = validate_emoji(emoji, self.guild)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_setting(self.guild.id, "starboard_emoji", emoji)
        await self.reload(interaction, "Starboard reaction saved.")

    async def _starboard_settings(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(StarboardSettingsModal(self))

    async def _member_count_settings(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MemberCountModal(self))

    async def _blocked_words(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BlockedWordsModal(self))

    def _role_error(self, member: discord.Member, role: discord.Role | None) -> str | None:
        if not member.guild_permissions.manage_roles:
            return "You need Manage Roles to configure an automatic role."
        if role is not None:
            if self.guild.me is None or not self.guild.me.guild_permissions.manage_roles:
                return "I need Manage Roles permission to assign automatic roles."
            if role.is_default() or role.managed or role >= self.guild.me.top_role:
                return "Choose a normal role below my highest role."
            if member.id != self.guild.owner_id and role >= member.top_role:
                return "Choose a role below your highest role."
        return None

    async def _role_selected(self, interaction: discord.Interaction) -> None:
        role = self.guild.get_role(self.role_select.values[0].id)
        error = self._role_error(interaction.user, role) if role else "That role is no longer available."
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_setting(self.guild.id, "autorole_id", role.id)
        await self.reload(interaction, "Automatic role saved.")

    async def _clear_role(self, interaction: discord.Interaction) -> None:
        error = self._role_error(interaction.user, None)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_setting(self.guild.id, "autorole_id", None)
        await self.reload(interaction, "Automatic role cleared.")

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.reload(interaction, "Configuration refreshed.")

    async def _sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.sync_guild_members(self.guild)
        await update_member_count_channel(self.guild, force=True)
        await self.reload(interaction, f"Saved {len(self.guild.members)} members.")

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)


class BotSetup(commands.Cog):
    """Let a server owner or administrator configure bot channels."""

    async def _send_panel(self, ctx: commands.Context) -> None:
        panel = SetupPanel(ctx.guild, ctx.author.id)
        await panel.load()
        await ctx.send(
            embed=panel._embed(),
            view=panel,
            ephemeral=ctx.interaction is not None,
        )

    @commands.hybrid_group(name="botsetup", description="Configure TGPDB for this server.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def botsetup(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await self._send_panel(ctx)

    @botsetup.command(name="panel", description="Open the interactive TGPDB setup panel.")
    async def panel(self, ctx: commands.Context) -> None:
        await self._send_panel(ctx)

    @botsetup.command(name="channel", description="Assign a channel to a bot feature.")
    async def channel(self, ctx: commands.Context, feature: str, channel: discord.TextChannel) -> None:
        """Set a feature channel for this guild only."""
        feature = feature.lower().strip()
        if feature not in CHANNEL_FEATURES:
            choices = ", ".join(CHANNEL_FEATURES)
            await ctx.send(f"Unknown feature. Choose one of: `{choices}`.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        await database.set_channel(ctx.guild.id, feature, channel.id)
        await database.upsert_guild(ctx.guild)
        if feature == "member_count":
            await update_member_count_channel(ctx.guild, force=True)
        await ctx.send(f"{CHANNEL_FEATURES[feature]} will use {channel.mention}.", ephemeral=True)

    @botsetup.command(name="clear", description="Clear a configured bot feature channel.")
    async def clear(self, ctx: commands.Context, feature: str) -> None:
        """Remove a channel assignment for this guild."""
        feature = feature.lower().strip()
        if feature not in CHANNEL_FEATURES:
            await ctx.send(f"Unknown feature. Choose one of: `{', '.join(CHANNEL_FEATURES)}`.", ephemeral=True)
            return
        await database.set_channel(ctx.guild.id, feature, None)
        await ctx.send(f"Cleared the {CHANNEL_FEATURES[feature].lower()} channel.", ephemeral=True)

    @botsetup.command(name="membercount", description="Configure a live member count channel name.")
    async def membercount(self, ctx: commands.Context, channel: discord.TextChannel, custom_text: str) -> None:
        """Assign a channel and set the text shown before its live member count."""
        await ctx.defer(ephemeral=True)
        await database.set_channel(ctx.guild.id, "member_count", channel.id)
        await database.set_setting(
            ctx.guild.id,
            MEMBER_COUNT_LABEL_SETTING,
            custom_text.strip()[:80] or DEFAULT_MEMBER_COUNT_LABEL,
        )
        await update_member_count_channel(ctx.guild, force=True)
        await ctx.send(
            f"Member count channel configured in {channel.mention}.",
            ephemeral=True,
        )

    @botsetup.command(name="show", description="Show this server's bot configuration.")
    async def show(self, ctx: commands.Context) -> None:
        """Display all configured channel IDs for this guild."""
        channels = await database.get_channels(ctx.guild.id)
        lines = []
        for feature, description in CHANNEL_FEATURES.items():
            channel_id = channels.get(feature)
            channel = ctx.guild.get_channel(channel_id) if channel_id else None
            value = channel.mention if channel else "Not configured"
            lines.append(f"**{description}:** {value}")
        embed = discord.Embed(title=f"{ctx.guild.name} bot setup", description="\n".join(lines), colour=discord.Colour.blurple())
        await ctx.send(embed=embed, ephemeral=True)

    @botsetup.command(name="sync", description="Save the current guild and member information.")
    async def sync(self, ctx: commands.Context) -> None:
        """Refresh the persisted guild/member snapshot."""
        await ctx.defer(ephemeral=True)
        await database.sync_guild_members(ctx.guild)
        await update_member_count_channel(ctx.guild, force=True)
        await ctx.send(f"Saved guild information and {len(ctx.guild.members)} member(s).", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await database.upsert_member(member)
        await update_member_count_channel(member.guild, force=False)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.display_name != after.display_name or before.roles != after.roles:
            await database.upsert_member(after)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await database.remove_member(member.guild.id, member.id)
        await update_member_count_channel(member.guild, force=False)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await database.sync_guild_members(guild)
        await update_member_count_channel(guild, force=True)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if ctx.cog is not self:
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Only server owners or administrators can change bot setup.", ephemeral=True)
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command can only be used in a server.", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await ctx.send("I could not resolve that channel. Mention a text channel.", ephemeral=True)
        else:
            logger.exception("Bot setup command failed", exc_info=error)
            await ctx.send("Bot setup failed.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotSetup())
