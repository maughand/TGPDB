"""Detects Instagram and X (Twitter) links and reposts them with sideways carousel buttons."""
from __future__ import annotations

import html
import logging
import re
from typing import Any

import aiohttp
import discord
from discord.ext import commands

import database

logger = logging.getLogger(__name__)


class InstagramEmbedCarouselView(discord.ui.View):
    """Sideways-scrolling Discord Embed carousel when direct slide image URLs are extracted."""

    def __init__(
        self,
        slides: list[str],
        source_url: str,
        author_name: str,
        insta_author: str = "",
    ) -> None:
        super().__init__(timeout=1800)  # Active for 30 minutes
        self.slides = slides[:10]
        self.source_url = source_url
        self.author_name = author_name
        self.insta_author = insta_author
        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= len(self.slides) - 1
        self.counter_button.label = f"Slide {self.index + 1} / {len(self.slides)}"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"Instagram Post (@{self.insta_author})" if self.insta_author else "Instagram Post",
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


class KKInstagramSlideView(discord.ui.View):
    """Sideways-scrolling view that cycles through kkinstagram.com/p/CODE/1..10 slides."""

    def __init__(
        self,
        shortcode: str,
        author_name: str,
        initial_slide: int = 1,
        max_slides: int = 10,
    ) -> None:
        super().__init__(timeout=1800)  # Active for 30 minutes
        self.shortcode = shortcode
        self.author_name = author_name
        self.max_slides = max(1, min(max_slides, 10))
        self.slide = max(1, min(initial_slide, self.max_slides))
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.slide <= 1
        self.next_button.disabled = self.slide >= self.max_slides
        if self.max_slides < 10:
            self.counter_button.label = f"Slide {self.slide} / {self.max_slides}"
        else:
            self.counter_button.label = f"Slide {self.slide}"

    def build_content(self) -> str:
        fixed_url = f"https://kkinstagram.com/p/{self.shortcode}/{self.slide}"
        return f"[Instagram Post (Slide {self.slide})]({fixed_url})\n-# Sent by {self.author_name}"

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.slide = max(1, self.slide - 1)
        self._update_buttons()
        await interaction.response.edit_message(content=self.build_content(), view=self)

    @discord.ui.button(label="Slide 1", style=discord.ButtonStyle.gray, disabled=True)
    async def counter_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.slide = min(self.max_slides, self.slide + 1)
        self._update_buttons()
        await interaction.response.edit_message(content=self.build_content(), view=self)


class MediaLinks(commands.Cog):
    """Detects Instagram and X (Twitter) links and reposts them properly."""

    # Captures full URL, post type (p/reel/reels/tv), shortcode, and optional ?img_index=N
    INSTA_PATTERN = re.compile(
        r"(https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.-]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)[^\s<>]*)",
        re.IGNORECASE,
    )

    IMG_INDEX_PATTERN = re.compile(r"[?&]img_index=(\d+)", re.IGNORECASE)

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
            index_match = self.IMG_INDEX_PATTERN.search(full_url)
            initial_slide = int(index_match.group(1)) if index_match else 1
            logger.info("Instagram link found: %s (kind=%s, shortcode=%s)", full_url, kind, shortcode)
            await self._handle_instagram_link(message, full_url, kind, shortcode, initial_slide)
            return

    async def _scrape_instagram_embed_slides(self, shortcode: str) -> list[str]:
        """Extract all carousel image URLs from Instagram's public embed page."""
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(embed_url) as resp:
                    if resp.status != 200:
                        return []
                    raw_html = await resp.text()

            # 1. Check for escaped JSON display_url entries inside the embed script payload
            urls: list[str] = []
            for match in re.finditer(r'"display_url"\s*:\s*"([^"]+)"', raw_html):
                cleaned = match.group(1).encode("utf-8").decode("unicode_escape").replace(r"\/", "/")
                cleaned = html.unescape(cleaned)
                if cleaned not in urls:
                    urls.append(cleaned)

            # 2. Check for EmbeddedMediaImage src attributes if JSON payload isn't present
            if len(urls) <= 1:
                for match in re.finditer(
                    r'class="[^"]*EmbeddedMediaImage[^"]*"[^>]+src="([^"]+)"',
                    raw_html,
                ):
                    cleaned = html.unescape(match.group(1))
                    if cleaned not in urls:
                        urls.append(cleaned)

            return urls[:10]
        except Exception as exc:
            logger.debug("Embed slide scrape failed for %s: %s", shortcode, exc)
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
        initial_slide: int = 1,
    ) -> None:
        """Handles Reels directly and provides sideways slide navigation for /p/ posts."""
        try:
            # Reels and TV links are single videos -> send directly via kkinstagram
            if kind in {"reel", "reels", "tv"}:
                fixed_url = f"https://kkinstagram.com/{kind}/{shortcode}"
                formatted_message = f"[Instagram Reel]({fixed_url})\n-# Sent by {message.author.display_name}"
                await self._repost_and_delete(message, content=formatted_message)
                return

            # For /p/ posts, first try extracting all carousel images from the embed page
            slides = await self._scrape_instagram_embed_slides(shortcode)
            if len(slides) > 1:
                clean_source = f"https://www.instagram.com/p/{shortcode}/"
                view = InstagramEmbedCarouselView(
                    slides=slides,
                    source_url=clean_source,
                    author_name=message.author.display_name,
                )
                view.index = max(0, min(initial_slide - 1, len(slides) - 1))
                view._update_buttons()
                await self._repost_and_delete(message, embed=view.build_embed(), view=view)
                return

            # Fallback: Use kkinstagram's /p/{shortcode}/{slide_num} URL indexer with ◀️ / ▶️ buttons
            # This works for both multi-image AND multi-video carousels even if Instagram blocks scraping!
            slide_view = KKInstagramSlideView(
                shortcode=shortcode,
                author_name=message.author.display_name,
                initial_slide=initial_slide,
                max_slides=10,
            )
            await self._repost_and_delete(
                message,
                content=slide_view.build_content(),
                view=slide_view,
            )
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