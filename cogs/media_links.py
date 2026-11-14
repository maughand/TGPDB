"""Expand supported social-media links through a self-hosted Cobalt API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from urllib.parse import urlsplit

import aiohttp
import discord
from discord.ext import commands

import settings


logger = logging.getLogger(__name__)
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SUPPORTED_HOSTS = (
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "twitter.com",
    "x.com",
)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)


@dataclass(frozen=True)
class MediaResult:
    url: str
    source_url: str
    filename: str
    kind: str


def _is_supported_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in SUPPORTED_HOSTS)


class MediaLinks(commands.Cog):
    """Turn supported public social links into Discord auto-embeds."""

    @staticmethod
    def _find_url(content: str) -> str | None:
        for match in URL_PATTERN.finditer(content):
            url = match.group(0).rstrip(".,!?)")
            if _is_supported_url(url):
                return url
        return None

    @staticmethod
    async def _resolve(source_url: str) -> MediaResult:
        if not settings.COBALT_REQUEST_URL:
            raise RuntimeError("COBALT_API_URL is not configured")

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.COBALT_API_KEY:
            headers["Authorization"] = f"Api-Key {settings.COBALT_API_KEY}"
        payload = {
            "url": source_url,
            "downloadMode": "auto",
            "videoQuality": "1080",
            "filenameStyle": "basic",
            "alwaysProxy": True,
        }
        timeout = aiohttp.ClientTimeout(total=settings.COBALT_TIMEOUT_SECONDS)
        api_url = settings.COBALT_REQUEST_URL.rstrip("/")
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(api_url, json=payload) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"Cobalt returned HTTP {response.status}")

        status = body.get("status")
        if status in {"tunnel", "redirect"} and body.get("url"):
            return MediaResult(str(body["url"]), source_url, str(body.get("filename") or "media"), "video")
        if status == "picker":
            items = body.get("picker") or []
            item = next((entry for entry in items if entry.get("type") in {"video", "gif"}), None)
            item = item or next((entry for entry in items if entry.get("type") == "photo"), None)
            if item and item.get("url"):
                return MediaResult(str(item["url"]), source_url, "media", str(item.get("type") or "media"))
        if status == "error":
            error = body.get("error") or {}
            raise RuntimeError(f"Cobalt error: {error.get('code', 'unknown')}")
        raise RuntimeError(f"Unsupported Cobalt response: {status or 'unknown'}")

    async def _process_url(self, message: discord.Message, source_url: str) -> None:
        async with DOWNLOAD_SEMAPHORE:
            status_message = await message.channel.send("⏳ Resolving media link…")
            try:
                result = await self._resolve(source_url)
                # The media URL is sent to Discord rather than downloaded by the bot.
                content = f"{result.url}\nSource: <{result.source_url}>"
                await message.channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                await status_message.delete()
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as error:
                logger.warning("Media link resolution failed for %s: %s", source_url, error)
                await status_message.edit(content="❌ I could not resolve that media link. It may be private, unavailable, or unsupported.")
            except discord.HTTPException:
                logger.warning("Could not send the resolved media link for %s", source_url)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        source_url = self._find_url(message.content)
        if source_url:
            await self._process_url(message, source_url)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MediaLinks())
