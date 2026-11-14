"""Moderation commands for TGPDB.

The commands are hybrid commands, so they are available as both slash commands
and traditional prefix commands.  Every mutating command checks Discord's
permissions and the bot's role hierarchy before doing anything.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Optional

import discord
from discord.ext import commands
import settings
import database


logger = logging.getLogger(__name__)
MAX_REASON_LENGTH = 480
MAX_PURGE = 100


class Moderation(commands.Cog):
    """Tools commonly needed by server moderators and owners."""

    async def _log_action(
        self,
        ctx: commands.Context,
        action: str,
        *,
        target: Optional[discord.abc.User] = None,
        reason: Optional[str] = None,
        details: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        delete_days: Optional[int] = None,
    ) -> None:
        """Send a moderation action to the configured log channel.

        Logging is deliberately best-effort: a missing channel or a temporary
        Discord error must not make a successful moderation action look failed.
        """
        if ctx.guild and target is not None:
            try:
                await database.record_moderation_action(
                    ctx.guild.id,
                    target.id,
                    ctx.author.id,
                    action,
                    reason=reason,
                    duration_seconds=duration_seconds,
                    delete_days=delete_days,
                )
            except Exception:
                logger.exception("Could not persist moderation action %s", action)
        try:
            channel_id = await database.get_channel(ctx.guild.id, "moderation_log") if ctx.guild else None
        except Exception:
            logger.exception("Could not read the moderation log channel")
            channel_id = None
        channel_id = channel_id or settings.LOG_CHANNEL_ID
        if not channel_id:
            logger.warning("Moderation log channel is not configured")
            return

        channel = ctx.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await ctx.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("Could not access moderation log channel %s", channel_id)
                return

        if not isinstance(channel, discord.TextChannel):
            logger.warning("Moderation log channel %s is not a text channel", channel_id)
            return

        embed = discord.Embed(
            title=f"Moderation: {action}",
            colour=discord.Colour.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Moderator", value=f"{ctx.author.mention} (`{ctx.author.id}`)", inline=False)
        if ctx.guild:
            embed.add_field(name="Server", value=f"{ctx.guild.name} (`{ctx.guild.id}`)", inline=False)
        if target is not None:
            embed.add_field(name="Target", value=f"{target} (`{target.id}`)", inline=False)
        if reason:
            embed.add_field(name="Reason", value=reason[:1024], inline=False)
        if details:
            embed.add_field(name="Details", value=details[:1024], inline=False)
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not write moderation action to channel %s", channel_id)

    @staticmethod
    def _reason(reason: Optional[str], moderator: discord.Member) -> str:
        value = (reason or "No reason provided").strip()
        return f"{value[:MAX_REASON_LENGTH]} | Moderator: {moderator} ({moderator.id})"

    @staticmethod
    def _can_act(moderator: discord.Member, target: discord.Member) -> Optional[str]:
        if target == moderator:
            return "You cannot moderate yourself."
        if target == moderator.guild.owner:
            return "The server owner cannot be moderated."
        if target.top_role >= moderator.top_role and moderator != moderator.guild.owner:
            return "That member has an equal or higher role than you."
        return None

    @staticmethod
    def _bot_can_act(guild: discord.Guild, target: discord.Member) -> Optional[str]:
        me = guild.me
        if me is None:
            return "I could not resolve my member record in this server."
        if target == me or target.top_role >= me.top_role:
            return "That member has an equal or higher role than my highest role."
        return None

    async def _target_error(self, ctx: commands.Context, target: discord.Member) -> bool:
        error = self._can_act(ctx.author, target)
        if error is None:
            error = self._bot_can_act(ctx.guild, target) if ctx.guild else "This command must be used in a server."
        if error:
            await ctx.send(f"❌ {error}", ephemeral=True)
            return True
        return False

    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @commands.guild_only()
    @commands.has_guild_permissions(kick_members=True)
    @commands.bot_has_guild_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """Kick a member."""
        if await self._target_error(ctx, member):
            return
        await member.kick(reason=self._reason(reason, ctx.author))
        await self._log_action(ctx, "Kick", target=member, reason=reason)
        await ctx.send(f"✅ Kicked **{member}**.", ephemeral=True)

    @commands.hybrid_command(name="ban", description="Ban a member from the server.")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, delete_days: commands.Range[int, 0, 7] = 0, *, reason: Optional[str] = None) -> None:
        """Ban a member and optionally delete up to seven days of messages."""
        if await self._target_error(ctx, member):
            return
        await member.ban(delete_message_days=delete_days, reason=self._reason(reason, ctx.author))
        await self._log_action(ctx, "Ban", target=member, reason=reason, details=f"Deleted {delete_days} day(s) of messages", delete_days=delete_days)
        await ctx.send(f"✅ Banned **{member}** (deleted {delete_days} day(s) of messages).", ephemeral=True)

    @commands.hybrid_command(name="unban", description="Unban a user by ID.")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: str, *, reason: Optional[str] = None) -> None:
        """Unban a user using their numeric Discord ID."""
        if not user_id.isdigit():
            await ctx.send("❌ User ID must be a number.", ephemeral=True)
            return
        try:
            user = discord.Object(id=int(user_id))
            await ctx.guild.unban(user, reason=self._reason(reason, ctx.author))
        except (discord.NotFound, discord.HTTPException) as exc:
            await ctx.send(f"❌ Could not unban that user: {exc}", ephemeral=True)
            return
        await self._log_action(ctx, "Unban", target=user, reason=reason)
        await ctx.send(f"✅ Unbanned user `{user_id}`.", ephemeral=True)

    @commands.hybrid_command(name="softban", description="Ban and immediately unban a member to clear their messages.")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_guild_permissions(ban_members=True)
    async def softban(self, ctx: commands.Context, member: discord.Member, delete_days: commands.Range[int, 0, 7] = 1, *, reason: Optional[str] = None) -> None:
        """Ban and unban a member, removing recent messages."""
        if await self._target_error(ctx, member):
            return
        audit_reason = self._reason(reason, ctx.author)
        await member.ban(delete_message_days=delete_days, reason=audit_reason)
        try:
            await ctx.guild.unban(member, reason=audit_reason)
        except (discord.Forbidden, discord.HTTPException) as error:
            await self._log_action(
                ctx,
                "Ban",
                target=member,
                reason=reason,
                details=f"Softban unban step failed; member remains banned: {error}",
                delete_days=delete_days,
            )
            await ctx.send("⚠️ The ban succeeded, but unbanning failed. The member remains banned; use `/unban` to recover.", ephemeral=True)
            return
        await self._log_action(ctx, "Softban", target=member, reason=reason, details=f"Deleted {delete_days} day(s) of messages", delete_days=delete_days)
        await ctx.send(f"✅ Soft-banned **{member}**.", ephemeral=True)

    @commands.hybrid_command(name="timeout", description="Timeout a member for up to 28 days.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, minutes: commands.Range[int, 1, 40320], *, reason: Optional[str] = None) -> None:
        """Timeout a member for a number of minutes."""
        if await self._target_error(ctx, member):
            return
        await member.timeout(timedelta(minutes=minutes), reason=self._reason(reason, ctx.author))
        await self._log_action(ctx, "Timeout", target=member, reason=reason, details=f"Duration: {minutes} minute(s)", duration_seconds=minutes * 60)
        await ctx.send(f"✅ Timed out **{member}** for {minutes} minute(s).", ephemeral=True)

    @commands.hybrid_command(name="untimeout", description="Remove a member's timeout.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(moderate_members=True)
    async def untimeout(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """Remove a timeout."""
        if await self._target_error(ctx, member):
            return
        await member.timeout(None, reason=self._reason(reason, ctx.author))
        await self._log_action(ctx, "Remove timeout", target=member, reason=reason)
        await ctx.send(f"✅ Removed the timeout from **{member}**.", ephemeral=True)

    @commands.hybrid_group(name="role", description="Manage member roles.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def role(self, ctx: commands.Context) -> None:
        """Manage roles with /role add and /role remove."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `role add` or `role remove`.", ephemeral=True)

    async def _change_role(self, ctx: commands.Context, member: discord.Member, role: discord.Role, adding: bool) -> None:
        if await self._target_error(ctx, member):
            return
        moderator = ctx.author
        if role.is_default() or role >= ctx.guild.me.top_role:
            await ctx.send("❌ I cannot manage that role.", ephemeral=True)
            return
        if moderator != ctx.guild.owner and role >= moderator.top_role:
            await ctx.send("❌ You cannot manage a role at or above your highest role.", ephemeral=True)
            return
        if adding:
            await member.add_roles(role, reason=self._reason(None, ctx.author))
            action = "Added"
        else:
            await member.remove_roles(role, reason=self._reason(None, ctx.author))
            action = "Removed"
        await self._log_action(ctx, f"Role {action.lower()}", target=member, details=f"Role: {role.name} (`{role.id}`)")
        await ctx.send(f"✅ {action} **{role.name}** {'to' if adding else 'from'} **{member}**.", ephemeral=True)

    @role.command(name="add", description="Add a role to a member.")
    async def role_add(self, ctx: commands.Context, member: discord.Member, role: discord.Role) -> None:
        await self._change_role(ctx, member, role, True)

    @role.command(name="remove", description="Remove a role from a member.")
    async def role_remove(self, ctx: commands.Context, member: discord.Member, role: discord.Role) -> None:
        await self._change_role(ctx, member, role, False)

    @commands.hybrid_command(name="purge", description="Delete up to 100 recent messages.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_guild_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: commands.Range[int, 1, MAX_PURGE]) -> None:
        """Bulk delete recent messages."""
        deleted = await ctx.channel.purge(limit=amount, reason=f"Purged by {ctx.author} ({ctx.author.id})")
        await self._log_action(ctx, "Purge", details=f"Deleted {len(deleted)} message(s) in {ctx.channel.mention}")
        await ctx.send(f"✅ Deleted {len(deleted)} message(s).", ephemeral=True, delete_after=5)

    @commands.hybrid_command(name="slowmode", description="Set channel slowmode in seconds.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: commands.Range[int, 0, 21600]) -> None:
        """Set the current text channel's slowmode."""
        await ctx.channel.edit(slowmode_delay=seconds, reason=f"Changed by {ctx.author} ({ctx.author.id})")
        await self._log_action(ctx, "Slowmode", details=f"Channel: {ctx.channel.mention}; Delay: {seconds} second(s)")
        await ctx.send(f"✅ Slowmode set to {seconds} second(s).", ephemeral=True)

    @commands.hybrid_command(name="lock", description="Lock the current channel for @everyone.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context) -> None:
        """Prevent @everyone from sending messages in the current channel."""
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Locked by {ctx.author} ({ctx.author.id})")
        await self._log_action(ctx, "Channel lock", details=f"Channel: {ctx.channel.mention}")
        await ctx.send("✅ Channel locked.", ephemeral=True)

    @commands.hybrid_command(name="unlock", description="Unlock the current channel for @everyone.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context) -> None:
        """Allow @everyone to send messages in the current channel again."""
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {ctx.author} ({ctx.author.id})")
        await self._log_action(ctx, "Channel unlock", details=f"Channel: {ctx.channel.mention}")
        await ctx.send("✅ Channel unlocked.", ephemeral=True)

    @commands.hybrid_command(name="nickname", description="Set or clear a member's nickname.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_nicknames=True)
    @commands.bot_has_guild_permissions(manage_nicknames=True)
    async def nickname(self, ctx: commands.Context, member: discord.Member, nickname: Optional[str] = None) -> None:
        """Set a nickname, or clear it when no nickname is supplied."""
        if await self._target_error(ctx, member):
            return
        await member.edit(nick=nickname, reason=f"Nickname changed by {ctx.author} ({ctx.author.id})")
        await self._log_action(ctx, "Nickname change", target=member, details=f"New nickname: {nickname or '[cleared]'}")
        await ctx.send(f"✅ Nickname {'cleared' if nickname is None else 'updated'} for **{member}**.", ephemeral=True)

    @commands.hybrid_command(name="warn", description="Send a formal warning to a member.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: Optional[str] = None) -> None:
        """DM a member a warning and record it in the audit log."""
        if await self._target_error(ctx, member):
            return
        message = f"You have been warned in **{ctx.guild.name}**. Reason: {reason or 'No reason provided'}"
        try:
            await member.send(message[:1900])
            dm_status = " The member was notified by DM."
        except discord.HTTPException:
            dm_status = " I could not DM the member."
        logger.info("Warning issued in %s to %s by %s: %s", ctx.guild.id, member.id, ctx.author.id, reason)
        await self._log_action(ctx, "Warning", target=member, reason=reason, details=dm_status.strip())
        await ctx.send(f"✅ Warning issued to **{member}**.{dm_status}", ephemeral=True)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if ctx.cog is not self:
            return
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You do not have permission to use that command.", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("❌ I am missing a required Discord permission.", ephemeral=True)
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ I could not find that member.", ephemeral=True)
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ One of the arguments is invalid. Check the command help.", ephemeral=True)
        else:
            logger.error("Moderation command failed", exc_info=error)
            await ctx.send("❌ Something went wrong while running that command.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation())
