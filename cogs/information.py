"""User and server information commands."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands


def _display_list(values: list[str], *, empty: str = "None", limit: int = 900) -> str:
    """Format a Discord field without exceeding embed field limits."""
    if not values:
        return empty
    result = ", ".join(values)
    if len(result) <= limit:
        return result
    return f"{result[:limit - 3]}..."


class Information(commands.Cog):
    """Commands for inspecting users and the current Discord server."""

    @commands.hybrid_command(name="userinfo", description="Show information about a server member.")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        member = member or ctx.author
        roles = [role.mention for role in reversed(member.roles) if not role.is_default()]
        embed = discord.Embed(
            title=f"User information · {member}",
            colour=member.colour if member.colour != discord.Colour.default() else discord.Colour.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="F"), inline=True)
        embed.add_field(
            name="Joined server",
            value=discord.utils.format_dt(member.joined_at, style="F") if member.joined_at else "Unknown",
            inline=True,
        )
        embed.add_field(name="Bot account", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Highest role", value=member.top_role.mention, inline=True)
        embed.add_field(name=f"Roles ({len(roles)})", value=_display_list(roles), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Show information about this Discord server.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        member_count = guild.member_count or len(guild.members)
        owner = guild.owner.mention if guild.owner else f"`{guild.owner_id}`"

        embed = discord.Embed(
            title=f"Server information · {guild.name}",
            colour=discord.Colour.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Owner", value=owner, inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="F"), inline=True)
        embed.add_field(name="Members", value=f"{member_count:,}", inline=True)
        embed.add_field(
            name="Channels",
            value=f"{text_channels} text · {voice_channels} voice · {categories} categories",
            inline=True,
        )
        embed.add_field(name="Roles", value=f"{len(guild.roles):,}", inline=True)
        embed.add_field(name="Verification", value=guild.verification_level.name.replace("_", " ").title(), inline=True)
        embed.add_field(name="Boosts", value=f"Level {guild.premium_tier} · {guild.premium_subscription_count or 0} boosts", inline=True)
        embed.add_field(name="Features", value=_display_list([feature.replace("_", " ").title() for feature in guild.features]), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Information())
