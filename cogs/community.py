"""Welcome, safety, scheduling, analytics, starboard, and leveling features."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import json
import logging
import re
import string
import time
from typing import Optional

import discord
from discord.ext import commands, tasks

import database


logger = logging.getLogger(__name__)
MESSAGE_LOG_FEATURE = "moderation_log"


def _format_message(template: str, member: discord.Member) -> str:
    try:
        return template.format(
            user=member.mention,
            username=member.display_name,
            server=member.guild.name,
            member_count=member.guild.member_count or len(member.guild.members),
        )[:2000]
    except (AttributeError, IndexError, KeyError, ValueError):
        return template[:2000]


def _validate_message_template(template: str) -> Optional[str]:
    """Return an error for unsupported or malformed template fields."""
    allowed = {"user", "username", "server", "member_count"}
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is not None and field_name not in allowed:
                return f"Unsupported placeholder `{field_name}`. Use user, username, server, or member_count."
            if format_spec or conversion:
                return "Only simple placeholders such as `{user}` are supported."
    except ValueError:
        return "The template contains unmatched braces."
    return None


class Community(commands.Cog):
    """Configurable community automation with safe defaults."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.raid_joins: dict[int, deque[float]] = defaultdict(deque)
        self.xp_cooldowns: dict[tuple[int, int], float] = {}
        self.sent_analytics_days: set[str] = set()
        self.worker.start()

    def cog_unload(self) -> None:
        self.worker.cancel()

    def clear_cache(self) -> None:
        self.xp_cooldowns.clear()
        self.raid_joins.clear()
        self.sent_analytics_days.clear()

    async def _channel(self, guild: discord.Guild, feature: str) -> Optional[discord.TextChannel]:
        channel_id = await database.get_channel(guild.id, feature)
        channel = guild.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _log(self, guild: discord.Guild, title: str, description: str, *, image_url: Optional[str] = None) -> bool:
        channel = await self._channel(guild, MESSAGE_LOG_FEATURE)
        if channel is None:
            return False
        embed = discord.Embed(title=title, description=description[:4096], colour=discord.Colour.orange(), timestamp=discord.utils.utcnow())
        if image_url:
            embed.set_image(url=image_url)
        try:
            await channel.send(embed=embed)
            return True
        except discord.HTTPException:
            logger.exception("Could not write community log in guild %s", guild.id)
            return False

    async def _automod_blocked(self, message: discord.Message) -> bool:
        if not await database.get_setting(message.guild.id, "automod_enabled", False):
            return False
        words = await database.get_setting(message.guild.id, "automod_words", [])
        lowered = message.content.casefold()
        blocked_word = any(str(word).casefold() in lowered for word in words if str(word).strip())
        invite = bool(re.search(r"discord(?:\.gg|(?:app)?\.com/invite)/", lowered))
        return blocked_word or invite

    @tasks.loop(seconds=30)
    async def worker(self) -> None:
        now = datetime.now(timezone.utc)
        for item in await database.get_due_scheduled_messages(now.isoformat()):
            channel = self.bot.get_channel(item["channel_id"])
            delivered = False
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(item["content"][:2000])
                    delivered = True
                except discord.HTTPException:
                    logger.exception("Scheduled message %s failed", item["schedule_id"])
            if not delivered:
                continue
            next_send = None
            if item["repeat_minutes"]:
                next_send = (now + timedelta(minutes=item["repeat_minutes"])).isoformat()
            await database.complete_scheduled_message(item["schedule_id"], next_send)

        yesterday = (now.date() - timedelta(days=1)).isoformat()
        if now.hour == 0 and yesterday not in self.sent_analytics_days:
            for guild in self.bot.guilds:
                channel = await self._channel(guild, "analytics")
                stats = await database.get_daily_stat(guild.id, yesterday)
                if channel and stats:
                    embed = discord.Embed(title=f"Daily analytics · {yesterday}", colour=discord.Colour.blurple())
                    embed.add_field(name="Messages", value=f"{stats['messages']:,}")
                    embed.add_field(name="Joins", value=f"{stats['joins']:,}")
                    embed.add_field(name="Leaves", value=f"{stats['leaves']:,}")
                    embed.add_field(name="Commands", value=f"{stats['commands']:,}")
                    try:
                        await channel.send(embed=embed)
                    except discord.HTTPException:
                        logger.exception("Could not send analytics for guild %s", guild.id)
            self.sent_analytics_days.add(yesterday)

    @worker.before_loop
    async def before_worker(self) -> None:
        await self.bot.wait_until_ready()

    @commands.hybrid_group(name="welcome", description="Configure welcome messages.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def welcome(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/welcome set`, `/welcome disable`, or `/welcome preview`.", ephemeral=True)

    @welcome.command(name="set", description="Set the welcome message template.")
    async def welcome_set(self, ctx: commands.Context, *, message: str) -> None:
        error = _validate_message_template(message)
        if error:
            await ctx.send(f"❌ {error}", ephemeral=True)
            return
        await database.set_setting(ctx.guild.id, "welcome_message", message[:2000])
        await ctx.send("✅ Welcome message saved. Use `{user}`, `{username}`, `{server}`, and `{member_count}`.", ephemeral=True)

    @welcome.command(name="disable", description="Disable welcome messages.")
    async def welcome_disable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "welcome_message", None)
        await ctx.send("✅ Welcome messages disabled.", ephemeral=True)

    @welcome.command(name="preview", description="Preview the configured welcome message.")
    async def welcome_preview(self, ctx: commands.Context) -> None:
        template = await database.get_setting(ctx.guild.id, "welcome_message", "Welcome {user} to **{server}**!")
        await ctx.send(_format_message(str(template), ctx.author), ephemeral=True)

    @commands.hybrid_group(name="farewell", description="Configure farewell messages.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def farewell(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/farewell set` or `/farewell disable`.", ephemeral=True)

    @farewell.command(name="set", description="Set the farewell message template.")
    async def farewell_set(self, ctx: commands.Context, *, message: str) -> None:
        error = _validate_message_template(message)
        if error:
            await ctx.send(f"❌ {error}", ephemeral=True)
            return
        await database.set_setting(ctx.guild.id, "farewell_message", message[:2000])
        await ctx.send("✅ Farewell message saved.", ephemeral=True)

    @farewell.command(name="disable", description="Disable farewell messages.")
    async def farewell_disable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "farewell_message", None)
        await ctx.send("✅ Farewell messages disabled.", ephemeral=True)

    @commands.hybrid_group(name="autorole", description="Configure the role assigned to new members.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    async def autorole(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/autorole set @role` or `/autorole clear`.", ephemeral=True)

    @autorole.command(name="set", description="Assign a role automatically when members join.")
    async def autorole_set(self, ctx: commands.Context, role: discord.Role) -> None:
        if ctx.guild.me is None or role >= ctx.guild.me.top_role or role.is_default() or role.managed:
            await ctx.send("❌ I can only assign a normal role below my highest role.", ephemeral=True)
            return
        if ctx.author != ctx.guild.owner and role >= ctx.author.top_role:
            await ctx.send("❌ You cannot configure a role at or above your highest role.", ephemeral=True)
            return
        await database.set_setting(ctx.guild.id, "autorole_id", role.id)
        await ctx.send(f"✅ New members will receive {role.mention}.", ephemeral=True)

    @autorole.command(name="clear", description="Disable automatic roles.")
    async def autorole_clear(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "autorole_id", None)
        await ctx.send("✅ Autorole disabled.", ephemeral=True)

    @commands.hybrid_group(name="automod", description="Configure automod; it is disabled by default.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def automod(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            enabled = await database.get_setting(ctx.guild.id, "automod_enabled", False)
            await ctx.send(f"Automod is currently **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    @automod.command(name="enable", description="Enable basic spam, invite, and word automod.")
    async def automod_enable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "automod_enabled", True)
        await ctx.send("✅ Automod enabled.", ephemeral=True)

    @automod.command(name="disable", description="Disable automod.")
    async def automod_disable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "automod_enabled", False)
        await ctx.send("✅ Automod disabled.", ephemeral=True)

    @automod.command(name="word", description="Add a blocked word or phrase.")
    async def automod_word(self, ctx: commands.Context, *, word: str) -> None:
        words = await database.get_setting(ctx.guild.id, "automod_words", [])
        words = list(words) if isinstance(words, list) else []
        if word.casefold() not in [str(item).casefold() for item in words]:
            words.append(word.strip()[:100])
        await database.set_setting(ctx.guild.id, "automod_words", words[:100])
        await ctx.send("✅ Automod word added.", ephemeral=True)

    @commands.hybrid_group(name="antiraid", description="Configure join-burst protection; disabled by default.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def antiraid(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            enabled = await database.get_setting(ctx.guild.id, "antiraid_enabled", False)
            await ctx.send(f"Anti-raid is currently **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)

    @antiraid.command(name="enable", description="Enable join-burst protection.")
    async def antiraid_enable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "antiraid_enabled", True)
        await ctx.send("✅ Anti-raid enabled. Five joins in 30 seconds will trigger protection.", ephemeral=True)

    @antiraid.command(name="disable", description="Disable join-burst protection.")
    async def antiraid_disable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "antiraid_enabled", False)
        await ctx.send("✅ Anti-raid disabled.", ephemeral=True)

    @commands.hybrid_group(name="schedule", description="Schedule messages for this server.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def schedule(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/schedule add`, `/schedule list`, or `/schedule remove`.", ephemeral=True)

    @schedule.command(name="add", description="Send a message after a delay in minutes.")
    async def schedule_add(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        delay_minutes: commands.Range[int, 1, 525600],
        repeat_minutes: commands.Range[int, 0, 525600] = 0,
        *,
        content: str,
    ) -> None:
        send_at = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat()
        schedule_id = await database.add_scheduled_message(
            ctx.guild.id,
            channel.id,
            content[:2000],
            send_at,
            repeat_minutes or None,
            ctx.author.id,
        )
        await ctx.send(f"✅ Scheduled message `#{schedule_id}` for {channel.mention}.", ephemeral=True)

    @schedule.command(name="list", description="List scheduled messages.")
    async def schedule_list(self, ctx: commands.Context) -> None:
        items = await database.get_scheduled_messages(ctx.guild.id)
        text = "\n".join(f"`#{item['schedule_id']}` <#{item['channel_id']}> · {item['send_at'][:16]} UTC" for item in items) or "No scheduled messages."
        await ctx.send(embed=discord.Embed(title="Scheduled messages", description=text), ephemeral=True)

    @schedule.command(name="remove", description="Remove a scheduled message.")
    async def schedule_remove(self, ctx: commands.Context, schedule_id: int) -> None:
        removed = await database.remove_scheduled_message(ctx.guild.id, schedule_id)
        await ctx.send("✅ Schedule removed." if removed else "❌ Schedule not found.", ephemeral=True)

    @commands.hybrid_group(name="starboard", description="Configure message highlights.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def starboard(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/starboard set #channel [threshold]` or `/starboard disable`.", ephemeral=True)

    @starboard.command(name="set", description="Set the starboard channel and reaction threshold.")
    async def starboard_set(self, ctx: commands.Context, channel: discord.TextChannel, threshold: commands.Range[int, 1, 100] = 3) -> None:
        await database.set_channel(ctx.guild.id, "starboard", channel.id)
        await database.set_setting(ctx.guild.id, "starboard_threshold", threshold)
        await ctx.send(f"✅ Starboard set to {channel.mention} at {threshold} ⭐ reactions.", ephemeral=True)

    @starboard.command(name="disable", description="Disable the starboard.")
    async def starboard_disable(self, ctx: commands.Context) -> None:
        await database.set_channel(ctx.guild.id, "starboard", None)
        await ctx.send("✅ Starboard disabled.", ephemeral=True)

    @commands.hybrid_group(name="levels", description="Configure XP levels and role rewards.")
    @commands.guild_only()
    async def levels(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            xp = await database.get_xp(ctx.guild.id, ctx.author.id)
            await ctx.send(f"You have **{xp['xp'] if xp else 0} XP** and are level **{xp['level'] if xp else 0}**.", ephemeral=True)

    @levels.command(name="enable", description="Enable XP levels for this server.")
    @commands.has_guild_permissions(manage_guild=True)
    async def levels_enable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "levels_enabled", True)
        await ctx.send("✅ XP levels enabled.", ephemeral=True)

    @levels.command(name="disable", description="Disable XP levels for this server.")
    @commands.has_guild_permissions(manage_guild=True)
    async def levels_disable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "levels_enabled", False)
        await ctx.send("✅ XP levels disabled.", ephemeral=True)

    @levels.command(name="reward", description="Give a role when members reach a level.")
    @commands.has_guild_permissions(manage_roles=True)
    async def levels_reward(self, ctx: commands.Context, level: commands.Range[int, 1, 1000], role: discord.Role) -> None:
        if ctx.guild.me is None or role >= ctx.guild.me.top_role or role.managed:
            await ctx.send("❌ I cannot assign that role.", ephemeral=True)
            return
        if ctx.author != ctx.guild.owner and role >= ctx.author.top_role:
            await ctx.send("❌ You cannot configure a role at or above your highest role.", ephemeral=True)
            return
        await database.set_level_reward(ctx.guild.id, level, role.id)
        await ctx.send(f"✅ Level {level} now awards {role.mention}.", ephemeral=True)

    @levels.command(name="leaderboard", description="Show the server XP leaderboard.")
    async def levels_leaderboard(self, ctx: commands.Context) -> None:
        rows = await database.get_xp_leaderboard(ctx.guild.id)
        lines = [f"**{index}.** <@{row['user_id']}> — level {row['level']} ({row['xp']} XP)" for index, row in enumerate(rows, 1)]
        await ctx.send(embed=discord.Embed(title="XP leaderboard", description="\n".join(lines) or "No XP recorded yet."))

    @commands.hybrid_group(name="infractions", description="View member warning and moderation history.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    async def infractions(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/infractions view @member`.", ephemeral=True)

    @infractions.command(name="view", description="View a member's infraction summary.")
    async def infractions_view(self, ctx: commands.Context, member: discord.Member) -> None:
        summary = await database.get_member_moderation(ctx.guild.id, member.id)
        actions = await database.get_member_actions(ctx.guild.id, member.id)
        lines = [f"{row['action']} — {row['reason'] or 'No reason'}" for row in actions[-10:]]
        embed = discord.Embed(title=f"Infractions · {member}", description=("\n".join(lines) or "No recorded actions.")[:4096], colour=discord.Colour.orange())
        if summary:
            embed.add_field(name="Warnings/actions", value=f"Warnings: {summary['warn_count']}\nKicks: {summary['kick_count']}\nMutes: {summary['mute_count']}\nBans: {summary['ban_count']}\nSoftbans: {summary['softban_count']}")
        if ctx.interaction is not None:
            await ctx.send(embed=embed, ephemeral=True)
        else:
            try:
                await ctx.author.send(embed=embed)
                await ctx.send("✅ I sent your infraction summary by DM.", delete_after=8)
            except discord.Forbidden:
                await ctx.send("❌ I could not DM you your private infraction summary.", delete_after=8)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await database.add_daily_stat(member.guild.id, datetime.now(timezone.utc).date().isoformat(), "joins")
        autorole_id = await database.get_setting(member.guild.id, "autorole_id")
        role = member.guild.get_role(int(autorole_id)) if autorole_id else None
        if role and member.guild.me and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="Configured autorole")
            except discord.HTTPException:
                logger.exception("Could not assign autorole in guild %s", member.guild.id)
        now = time.monotonic()
        joins = self.raid_joins[member.guild.id]
        joins.append(now)
        while joins and now - joins[0] > 30:
            joins.popleft()
        if await database.get_setting(member.guild.id, "antiraid_enabled", False) and len(joins) >= 5:
            try:
                await member.timeout(timedelta(minutes=10), reason="Anti-raid join burst")
            except discord.HTTPException:
                pass
        template = await database.get_setting(member.guild.id, "welcome_message")
        channel = await self._channel(member.guild, "welcome")
        if template and channel:
            await channel.send(_format_message(str(template), member))
        await self._log(member.guild, "Member joined", f"{member.mention} (`{member.id}`) joined the server.")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await database.add_daily_stat(member.guild.id, datetime.now(timezone.utc).date().isoformat(), "leaves")
        template = await database.get_setting(member.guild.id, "farewell_message")
        channel = await self._channel(member.guild, "farewell")
        if template and channel:
            await channel.send(_format_message(str(template), member))
        await self._log(member.guild, "Member left", f"{member} (`{member.id}`) left the server.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        await database.upsert_guild(message.guild)
        await database.save_message_snapshot(message)
        await database.add_daily_stat(message.guild.id, datetime.now(timezone.utc).date().isoformat(), "messages")
        if message.author.bot:
            return
        if await self._automod_blocked(message):
            try:
                await message.delete(reason="Automod filter")
            except discord.HTTPException:
                pass
            await self._log(message.guild, "Automod action", f"Deleted a message from {message.author.mention} in {message.channel.mention}.")
            return
        if await database.get_setting(message.guild.id, "levels_enabled", False):
            key = (message.guild.id, message.author.id)
            now = time.monotonic()
            if now - self.xp_cooldowns.get(key, 0) >= 60:
                self.xp_cooldowns[key] = now
                previous = await database.get_xp(message.guild.id, message.author.id)
                current = await database.award_xp(message.guild.id, message.author.id, 5, datetime.now(timezone.utc).isoformat())
                if previous and current["level"] > previous["level"]:
                    role_id = await database.get_level_reward(message.guild.id, current["level"])
                    role = message.guild.get_role(role_id) if role_id else None
                    if role and message.guild.me and role < message.guild.me.top_role:
                        await message.author.add_roles(role, reason="Level reward")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        await self._audit_deleted_message(message.guild, message.id, message)

    async def _audit_deleted_message(
        self,
        guild: discord.Guild,
        message_id: int,
        message: Optional[discord.Message] = None,
    ) -> None:
        if not await database.claim_message_deletion(message_id):
            return
        snapshot = await database.get_message_snapshot(message_id)
        if message is not None:
            if message.author.bot:
                await database.release_message_deletion_claim(message_id)
                return
            snapshot = {
                "content": message.content,
                "author_id": message.author.id,
                "attachments": json.dumps([{"url": a.url, "filename": a.filename} for a in message.attachments]),
            }
        elif snapshot is None:
            snapshot = {"content": "", "author_id": 0, "attachments": "[]"}
        attachments = json.loads(snapshot["attachments"] or "[]")
        image_url = next(
            (
                item["url"]
                for item in attachments
                if str(item.get("filename") or item.get("url", "")).lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
            ),
            None,
        )
        attachment_text = "\n".join(
            f"📎 {item.get('filename') or 'attachment'}: {item.get('url')}"
            for item in attachments
            if item.get("url")
        )
        description = f"Author: <@{snapshot['author_id']}>\n{snapshot['content'] or '[no text content]'}"
        if attachment_text:
            description += f"\n{attachment_text}"
        try:
            delivered = await self._log(guild, "Message deleted", description, image_url=image_url)
        except Exception:
            logger.exception("Could not audit deleted message %s", message_id)
            delivered = False
        if delivered:
            await database.mark_message_deleted(message_id)
        else:
            await database.release_message_deletion_claim(message_id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild:
            if payload.cached_message is None or not payload.cached_message.author.bot:
                await self._audit_deleted_message(guild, payload.message_id, payload.cached_message)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return
        cached_messages = {message.id: message for message in payload.cached_messages}
        entries: list[tuple[int, str, Optional[str]]] = []
        for message_id in payload.message_ids:
            message = cached_messages.get(message_id)
            if message is not None and message.author.bot:
                continue
            if not await database.claim_message_deletion(message_id):
                continue
            snapshot = await database.get_message_snapshot(message_id)
            if message is not None:
                author_id = message.author.id
                content = message.content
                attachments = [{"url": item.url, "filename": item.filename} for item in message.attachments]
            elif snapshot:
                author_id = snapshot["author_id"]
                content = snapshot["content"]
                attachments = json.loads(snapshot["attachments"] or "[]")
            else:
                author_id, content, attachments = 0, "", []
            image_url = next((item["url"] for item in attachments if str(item.get("filename") or item.get("url", "")).lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))), None)
            entries.append((message_id, f"<@{author_id}>: {content or '[no text content]'}", image_url))
        if not entries:
            return
        description = "\n".join(text for _, text, _ in entries)
        image_url = next((url for _, _, url in entries if url), None)
        try:
            delivered = await self._log(guild, "Messages deleted", description, image_url=image_url)
        except Exception:
            logger.exception("Could not audit bulk message deletion in guild %s", guild.id)
            delivered = False
        for message_id, _, _ in entries:
            if delivered:
                await database.mark_message_deleted(message_id)
            else:
                await database.release_message_deletion_claim(message_id)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.bot or (
            before.content == after.content
            and [(item.url, item.filename) for item in before.attachments]
            == [(item.url, item.filename) for item in after.attachments]
        ):
            return
        await database.save_message_snapshot(after)
        if await self._automod_blocked(after):
            try:
                await after.delete(reason="Automod filter on edited message")
            except discord.HTTPException:
                pass
            await self._log(after.guild, "Automod action", f"Deleted an edited message from {after.author.mention} in {after.channel.mention}.")
            return
        await self._log(
            after.guild,
            "Message edited",
            f"Author: {after.author.mention}\nBefore: {before.content or '[empty]'}\nAfter: {after.content or '[empty]'}",
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Handle edits for messages that were not present in the local cache."""
        if payload.guild_id is None or payload.cached_message is not None:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        snapshot = await database.get_message_snapshot(message.id)
        if message.author.bot:
            return
        current_attachments = json.dumps([{"url": item.url, "filename": item.filename} for item in message.attachments])
        if snapshot and snapshot.get("content", "") == message.content and snapshot.get("attachments", "[]") == current_attachments:
            return
        before_content = snapshot.get("content", "") if snapshot else "[unknown — message was uncached]"
        await database.save_message_snapshot(message)
        if await self._automod_blocked(message):
            try:
                await message.delete(reason="Automod filter on edited message")
            except discord.HTTPException:
                pass
            await self._log(message.guild, "Automod action", f"Deleted an edited message from {message.author.mention} in {message.channel.mention}.")
            return
        await self._log(
            message.guild,
            "Message edited",
            f"Author: {message.author.mention}\nBefore: {before_content or '[empty]'}\nAfter: {message.content or '[empty]'}",
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or str(payload.emoji) != "⭐":
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        starboard = await self._channel(channel.guild, "starboard")
        threshold = int(await database.get_setting(channel.guild.id, "starboard_threshold", 3))
        if starboard is None or channel.id == starboard.id:
            return
        try:
            source = await channel.fetch_message(payload.message_id)
            if source.author.bot:
                return
            reaction = discord.utils.get(source.reactions, emoji="⭐")
            if reaction is None or reaction.count < threshold or await database.get_starboard_message(channel.guild.id, source.id):
                return
            if not await database.claim_starboard_message(channel.guild.id, source.id):
                return
            embed = discord.Embed(description=source.content or "[no text content]", colour=discord.Colour.gold(), timestamp=source.created_at)
            embed.set_author(name=str(source.author), icon_url=source.author.display_avatar.url)
            if source.attachments:
                embed.set_image(url=source.attachments[0].url)
            try:
                posted = await starboard.send(content=f"⭐ **{reaction.count}** · {source.channel.mention}", embed=embed)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await database.release_starboard_claim(channel.guild.id, source.id)
                return
            await database.set_starboard_message(channel.guild.id, source.id, posted.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        if ctx.guild:
            await database.add_daily_stat(ctx.guild.id, datetime.now(timezone.utc).date().isoformat(), "commands")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Community(bot))
