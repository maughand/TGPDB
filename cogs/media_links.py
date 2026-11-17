"""Detects Instagram and X (Twitter) links and reposts them with an interactive multi-image carousel."""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import discord
from discord.ext import commands

import database

logger = logging.getLogger(__name__)


class KKInstagramCarouselView(discord.ui.View):
    """Interactive button carousel that flips through multi-image Instagram slides."""

    def __init__(self, slides: list[str], source_url: str, author_name: str) -> None:
        super().__init__(timeout=1800)  # Buttons active for 30 minutes
        self.slides = slides[:20]
        self.source_url = source_url
        self.author_name = author_name
        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= len(self.slides) - 1
        self.counter_button.label = f"Slide {self.index + 1} / {len(self.slides)}"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Instagram Post",
            url=self.source_url,
            colour=discord.Colour.from_rgb(225, 48, 108),
        )
        embed.set_image(url=self.slides[self.index])
        embed.set_footer(text=f"Slide {self.index + 1} of {len(self.slides)} • Sent by {self.author_name}")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Slide 1 / 1", style=discord.ButtonStyle.gray, disabled=True)
    async def counter_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.slides) - 1, self.index + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class MediaLinks(commands.Cog):
    """Detects Instagram and X (Twitter) links and reposts them properly."""

    INSTA_PATTERN = re.compile(
        r"(https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.-]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)[^\s<>]*)",
        re.IGNORECASE,
    )

    X_PATTERN = re.compile(
        r"(https?://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+/status/[0-9]+)",
        re.IGNORECASE,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Detects social media links and routes them to the correct handler."""
        if message.author.bot or not message.content:
            return

        # Handle X / Twitter Links
        x_match = self.X_PATTERN.search(message.content)
        if x_match:
            original_url = x_match.group(1)
            logger.info("X/Twitter link found: %s", original_url)
            await self._handle_x_link(message, original_url)
            return

        # Handle Instagram Links
        insta_match = self.INSTA_PATTERN.search(message.content)
        if insta_match:
            full_url = insta_match.group(1).rstrip(".,!?)")
            kind = insta_match.group(2).lower()
            shortcode = insta_match.group(3)
            logger.info("Instagram link found: %s (kind=%s, shortcode=%s)", full_url, kind, shortcode)
            await self._handle_instagram_link(message, full_url, kind, shortcode)
            return

    async def _fetch_kkinstagram_media(self, shortcode: str) -> list[str]:
        """Fetch all image slide URLs from kkinstagram / vxinstagram APIs."""
        headers = {"User-Agent": "Discordbot/2.0", "Accept": "application/json"}
        timeout = aiohttp.ClientTimeout(total=6)
        
        # Try primary and backup API endpoints used by embed fixers
        api_endpoints = [
            f"https://api.vxinstagram.com/p/{shortcode}",
            f"https://kkinstagram.com/api/post/{shortcode}",
        ]

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for endpoint in api_endpoints:
                try:
                    async with session.get(endpoint) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json(content_type=None)
                        if not isinstance(data, dict):
                            continue
                        
                        media_items = data.get("media_extended") or data.get("media") or []
                        slides = []
                        for item in media_items:
                            if isinstance(item, dict):
                                img_url = item.get("url") or item.get("image_url")
                                item_type = item.get("type", "image")
                                if img_url and item_type in {"image", "photo"}:
                                    slides.append(img_url)
                        
                        if slides:
                            return slides[:10]
                except Exception as exc:
                    logger.debug("API fetch failed for %s via %s: %s", shortcode, endpoint, exc)
                    
        return []

    async def _handle_x_link(self, message: discord.Message, url: str) -> None:
        """Replaces standard X/Twitter domain with FxEmbed domain."""
        try:
            fixed_url = re.sub(
                r"https?://(?:www\.)?(?:twitter\.com|x\.com)",
                "https://fixupx.com",
                url,
                flags=re.IGNORECASE,
            )
            formatted_message = f"[X / Twitter Post]({fixed_url})\n-# Sent by {message.author.display_name}"
            await self._repost_and_delete(message, content=formatted_message)
        except discord.HTTPException as exc:
            logger.warning("Error processing X link %s: %s", url, exc)

    async def _handle_instagram_link(
        self,
        message: discord.Message,
        url: str,
        kind: str,
        shortcode: str,
    ) -> None:
        """Handles Reels as direct links and multi-image posts as interactive carousels."""
        try:
            # Reels or single videos use kkinstagram link directly
            if kind in {"reel", "reels", "tv"}:
                fixed_url = f"https://kkinstagram.com/{kind}/{shortcode}"
                formatted_message = f"[Instagram Reel]({fixed_url})\n-# Sent by {message.author.display_name}"
                await self._repost_and_delete(message, content=formatted_message)
                return

            # Attempt to fetch multi-image carousel slides
            slides = await self._fetch_kkinstagram_media(shortcode)
            if len(slides) > 1:
                clean_source = f"https://www.instagram.com/p/{shortcode}/"
                view = KKInstagramCarouselView(
                    slides=slides,
                    source_url=clean_source,
                    author_name=message.author.display_name,
                )
                await self._repost_and_delete(message, embed=view.build_embed(), view=view)
                return

            # Fallback for single image posts
            fixed_url = re.sub(
                r"https?://(?:www\.)?instagram\.com",
                "https://kkinstagram.com",
                url,
                flags=re.IGNORECASE,
            )
            formatted_message = f"[Instagram Post]({fixed_url})\n-# Sent by {message.author.display_name}"
            await self._repost_and_delete(message, content=formatted_message)
        except discord.HTTPException as exc:
            logger.warning("Error processing Instagram link %s: %s", url, exc)

    async def _repost_and_delete(
        self,
        message: discord.Message,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
    ) -> None:
        """Send the new formatted message and delete the original without triggering audit logs."""
        await message.channel.send(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        # Delete original message if the bot has permissions in this channel
        if message.guild and message.guild.me:
            perms = message.channel.permissions_for(message.guild.me)
            if perms.manage_messages:
                if await database.claim_message_deletion(message.id):
                    await database.mark_message_deleted(message.id)
                try:
                    await message.delete()
                except discord.NotFound:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MediaLinks(bot))