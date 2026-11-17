"""YouTube upload and live-stream notifications for multiple platforms."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import xml.etree.ElementTree as ET
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands, tasks

import database
import settings

logger = logging.getLogger(__name__)
SUPPORTED_PLATFORMS = {"youtube", "twitch", "kick"}
POLL_SECONDS = 60


@dataclass(frozen=True)
class Notification:
    item_id: str
    title: str
    url: str
    creator: str
    kind: str


class Notifications(commands.Cog):
    """Persist and poll creator subscriptions for server notifications."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.poller.start()

    def cog_unload(self) -> None:
        self.poller.cancel()

    @tasks.loop(seconds=POLL_SECONDS)
    async def poller(self) -> None:
        subscriptions = await database.get_notification_subscriptions()
        for subscription in subscriptions:
            try:
                await self._poll_subscription(subscription)
            except Exception:
                logger.exception("Notification polling failed for subscription %s", subscription["subscription_id"])

    @poller.before_loop
    async def before_poller(self) -> None:
        await self.bot.wait_until_ready()

    async def _poll_subscription(self, subscription: dict[str, Any]) -> None:
        platform = subscription["platform"]
        identifier = subscription["identifier"]
        if platform == "youtube":
            notification = await self._youtube_latest(identifier)
            if notification is None:
                return
            if subscription["last_item_id"] is None:
                await database.update_notification_state(subscription["subscription_id"], last_item_id=notification.item_id)
                return
            if notification.item_id != subscription["last_item_id"]:
                if await self._send_notification(subscription, notification):
                    await database.update_notification_state(subscription["subscription_id"], last_item_id=notification.item_id)
            return

        if platform == "twitch":
            live, notification = await self._twitch_live(identifier)
        else:
            live, notification = await self._kick_live(identifier)
        if live is None:
            return
        previous_live = bool(subscription["last_live_state"])
        if live and not previous_live:
            if notification is not None and await self._send_notification(subscription, notification):
                await database.update_notification_state(subscription["subscription_id"], last_live_state=True)
        elif not live and previous_live:
            await database.update_notification_state(subscription["subscription_id"], last_live_state=False)

    async def _youtube_latest(self, channel_id: str) -> Optional[Notification]:
        if settings.YOUTUBE_API_KEY:
            return await self._youtube_api_latest(channel_id)
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(feed_url) as response:
                if response.status != 200:
                    return None
                root = ET.fromstring(await response.text())
        entry = next(iter(root.findall("{http://www.w3.org/2005/Atom}entry")), None)
        if entry is None:
            return None
        ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        video_id = entry.findtext("yt:videoId", namespaces=ns)
        title = entry.findtext("atom:title", namespaces=ns) or "New YouTube upload"
        creator = entry.findtext("atom:author/atom:name", namespaces=ns) or channel_id
        if not video_id:
            return None
        return Notification(video_id, title, f"https://www.youtube.com/watch?v={video_id}", creator, "YouTube upload")

    async def _youtube_api_latest(self, channel_id: str) -> Optional[Notification]:
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "type": "video",
            "maxResults": "1",
            "key": settings.YOUTUBE_API_KEY,
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get("https://www.googleapis.com/youtube/v3/search", params=params) as response:
                if response.status != 200:
                    return None
                body = await response.json()
        item = (body.get("items") or [None])[0]
        if not item:
            return None
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            return None
        live_state = snippet.get("liveBroadcastContent")
        kind = "YouTube live stream" if live_state == "live" else "YouTube upload"
        return Notification(
            str(video_id),
            str(snippet.get("title") or kind),
            f"https://www.youtube.com/watch?v={video_id}",
            str(snippet.get("channelTitle") or channel_id),
            kind,
        )

    async def _twitch_live(self, username: str) -> tuple[Optional[bool], Optional[Notification]]:
        if not settings.TWITCH_CLIENT_ID or not settings.TWITCH_APP_TOKEN:
            return None, None
        headers = {
            "Client-Id": settings.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {settings.TWITCH_APP_TOKEN}",
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers=headers) as session:
            async with session.get("https://api.twitch.tv/helix/streams", params={"user_login": username}) as response:
                if response.status != 200:
                    return None, None
                body = await response.json()
        streams = body.get("data") or []
        if not streams:
            return False, None
        stream = streams[0]
        return True, Notification(
            str(stream.get("id", username)),
            str(stream.get("title") or f"{username} is live"),
            f"https://twitch.tv/{username}",
            str(stream.get("user_name") or username),
            "Twitch stream",
        )

    async def _kick_live(self, slug: str) -> tuple[Optional[bool], Optional[Notification]]:
        if not settings.KICK_API_TOKEN:
            return None, None
        headers = {"Authorization": f"Bearer {settings.KICK_API_TOKEN}"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers=headers) as session:
            async with session.get("https://api.kick.com/public/v1/channels", params={"slug": slug}) as response:
                if response.status != 200:
                    return None, None
                body = await response.json()
        channel = (body.get("data") or body.get("channels") or [None])[0]
        stream = (channel or {}).get("livestream") or (channel or {}).get("stream")
        if not stream or stream.get("is_live") is not True:
            return False, None
        creator = (
            (channel or {}).get("slug")
            or (channel or {}).get("username")
            or ((channel or {}).get("user") or {}).get("username")
            or slug
        )
        return True, Notification(
            str(stream.get("id") or slug),
            str(stream.get("session_title") or stream.get("stream_title") or (channel or {}).get("stream_title") or f"{slug} is live"),
            f"https://kick.com/{slug}",
            str(creator),
            "Kick stream",
        )

    async def _send_notification(self, subscription: dict[str, Any], notification: Notification) -> bool:
        channel = self.bot.get_channel(subscription["channel_id"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(subscription["channel_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False
        if not isinstance(channel, discord.TextChannel):
            return False
        embed = discord.Embed(
            title=f"{notification.creator} {notification.kind.lower()}",
            description=notification.title,
            url=notification.url,
            colour=discord.Colour.red() if "stream" in notification.kind.lower() else discord.Colour.blurple(),
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            return False
        return True

    @commands.hybrid_group(name="notify", description="Manage stream and upload notifications.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def notify(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `notify add`, `notify remove`, or `notify list`.", ephemeral=True)

    @notify.command(name="add", description="Subscribe this server to a creator.")
    async def notify_add(self, ctx: commands.Context, platform: str, identifier: str, channel: discord.TextChannel) -> None:
        platform = platform.casefold()
        if platform not in SUPPORTED_PLATFORMS:
            await ctx.send("Platform must be `youtube`, `twitch`, or `kick`.", ephemeral=True)
            return
        identifier = identifier.strip().removeprefix("@").rstrip("/")
        if platform == "youtube" and not identifier.startswith("UC"):
            await ctx.send("YouTube currently requires the channel ID, such as `UC...`.", ephemeral=True)
            return
        await database.add_notification_subscription(
            ctx.guild.id, platform, identifier, identifier, channel.id, ctx.author.id
        )
        await ctx.send(f"Added {platform} notifications for `{identifier}` in {channel.mention}.", ephemeral=True)

    @notify.command(name="remove", description="Remove a creator notification subscription.")
    async def notify_remove(self, ctx: commands.Context, platform: str, identifier: str) -> None:
        removed = await database.remove_notification_subscription(
            ctx.guild.id, platform.casefold(), identifier.strip().removeprefix("@").rstrip("/")
        )
        await ctx.send("Subscription removed." if removed else "Subscription not found.", ephemeral=True)

    @notify.command(name="list", description="List this server's notification subscriptions.")
    async def notify_list(self, ctx: commands.Context) -> None:
        subscriptions = await database.get_notification_subscriptions(ctx.guild.id)
        if not subscriptions:
            await ctx.send("No notification subscriptions are configured.", ephemeral=True)
            return
        lines = [f"• `{item['platform']}` `{item['identifier']}` → <#{item['channel_id']}>" for item in subscriptions]
        await ctx.send(embed=discord.Embed(title="Notification subscriptions", description="\n".join(lines)), ephemeral=True)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if ctx.cog is self and isinstance(error, commands.MissingPermissions):
            await ctx.send("You need Manage Server to change notification subscriptions.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Notifications(bot))