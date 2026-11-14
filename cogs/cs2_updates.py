"""Counter-Strike 2 update notifications with per-guild channels."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

import aiohttp
import discord
from discord.ext import commands, tasks

import database


CS2_CHANNEL_FEATURE = "cs2_updates"
CS2_UPDATE_URL = (
    "https://store.steampowered.com/events/ajaxgetpartnereventspageable/"
    "?clan_accountid=0&appid=730&offset=0&count=10&l=english"
    "&origin=https://www.counter-strike.net"
)


class CounterStrikeUpdateChecker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.last_release: int | None = None
        self.check_loop.start()

    def cog_unload(self) -> None:
        self.check_loop.cancel()

    @tasks.loop(minutes=30)
    async def check_loop(self) -> None:
        await self.check_for_update_once()

    @check_loop.before_loop
    async def before_check_loop(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def parse_patch_note_body(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Convert BBCode links: [url=https://...]Label[/url] -> [Label](https://...)
        text = re.sub(r'\[url=(["\']?)(.*?)\1\](.*?)\[/url\]', r"[\3](\2)", text, flags=re.IGNORECASE)
        # Convert inline formatting tags
        text = re.sub(r"\[h[1-3]\](.*?)\[/h[1-3]\]", r"\n**\1**\n", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\[b\](.*?)\[/b\]", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\[i\](.*?)\[/i\]", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\[u\](.*?)\[/u\]", r"__\1__", text, flags=re.IGNORECASE | re.DOTALL)
        # Replace paragraph and list containers with newlines so blocks don't collapse
        text = re.sub(r"\[/?(?:p|list|olist)\]", "\n", text, flags=re.IGNORECASE)
        # Put each list item on its own Markdown bullet line
        text = re.sub(r"\s*\[\*\]\s*", "\n- ", text)
        text = re.sub(r"\[/\*\]|\[/\]", "", text)
        # Unescape escaped brackets (e.g., \[ ANIMATION \])
        text = re.sub(r"\\([\[\]])", r"\1", text)
        # Normalize section headers like [ ANIMATION ] into **[ANIMATION]** on their own line
        text = re.sub(
            r"(?:\*\*)?\[\s*([A-Z0-9][A-Z0-9 _/&+-]*?)\s*\](?:\*\*)?(?!\()",
            lambda m: f"\n\n**[{m.group(1).strip()}]**\n",
            text,
        )
        # Clean up whitespace and keep bullets directly underneath their section header
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"(\*\*\[[^\n]+\]\*\*)\n\n(-\s)", r"\1\n\2", text)
        return text.strip()

    @staticmethod
    def _chunk_text(text: str, limit: int = 3900) -> list[str]:
        if not text:
            return ["No patch notes were provided."]
        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > limit:
                if current:
                    chunks.append(current.strip())
                    current = ""
                for i in range(0, len(line), limit):
                    chunks.append(line[i : i + limit].strip())
            elif len(current) + len(line) > limit:
                chunks.append(current.strip())
                current = line
            else:
                current += line
        if current.strip():
            chunks.append(current.strip())
        return chunks or ["No patch notes were provided."]

    async def fetch_latest_update(self) -> dict[str, Any] | None:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(CS2_UPDATE_URL) as response:
                if response.status != 200:
                    return None
                body = await response.json()
        for event in body.get("events", []):
            if str(event.get("event_type")) != "12":
                continue
            announcement = event.get("announcement_body") or {}
            raw_release = (
                event.get("event_date")
                or event.get("posttime")
                or announcement.get("posttime")
                or announcement.get("release")
                or 0
            )
            try:
                release = int(raw_release)
            except (TypeError, ValueError):
                release = 0
            update_id = str(event.get("gid") or event.get("event_gid") or release)
            return {
                "id": update_id,
                "title": announcement.get("headline") or "Counter-Strike 2 Update",
                "description": self.parse_patch_note_body(announcement.get("body") or "No patch notes were provided."),
                "release": release,
                "url": "https://www.counter-strike.net/news/updates",
            }
        return None

    async def check_for_update_once(self) -> bool:
        latest = await self.fetch_latest_update()
        if not latest or not latest["release"]:
            return False
        previous = await database.get_latest_cs2_update()
        await database.save_cs2_update(latest)
        self.last_release = latest["release"]
        if previous is None:
            # Baseline the update already present when the bot first starts.
            for guild in self.bot.guilds:
                if await database.get_channel(guild.id, CS2_CHANNEL_FEATURE):
                    await database.mark_cs2_update_delivered(guild.id, latest["id"])
            return False
        subscriptions = []
        for guild in self.bot.guilds:
            channel_id = await database.get_channel(guild.id, CS2_CHANNEL_FEATURE)
            if channel_id:
                subscriptions.append((guild, int(channel_id)))
        sent = False
        for guild, channel_id in subscriptions:
            if await database.is_cs2_update_delivered(guild.id, latest["id"]):
                continue
            if not await database.claim_cs2_update_delivery(guild.id, latest["id"]):
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                await database.release_cs2_update_claim(guild.id, latest["id"])
                continue
            delivered = await self._send_update(channel, latest)
            if delivered:
                await database.mark_cs2_update_delivered(guild.id, latest["id"])
                sent = True
            else:
                await database.release_cs2_update_claim(guild.id, latest["id"])
        return sent

    async def _send_update(self, channel: discord.TextChannel, update: dict[str, Any]) -> bool:
        chunks = self._chunk_text(update["description"])
        try:
            for index, chunk in enumerate(chunks):
                footer = (
                    "Counter-Strike 2 Patch Notes"
                    if len(chunks) == 1
                    else f"Counter-Strike 2 Patch Notes · Part {index + 1}/{len(chunks)}"
                )
                embed = discord.Embed(
                    title=update["title"] if index == 0 else f"{update['title']} (continued)",
                    description=chunk,
                    url=update["url"],
                    colour=discord.Colour.gold(),
                    timestamp=datetime.fromtimestamp(update["release"], tz=timezone.utc),
                )
                embed.set_footer(text=footer)
                await channel.send(embed=embed)
        except discord.HTTPException:
            return False
        return True

    @commands.hybrid_command(name="check_update", description="Check for a new Counter-Strike 2 update now.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def check_update(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)
        changed = await self.check_for_update_once()
        await ctx.send("Checked for updates." if changed else "No new update was found.", ephemeral=True)

    @commands.hybrid_command(name="last_update", description="Show the latest Counter-Strike 2 update known to the bot.")
    async def last_update(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)
        latest = await self.fetch_latest_update()
        if not latest:
            await ctx.send("I could not reach the Counter-Strike update feed.", ephemeral=True)
            return
        embed = discord.Embed(
            title=latest["title"],
            description=latest["description"][:4096],
            url=latest["url"],
            colour=discord.Colour.gold(),
            timestamp=datetime.fromtimestamp(latest["release"], tz=timezone.utc) if latest.get("release") else None,
        )
        embed.set_footer(text="Counter-Strike 2 Patch Notes")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CounterStrikeUpdateChecker(bot))
