"""Persistent reaction-role setup and assignment."""

from __future__ import annotations

import re

import discord
from discord.ext import commands

import database


RANK_EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
MESSAGE_LINK_RE = re.compile(r"/channels/\d+/(\d+)/(\d+)")


def _message_reference(value: str) -> tuple[int | None, int | None]:
    value = value.strip()
    match = MESSAGE_LINK_RE.search(value)
    if match:
        return int(match.group(1)), int(match.group(2))
    if value.isdigit():
        return None, int(value)
    return None, None


class MessageReferenceModal(discord.ui.Modal, title="Reaction-role message"):
    reference = discord.ui.TextInput(
        label="Message ID or message link",
        placeholder="Paste the message link, or enter its ID",
        min_length=10,
        max_length=200,
        required=True,
    )

    def __init__(self, panel: "ReactionRolePanel") -> None:
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        channel_id, message_id = _message_reference(str(self.reference.value))
        if message_id is None:
            await interaction.followup.send("❌ That is not a valid message ID or Discord message link.", ephemeral=True)
            return
        message = await self.panel.find_message(channel_id, message_id)
        if message is None:
            await interaction.followup.send(
                "❌ I could not find that message. A raw ID must be in a text channel I can read; a message link is preferred.",
                ephemeral=True,
            )
            return
        self.panel.channel_id = message.channel.id
        self.panel.message_id = message.id
        self.panel.status = f"Message found in {message.channel.mention}. Select the roles to assign."
        await interaction.edit_original_response(embed=self.panel.embed(), view=self.panel)


class ReactionRolePanel(discord.ui.View):
    """Owner-only setup panel for one reaction-role message."""

    def __init__(self, guild: discord.Guild, user_id: int) -> None:
        super().__init__(timeout=600)
        self.guild = guild
        self.user_id = user_id
        self.channel_id: int | None = None
        self.message_id: int | None = None
        self.selected_roles: list[discord.Role] = []
        self.status = "Start by choosing the message that should receive role reactions."

        self.message_button = discord.ui.Button(label="Choose message", style=discord.ButtonStyle.primary)
        self.message_button.callback = self._choose_message
        self.add_item(self.message_button)

        self.role_select = discord.ui.RoleSelect(
            placeholder="Select one or more roles...",
            min_values=1,
            max_values=min(len(RANK_EMOJIS), 25),
            row=1,
        )
        self.role_select.callback = self._roles_selected
        self.add_item(self.role_select)

        self.save_button = discord.ui.Button(label="Save reaction roles", style=discord.ButtonStyle.success)
        self.save_button.callback = self._save
        self.add_item(self.save_button)

        self.close_button = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary)
        self.close_button.callback = self._close
        self.add_item(self.close_button)

    def embed(self) -> discord.Embed:
        message = f"`{self.message_id}` in <#{self.channel_id}>" if self.message_id and self.channel_id else "Not selected"
        if self.selected_roles:
            roles = "\n".join(f"{RANK_EMOJIS[index]} {role.mention}" for index, role in enumerate(self.selected_roles))
        else:
            roles = "No roles selected yet."
        embed = discord.Embed(
            title="Reaction roles setup",
            description="Choose a message, select roles, then save. Roles receive numeric rank reactions in selection order.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Target message", value=message, inline=False)
        embed.add_field(name="Rank assignments", value=roles, inline=False)
        embed.set_footer(text=self.status)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This reaction-role panel belongs to the person who opened it.", ephemeral=True)
            return False
        return True

    async def find_message(self, channel_id: int | None, message_id: int) -> discord.Message | None:
        if channel_id is not None:
            channel = self.guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    return await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return None
            return None
        for channel in self.guild.text_channels:
            try:
                return await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        return None

    async def _choose_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MessageReferenceModal(self))

    async def _roles_selected(self, interaction: discord.Interaction) -> None:
        self.selected_roles = list(self.role_select.values)
        self.status = f"Selected {len(self.selected_roles)} role(s)."
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _save(self, interaction: discord.Interaction) -> None:
        if self.message_id is None or self.channel_id is None:
            await interaction.response.send_message("❌ Choose a target message first.", ephemeral=True)
            return
        if not self.selected_roles:
            await interaction.response.send_message("❌ Select at least one role first.", ephemeral=True)
            return
        bot_member = self.guild.me
        if bot_member is None:
            await interaction.response.send_message("❌ I could not determine my role permissions in this server.", ephemeral=True)
            return
        moderator = self.guild.get_member(self.user_id)
        if moderator is None or not moderator.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ You need Manage Roles to configure reaction roles.", ephemeral=True)
            return
        manageable = [
            role
            for role in self.selected_roles
            if not role.is_default()
            and not role.managed
            and role < bot_member.top_role
            and (moderator == self.guild.owner or role < moderator.top_role)
        ]
        if len(manageable) != len(self.selected_roles):
            await interaction.response.send_message(
                "❌ Every selected role must be below both your highest role and my highest role. Managed roles are not supported.",
                ephemeral=True,
            )
            return
        channel = self.guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ The target channel is no longer available.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            message = await channel.fetch_message(self.message_id)
            mappings = [(RANK_EMOJIS[index], role.id, index + 1) for index, role in enumerate(self.selected_roles)]
            await database.replace_reaction_roles(self.guild.id, self.message_id, self.channel_id, mappings)
            for emoji, _, _ in mappings:
                await message.add_reaction(emoji)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            await interaction.followup.send(
                "❌ I could not update that message. Check that I can view it, add reactions, and manage the selected roles.",
                ephemeral=True,
            )
            return
        self.status = "Saved. Members can now react to receive their roles."
        await interaction.edit_original_response(embed=self.embed(), view=self)

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)


class ReactionRoles(commands.Cog):
    """Assign and remove roles through reactions on a configured message."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="reactionroles", description="Create a role-reaction message setup panel.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def reactionroles(self, ctx: commands.Context) -> None:
        panel = ReactionRolePanel(ctx.guild, ctx.author.id)
        await ctx.send(embed=panel.embed(), view=panel, ephemeral=ctx.interaction is not None)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or self.bot.user and payload.user_id == self.bot.user.id:
            return
        role_id = await database.get_reaction_role(payload.guild_id, payload.message_id, str(payload.emoji))
        if role_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = payload.member or (await guild.fetch_member(payload.user_id) if guild else None)
        role = guild.get_role(role_id) if guild else None
        bot_member = guild.me if guild else None
        if member and role and bot_member and not member.bot and role < bot_member.top_role:
            try:
                await member.add_roles(role, reason="Reaction role assignment")
            except (discord.Forbidden, discord.HTTPException):
                return

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        role_id = await database.get_reaction_role(payload.guild_id, payload.message_id, str(payload.emoji))
        if role_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(role_id)
        try:
            member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if member and role and not member.bot:
            try:
                await member.remove_roles(role, reason="Reaction role removal")
            except (discord.Forbidden, discord.HTTPException):
                return

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if ctx.cog is self and isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You need Manage Server to configure reaction roles.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
