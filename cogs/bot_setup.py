"""Per-server bot setup and guild data synchronization commands."""

from __future__ import annotations

import logging
import string
import time

import discord
from discord.ext import commands

import database
import settings


logger = logging.getLogger(__name__)
CHANNEL_FEATURES = {
    "moderation_log": "Moderation action logs",
    "welcome": "Welcome messages",
    "farewell": "Farewell messages",
    "member_count": "Member count updates",
    "announcements": "Bot announcements",
    "cs2_updates": "Counter-Strike 2 update notifications",
    "analytics": "Daily analytics",
    "starboard": "Starboard highlights",
}
MEMBER_COUNT_LABEL_SETTING = "member_count_label"
DEFAULT_MEMBER_COUNT_LABEL = "Members"
_member_count_updates: dict[int, float] = {}
ALLOWED_TEMPLATE_FIELDS = {"user", "username", "server", "member_count"}
SETTING_OPTIONS = {
    "welcome_message": "Welcome message",
    "farewell_message": "Farewell message",
    "autorole_id": "Autorole",
    "automod_enabled": "Automod",
    "antiraid_enabled": "Anti-raid",
    "starboard_threshold": "Starboard threshold",
    "levels_enabled": "XP levels",
    "health_dashboard_enabled": "Server health dashboard",
}


def _validate_template(template: str) -> str | None:
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is not None and field_name not in ALLOWED_TEMPLATE_FIELDS:
                return f"Unsupported placeholder `{field_name}`."
            if format_spec or conversion:
                return "Only simple placeholders such as `{user}` are supported."
    except ValueError:
        return "The template contains unmatched braces."
    return None


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


class MemberCountModal(discord.ui.Modal, title="Member count channel"):
    """Collect the custom text displayed before the live member count."""

    label = discord.ui.TextInput(
        label="Custom text",
        placeholder="For example: Community members",
        min_length=1,
        max_length=80,
        required=True,
    )

    def __init__(self, panel: "SetupPanel", current_label: str) -> None:
        super().__init__()
        self.panel = panel
        self.label.default = current_label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        await interaction.response.defer()
        await database.set_setting(
            self.panel.guild.id,
            MEMBER_COUNT_LABEL_SETTING,
            str(self.label.value).strip() or DEFAULT_MEMBER_COUNT_LABEL,
        )
        await update_member_count_channel(self.panel.guild, force=True)
        self.panel.status = "Member count text saved and channel name updated."
        await self.panel.load()
        await interaction.edit_original_response(embed=self.panel._embed(), view=self.panel)


class FeatureSettingModal(discord.ui.Modal, title="Bot feature setting"):
    """Edit a text, number, or role-ID feature setting from the setup panel."""

    value = discord.ui.TextInput(label="Value", min_length=1, max_length=2000, required=True)

    def __init__(self, panel: "SetupPanel", setting: str, current: str, prompt: str) -> None:
        super().__init__()
        self.panel = panel
        self.setting = setting
        self.value.label = prompt[:45]
        self.value.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        raw_value = str(self.value.value).strip()
        if self.setting == "starboard_threshold":
            if not raw_value.isdigit() or not 1 <= int(raw_value) <= 100:
                await interaction.response.send_message("❌ Starboard threshold must be a number from 1 to 100.", ephemeral=True)
                return
            value: object = int(raw_value)
        elif self.setting == "autorole_id":
            role_id = raw_value.strip("<@&>")
            if not role_id.isdigit() or self.panel.guild.get_role(int(role_id)) is None:
                await interaction.response.send_message("❌ Enter a valid role ID or role mention.", ephemeral=True)
                return
            role = self.panel.guild.get_role(int(role_id))
            moderator = self.panel.guild.get_member(self.panel.user_id)
            if moderator is None or not moderator.guild_permissions.manage_roles:
                await interaction.response.send_message("❌ You need Manage Roles to configure an autorole.", ephemeral=True)
                return
            if role is None or role.is_default() or role.managed or self.panel.guild.me is None or role >= self.panel.guild.me.top_role:
                await interaction.response.send_message("❌ I can only assign a normal role below my highest role.", ephemeral=True)
                return
            if moderator and moderator != self.panel.guild.owner and role >= moderator.top_role:
                await interaction.response.send_message("❌ You cannot configure a role at or above your highest role.", ephemeral=True)
                return
            value = role.id
        else:
            value = raw_value
            if self.setting in {"welcome_message", "farewell_message"}:
                template_error = _validate_template(raw_value)
                if template_error:
                    await interaction.response.send_message(f"❌ {template_error}", ephemeral=True)
                    return
        await interaction.response.defer()
        await database.set_setting(self.panel.guild.id, self.setting, value)
        self.panel.status = f"Saved {SETTING_OPTIONS[self.setting].lower()}."
        await self.panel.load()
        await interaction.edit_original_response(embed=self.panel._embed(), view=self.panel)


