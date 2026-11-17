"""Detects Instagram and X (Twitter) links and reposts them with multi-image carousel support."""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import discord
from discord.ext import commands

import database

logger = logging.getLogger(__name__)


class InstagramCarouselView(discord.ui.View):
    """Interactive Prev/Next button carousel for multi-image Instagram posts."""

    def __init__(
        self,
        slides: list[dict[str, Any]],
        source_url: str,
        author_name: str,
        caption: str,
        insta_author: str,
    ) -> None:
        super().__init__(timeout=900)  # Buttons stay active for 15 minutes
        self.slides = slides[:10]
        self.source_url = source_url
        self.author_name = author_name
        self.caption = caption
        self.insta_author = insta_author
        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= len(self.slides) - 1
        self.counter_button.label = f"{self.index + 1} / {len(self.slides)}"

    def build_embed(self) -> discord.Embed:
        slide = self.slides[self.index]
        embed = discord.Embed(
            title=f"Instagram Post (@{self.insta_author})" if self.insta_author else "Instagram Post",
            url=self.source_url,
            description=self.caption[:400] + ("..." if len(self.caption) > 400 else "") if self.caption else None,
            colour=discord.Colour.from_rgb(225, 48, 108),
        )
        embed.set_image(url=slide["url"])
        embed.set_footer(text=f"Slide {self.index + 1} of {len(self.slides)} • Sent by {self.author_name}")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.gray, disabled=True)
    async def counter_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.slides) - 1, self.index + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class MediaLinks(commands.Cog):
    """Detects Instagram and X (Twitter) links and reposts them properly."""

    # Matches /p/, /reel/, /reels/, /tv/, and username-prefixed URLs like instagram.com/username/p/CODE
    INSTA_PATTERN = re.compile(
        r"(https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.-]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+))",
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
            url = insta_match.group(1)
            shortcode = insta_match.group(2)
            logger.info("Instagram link found: %s (shortcode: %s)", url, shortcode)
            await self._handle_instagram_link(message, url, shortcode)
            return

    async def _fetch_instagram_slides(self, shortcode: str) -> dict[str, Any] | None:
        """Query vxinstagram's JSON API to see if the post is a multi-image carousel."""
        api_url = f"https://api.vxinstagram.com/p/{shortcode}"
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"User-Agent": "Discordbot/2.0", "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        return None
                    return data
        except Exception as exc:
            logger.debug("Carousel metadata fetch skipped for %s: %s", shortcode, exc)
            return None

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

    async def _handle_instagram_link(self, message: discord.Message, url: str, shortcode: str) -> None:
        """Renders a scrollable button carousel for multi-image posts, or kkinstagram for videos/single posts."""
        try:
            data = await self._fetch_instagram_slides(shortcode)
            media_items = (data or {}).get("media_extended") or (data or {}).get("media") or []

            # Collect image slides if this is an image carousel (>1 image and no video slides)
            image_slides = [
                {"url": item.get("url") or item.get("image_url")}
                for item in media_items
                if isinstance(item, dict)
                and item.get("type") in {"image", "photo", None}
                and (item.get("url") or item.get("image_url"))
            ]
            has_video = any(
                isinstance(item, dict) and item.get("type") in {"video", "gif"}
                for item in media_items
            )

            if len(image_slides) > 1 and not has_video:
                caption = str((data or {}).get("text") or (data or {}).get("caption") or "")
                insta_author = str((data or {}).get("user_name") or (data or {}).get("author_name") or "")
                view = InstagramCarouselView(
                    slides=image_slides,
                    source_url=url,
                    author_name=message.author.display_name,
                    caption=caption,
                    insta_author=insta_author,
                )
                await self._repost_and_delete(message, embed=view.build_embed(), view=view)
                return

            # Standard fallback for single posts, reels, or video carousels
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