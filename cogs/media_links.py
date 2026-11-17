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


import discord
from discord.ext import commands
import re


class MediaLinks(commands.Cog):
    """Detects Instagram and X (Twitter) links and reposts them properly."""

    INSTA_PATTERN = re.compile(
        r"(https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+)"
    )

    X_PATTERN = re.compile(
        r"(https?://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+/status/[0-9]+)"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Detects social media links and routes them to the correct handler."""
        if message.author.bot or not message.content:
            return

        # Handle X / Twitter Links
        x_match = self.X_PATTERN.search(message.content)
        if x_match:
            original_url = x_match.group(1)
            print(f"X/Twitter link found: {original_url}")
            await self._handle_x_link(message, original_url)
            return

        # Handle Instagram Links
        insta_match = self.INSTA_PATTERN.search(message.content)
        if insta_match:
            url = insta_match.group(1)
            print(f"Instagram link found: {url}")
            await self._handle_instagram_link(message, url)
            return

    async def _handle_x_link(self, message: discord.Message, url: str):
        """Replaces standard X/Twitter domain with FxEmbed domain."""
        try:
            fixed_url = re.sub(r"https?://(?:www\.)?(twitter\.com|x\.com)", "https://fixupx.com", url)
            formatted_message = f"[X / Twitter Post]({fixed_url})\n-# Sent by {message.author.name}"

            await self._repost_and_delete(message, formatted_message)
            print("X message sent to channel")
        except Exception as e:
            print(f"Error processing X link: {e}")

    async def _handle_instagram_link(self, message: discord.Message, url: str):
        """Replaces standard Instagram domain with kkinstagram."""
        try:
            # Swap domain to the working kkinstagram proxy
            fixed_url = re.sub(r"https?://(?:www\.)?instagram\.com", "https://kkinstagram.com", url)
            formatted_message = f"[Instagram Post]({fixed_url})\n-# Sent by {message.author.name}"

            await self._repost_and_delete(message, formatted_message)
            print("Instagram message sent to channel")
        except Exception as e:
            print(f"Error processing Instagram link: {e}")

    async def _repost_and_delete(self, message: discord.Message, new_content: str):
        """Helper method to send the new formatted message and delete the original."""
        await message.channel.send(new_content)

        # Delete original message if the bot has the correct permissions
        if message.guild and message.guild.me.guild_permissions.manage_messages:
            await message.delete()

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MediaLinks())