class SetupPanel(discord.ui.View):
    """Interactive, ephemeral setup wizard for one guild."""

    def __init__(self, guild: discord.Guild, user_id: int) -> None:
        super().__init__(timeout=300)
        self.guild = guild
        self.user_id = user_id
        self.page = 0
        self.selected_feature = next(iter(CHANNEL_FEATURES))
        self.selected_setting = next(iter(SETTING_OPTIONS))
        self.selected_channel_id: int | None = None
        self.channels: dict[str, int | None] = {}
        self.settings: dict[str, object] = {}
        self.member_count_label = DEFAULT_MEMBER_COUNT_LABEL
        self.status = "Choose a page below to get started."

        self.feature_select = discord.ui.Select(
            placeholder="Choose a bot feature...",
            options=[
                discord.SelectOption(label=description, value=feature)
                for feature, description in CHANNEL_FEATURES.items()
            ],
            row=0,
        )
        self.feature_select.callback = self._feature_selected
        self.add_item(self.feature_select)

        self.channel_select = discord.ui.ChannelSelect(
            placeholder="Choose a text channel or category...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.category],
            row=1,
        )
        self.channel_select.callback = self._channel_selected
        self.add_item(self.channel_select)

        self.back_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=2)
        self.back_button.callback = self._back
        self.add_item(self.back_button)

        self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary, row=2)
        self.next_button.callback = self._next
        self.add_item(self.next_button)

        self.configure_button = discord.ui.Button(label="Configure channels", style=discord.ButtonStyle.primary, row=3)
        self.configure_button.callback = self._configure
        self.add_item(self.configure_button)

        self.refresh_button = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, row=3)
        self.refresh_button.callback = self._refresh
        self.add_item(self.refresh_button)

        self.save_button = discord.ui.Button(label="Save channel", style=discord.ButtonStyle.success, row=3)
        self.save_button.callback = self._save
        self.add_item(self.save_button)

        self.clear_button = discord.ui.Button(label="Clear channel", style=discord.ButtonStyle.danger, row=4)
        self.clear_button.callback = self._clear
        self.add_item(self.clear_button)

        self.sync_button = discord.ui.Button(label="Sync members", style=discord.ButtonStyle.success, row=4)
        self.sync_button.callback = self._sync
        self.add_item(self.sync_button)

        self.close_button = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, row=4)
        self.close_button.callback = self._close
        self.add_item(self.close_button)

        self.member_count_button = discord.ui.Button(label="Member count text", style=discord.ButtonStyle.primary, row=4)
        self.member_count_button.callback = self._member_count_settings
        self.add_item(self.member_count_button)

    async def load(self) -> None:
        self.channels = await database.get_channels(self.guild.id)
        label = await database.get_setting(self.guild.id, MEMBER_COUNT_LABEL_SETTING, DEFAULT_MEMBER_COUNT_LABEL)
        self.member_count_label = str(label or DEFAULT_MEMBER_COUNT_LABEL)
        self.settings = {
            setting: await database.get_setting(self.guild.id, setting)
            for setting in SETTING_OPTIONS
        }

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if settings.OWNER_USER_ID is None or interaction.user.id != settings.OWNER_USER_ID:
            if getattr(self.guild, "owner_id", None) != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message("You no longer have Manage Server permission to use this setup panel.", ephemeral=True)
                return False
        if getattr(interaction.client, "maintenance_mode", False):
            owner_id = settings.OWNER_USER_ID
            if owner_id is None or interaction.user.id != owner_id:
                await interaction.response.send_message("The bot is currently in maintenance mode.", ephemeral=True)
                return False
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This setup panel belongs to the person who opened it.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("You no longer have Manage Server permission to use this setup panel.", ephemeral=True)
            return False
        return True

    def _embed(self) -> discord.Embed:
        if self.page == 0:
            lines = []
            for feature, description in CHANNEL_FEATURES.items():
                channel_id = self.channels.get(feature)
                channel = self.guild.get_channel(channel_id) if channel_id else None
                lines.append(f"**{description}:** {channel.mention if channel else 'Not configured'}")
            title = "Bot setup · Overview"
            description = "Review the channels currently assigned to TGPDB. Use **Configure channels** to change them."
            fields = [("Configured channels", "\n".join(lines), False)]
        elif self.page == 1:
            selected_channel = self.guild.get_channel(self.selected_channel_id) if self.selected_channel_id else None
            title = "Bot setup · Configure channels"
            description = "Select a feature, choose a text channel or category, then press **Save channel**."
            fields = [
                ("Feature", CHANNEL_FEATURES[self.selected_feature], True),
                ("Channel", selected_channel.mention if selected_channel else "No channel selected", True),
                ("Member count text", self.member_count_label, False),
            ]
        elif self.page == 2:
            title = "Bot setup · Guild data"
            description = "TGPDB stores this server's configuration and member snapshot separately from every other server."
            fields = [
                ("Guild", f"{self.guild.name}\n`{self.guild.id}`", True),
                ("Members currently visible", str(len(self.guild.members)), True),
                ("Stored snapshot", "Use **Sync members** to refresh it now.", False),
            ]
        else:
            values = []
            for setting, label in SETTING_OPTIONS.items():
                current = self._setting_display(setting)
                values.append(f"**{label}:** {current}")
            title = "Bot setup · Feature settings"
            description = "Select a setting, then use **Configure setting**. Automod and anti-raid are disabled by default."
            fields = [("Server feature settings", "\n".join(values), False)]

        embed = discord.Embed(title=title, description=description, colour=discord.Colour.blurple())
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        if self.status:
            embed.set_footer(text=f"Page {self.page + 1}/4 · {self.status}")
        else:
            embed.set_footer(text=f"Page {self.page + 1}/4")
        return embed

    def _setting_display(self, setting: str) -> str:
        if setting in {"automod_enabled", "antiraid_enabled", "levels_enabled", "health_dashboard_enabled"}:
            return "Enabled" if self.settings.get(setting, False) else "Disabled"
        if setting == "autorole_id":
            role = self.guild.get_role(self.settings.get(setting)) if self.settings.get(setting) else None
            return role.mention if role else "Not configured"
        if setting == "starboard_threshold":
            return f"{self.settings.get(setting, 3)} reactions"
        return str(self.settings.get(setting) or "Not configured")[:100]

    def _update_items(self) -> None:
        configuring = self.page == 1
        self.feature_select.disabled = self.page not in {1, 3}
        if self.page == 3:
            self.feature_select.options = [discord.SelectOption(label=label, value=key) for key, label in SETTING_OPTIONS.items()]
            self.feature_select.placeholder = "Choose a feature setting..."
        else:
            self.feature_select.options = [discord.SelectOption(label=label, value=key) for key, label in CHANNEL_FEATURES.items()]
            self.feature_select.placeholder = "Choose a bot channel..."
        self.channel_select.disabled = not configuring
        self.save_button.disabled = not configuring
        self.clear_button.disabled = not configuring
        self.member_count_button.disabled = not configuring or self.selected_feature != "member_count"
        self.configure_button.disabled = self.page == 1
        self.configure_button.label = "Feature settings" if self.page == 2 else "Configure channels" if self.page == 0 else "Configure setting"
        self.sync_button.disabled = self.page != 2
        self.back_button.disabled = self.page == 0
        self.next_button.disabled = self.page == 3

    async def edit(self, interaction: discord.Interaction) -> None:
        self._update_items()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self._embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _feature_selected(self, interaction: discord.Interaction) -> None:
        selected = self.feature_select.values[0]
        if self.page == 3:
            self.selected_setting = selected
            self.status = "Setting selected. Press Configure setting to edit it."
        else:
            self.selected_feature = selected
            self.selected_channel_id = self.channels.get(self.selected_feature)
            self.status = "Feature selected. Choose a channel to continue."
        await self.edit(interaction)

    async def _channel_selected(self, interaction: discord.Interaction) -> None:
        self.selected_channel_id = self.channel_select.values[0].id
        self.status = "Channel selected. Press Save channel to apply it."
        await self.edit(interaction)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self.edit(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(3, self.page + 1)
        await self.edit(interaction)

    async def _configure(self, interaction: discord.Interaction) -> None:
        if self.page == 0:
            self.page = 1
            self.selected_channel_id = self.channels.get(self.selected_feature)
            await self.edit(interaction)
            return
        if self.page == 2:
            self.page = 3
            await self.edit(interaction)
            return
        if self.selected_setting in {"welcome_message", "farewell_message"}:
            current = str(self.settings.get(self.selected_setting) or "")
        elif self.selected_setting == "starboard_threshold":
            current = str(self.settings.get(self.selected_setting) or 3)
        elif self.selected_setting == "autorole_id":
            role = self.guild.get_role(self.settings.get(self.selected_setting)) if self.settings.get(self.selected_setting) else None
            current = str(role.id) if role else ""
        else:
            current = ""
        prompts = {
            "welcome_message": "Welcome message template",
            "farewell_message": "Farewell message template",
            "autorole_id": "Role ID or mention",
            "starboard_threshold": "Reaction threshold",
        }
        if self.selected_setting in {"automod_enabled", "antiraid_enabled", "levels_enabled", "health_dashboard_enabled"}:
            enabled = not bool(self.settings.get(self.selected_setting, False))
            await database.set_setting(self.guild.id, self.selected_setting, enabled)
            self.status = f"{SETTING_OPTIONS[self.selected_setting]} {'enabled' if enabled else 'disabled'}."
            await self.load()
            await self.edit(interaction)
            return
        await interaction.response.send_modal(FeatureSettingModal(self, self.selected_setting, current, prompts[self.selected_setting]))

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await self.load()
        self.status = "Configuration refreshed."
        await self.edit(interaction)

    async def _save(self, interaction: discord.Interaction) -> None:
        if self.selected_channel_id is None:
            await interaction.response.send_message("Choose a channel before saving.", ephemeral=True)
            return
        await interaction.response.defer()
        await database.set_channel(self.guild.id, self.selected_feature, self.selected_channel_id)
        await database.upsert_guild(self.guild)
        if self.selected_feature == "member_count":
            await update_member_count_channel(self.guild, force=True)
        await self.load()
        self.page = 0
        self.status = f"Saved {CHANNEL_FEATURES[self.selected_feature].lower()}."
        await self.edit(interaction)

    async def _clear(self, interaction: discord.Interaction) -> None:
        await database.set_channel(self.guild.id, self.selected_feature, None)
        await self.load()
        self.selected_channel_id = None
        self.page = 0
        self.status = f"Cleared {CHANNEL_FEATURES[self.selected_feature].lower()}."
        await self.edit(interaction)

    async def _member_count_settings(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MemberCountModal(self, self.member_count_label))

    async def _sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await database.sync_guild_members(self.guild)
        await update_member_count_channel(self.guild, force=True)
        self.status = f"Saved {len(self.guild.members)} member(s)."
        await interaction.edit_original_response(embed=self._embed(), view=self)

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
            await ctx.send(f"❌ Unknown feature. Choose one of: `{choices}`.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        await database.set_channel(ctx.guild.id, feature, channel.id)
        await database.upsert_guild(ctx.guild)
        if feature == "member_count":
            await update_member_count_channel(ctx.guild, force=True)
        await ctx.send(f"✅ {CHANNEL_FEATURES[feature]} will use {channel.mention}.", ephemeral=True)

    @botsetup.command(name="clear", description="Clear a configured bot feature channel.")
    async def clear(self, ctx: commands.Context, feature: str) -> None:
        """Remove a channel assignment for this guild."""
        feature = feature.lower().strip()
        if feature not in CHANNEL_FEATURES:
            await ctx.send(f"❌ Unknown feature. Choose one of: `{', '.join(CHANNEL_FEATURES)}`.", ephemeral=True)
            return
        await database.set_channel(ctx.guild.id, feature, None)
        await ctx.send(f"✅ Cleared the {CHANNEL_FEATURES[feature].lower()} channel.", ephemeral=True)

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
            f"✅ Member count channel configured in {channel.mention}.",
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
        await ctx.send(f"✅ Saved guild information and {len(ctx.guild.members)} member(s).", ephemeral=True)

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
            await ctx.send("❌ Only server owners or administrators can change bot setup.", ephemeral=True)
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ I could not resolve that channel. Mention a text channel.", ephemeral=True)
        else:
            logger.exception("Bot setup command failed", exc_info=error)
            await ctx.send("❌ Bot setup failed.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotSetup())
