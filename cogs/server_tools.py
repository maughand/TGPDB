"""Server health, incident management, evidence storage, and personal tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

import discord
from discord.ext import commands, tasks

import database


logger = logging.getLogger(__name__)


class ServerTools(commands.Cog):
    """Private operational tools for moderators and everyday members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.last_health_day: Optional[str] = None
        self.health_worker.start()

    def cog_unload(self) -> None:
        self.health_worker.cancel()

    async def _private_response(
        self,
        ctx: commands.Context,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        """Keep sensitive results private for both slash and prefix invocations."""
        if ctx.interaction is not None:
            await ctx.send(content, embed=embed, ephemeral=True)
            return
        try:
            await ctx.author.send(content, embed=embed)
            await ctx.send("✅ I sent the private result to your DMs.", delete_after=8)
        except discord.Forbidden:
            await ctx.send("❌ I could not DM you. Enable DMs from server members and try again.", delete_after=8)

    async def _analytics_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel_id = await database.get_channel(guild.id, "analytics")
        channel = guild.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _health_data(self, guild: discord.Guild) -> tuple[int, list[dict[str, object]]]:
        rows = await database.get_daily_stats(guild.id, 7)
        messages = sum(int(row["messages"]) for row in rows)
        joins = sum(int(row["joins"]) for row in rows)
        leaves = sum(int(row["leaves"]) for row in rows)
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        moderation = await database.get_moderation_count(guild.id, since)
        score = 50
        score += min(25, messages // 50)
        score += min(15, max(-15, (joins - leaves) * 2))
        score -= min(20, moderation)
        return max(0, min(100, score)), rows

    async def _health_embed(self, guild: discord.Guild) -> discord.Embed:
        score, rows = await self._health_data(guild)
        messages = sum(int(row["messages"]) for row in rows)
        joins = sum(int(row["joins"]) for row in rows)
        leaves = sum(int(row["leaves"]) for row in rows)
        colour = discord.Colour.green() if score >= 70 else discord.Colour.gold() if score >= 40 else discord.Colour.red()
        embed = discord.Embed(title=f"Server health · {guild.name}", colour=colour, timestamp=discord.utils.utcnow())
        embed.add_field(name="Health score", value=f"**{score}/100**", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count or len(guild.members):,}", inline=True)
        embed.add_field(name="7-day messages", value=f"{messages:,}", inline=True)
        embed.add_field(name="7-day joins", value=f"{joins:,}", inline=True)
        embed.add_field(name="7-day leaves", value=f"{leaves:,}", inline=True)
        recommendations = []
        if messages < 50:
            recommendations.append("Consider an event, poll, or scheduled announcement to increase activity.")
        if leaves > joins:
            recommendations.append("More members left than joined during the recorded period.")
        if not recommendations:
            recommendations.append("Activity and membership trends look stable.")
        embed.add_field(name="Recommendations", value="\n".join(recommendations), inline=False)
        embed.set_footer(text="Based on the last seven days of recorded bot activity")
        return embed

    @tasks.loop(hours=1)
    async def health_worker(self) -> None:
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        if now.hour != 0 or self.last_health_day == day:
            return
        self.last_health_day = day
        for guild in self.bot.guilds:
            if not await database.get_setting(guild.id, "health_dashboard_enabled", False):
                continue
            channel = await self._analytics_channel(guild)
            if channel:
                try:
                    await channel.send(embed=await self._health_embed(guild))
                except discord.HTTPException:
                    logger.exception("Could not post health dashboard for guild %s", guild.id)

    @health_worker.before_loop
    async def before_health_worker(self) -> None:
        await self.bot.wait_until_ready()

    @commands.hybrid_group(name="health", description="View and configure the server health dashboard.")
    @commands.guild_only()
    async def health(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send(embed=await self._health_embed(ctx.guild))

    @health.command(name="enable", description="Enable daily server health posts in the analytics channel.")
    @commands.has_guild_permissions(manage_guild=True)
    async def health_enable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "health_dashboard_enabled", True)
        await ctx.send("✅ Daily health dashboard posts enabled.", ephemeral=True)

    @health.command(name="disable", description="Disable daily server health posts.")
    @commands.has_guild_permissions(manage_guild=True)
    async def health_disable(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, "health_dashboard_enabled", False)
        await ctx.send("✅ Daily health dashboard posts disabled.", ephemeral=True)

    @commands.hybrid_group(name="incident", description="Manage private moderation incident timelines.")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    async def incident(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/incident open`, `/incident add`, `/incident view`, or `/incident close`.", ephemeral=True)

    @incident.command(name="open", description="Open a new incident case.")
    async def incident_open(self, ctx: commands.Context, *, title: str) -> None:
        case_id = await database.create_incident_case(ctx.guild.id, title, ctx.author.id)
        await self._private_response(ctx, f"✅ Incident `#{case_id}` opened: **{title[:200]}**")

    @incident.command(name="add", description="Add evidence text or an attachment to an incident.")
    async def incident_add(self, ctx: commands.Context, case_id: int, evidence: str, attachment: Optional[discord.Attachment] = None) -> None:
        item_id = await database.add_incident_evidence(
            ctx.guild.id,
            case_id,
            ctx.author.id,
            "attachment" if attachment else "note",
            evidence,
            attachment.url if attachment else None,
        )
        if item_id is None:
            await self._private_response(ctx, "❌ That incident does not exist in this server.")
            return
        await self._private_response(ctx, f"✅ Evidence `#{item_id}` added to incident `#{case_id}`.")

    @incident.command(name="view", description="View an incident timeline and evidence.")
    async def incident_view(self, ctx: commands.Context, case_id: int) -> None:
        case = await database.get_incident_case(ctx.guild.id, case_id)
        if case is None:
            await self._private_response(ctx, "❌ Incident not found.")
            return
        evidence = await database.get_incident_evidence(ctx.guild.id, case_id)
        lines = [f"`{item['created_at'][:19]}` {item['content']}" for item in evidence]
        for item in evidence:
            if item["attachment_url"]:
                lines.append(f"📎 {item['attachment_url']}")
        embed = discord.Embed(title=f"Incident #{case_id} · {case['title']}", description="\n".join(lines)[:4096] or "No evidence recorded.", colour=discord.Colour.orange())
        embed.add_field(name="Status", value=str(case["status"]).title())
        embed.add_field(name="Opened by", value=f"<@{case['opened_by']}>")
        await self._private_response(ctx, embed=embed)

    @incident.command(name="close", description="Close an incident case.")
    async def incident_close(self, ctx: commands.Context, case_id: int) -> None:
        closed = await database.close_incident_case(ctx.guild.id, case_id)
        await self._private_response(ctx, "✅ Incident closed." if closed else "❌ Open incident not found.")

    @commands.hybrid_group(name="assistant", description="Private personal server tools.")
    @commands.guild_only()
    async def assistant(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/assistant profile` or `/assistant infractions`.", ephemeral=True)

    @assistant.command(name="profile", description="Show your private server profile.")
    async def assistant_profile(self, ctx: commands.Context) -> None:
        xp = await database.get_xp(ctx.guild.id, ctx.author.id)
        summary = await database.get_member_moderation(ctx.guild.id, ctx.author.id)
        embed = discord.Embed(title=f"Your profile · {ctx.guild.name}", colour=discord.Colour.blurple())
        embed.add_field(name="XP", value=f"{xp['xp'] if xp else 0:,}", inline=True)
        embed.add_field(name="Level", value=str(xp["level"] if xp else 0), inline=True)
        embed.add_field(name="Roles", value=str(max(0, len(ctx.author.roles) - 1)), inline=True)
        embed.add_field(name="Recorded warnings/actions", value=str(sum(summary[key] for key in ("warn_count", "kick_count", "mute_count", "ban_count", "softban_count")) if summary else 0), inline=True)
        await self._private_response(ctx, embed=embed)

    @assistant.command(name="infractions", description="View your private infraction summary.")
    async def assistant_infractions(self, ctx: commands.Context) -> None:
        summary = await database.get_member_moderation(ctx.guild.id, ctx.author.id)
        actions = await database.get_member_actions(ctx.guild.id, ctx.author.id)
        text = "\n".join(f"{row['action']} — {row['reason'] or 'No reason'}" for row in actions[-10:]) or "No recorded actions."
        embed = discord.Embed(title="Your infractions", description=text[:4096], colour=discord.Colour.orange())
        if summary:
            embed.set_footer(text=f"Warnings: {summary['warn_count']} · Kicks: {summary['kick_count']} · Mutes: {summary['mute_count']} · Bans: {summary['ban_count']} · Softbans: {summary['softban_count']}")
        await self._private_response(ctx, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerTools(bot))
