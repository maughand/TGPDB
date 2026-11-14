"""Small image utilities that do not retain uploaded files on the bot host."""

from __future__ import annotations

from io import BytesIO

import discord
from discord.ext import commands
from PIL import Image, UnidentifiedImageError


class ImageTools(commands.Cog):
    """Convert one uploaded image into a valid GIF entirely in memory."""

    @commands.hybrid_command(name="to_gif", description="Convert an uploaded image to a GIF without saving it on the server.")
    async def to_gif(self, ctx: commands.Context, image: discord.Attachment) -> None:
        await ctx.defer()
        if image.content_type and not image.content_type.startswith("image/"):
            await ctx.send("❌ Please upload an image file.", ephemeral=True)
            return
        if image.size > 10 * 1024 * 1024:
            await ctx.send("❌ Please keep the image under 10 MB.", ephemeral=True)
            return
        try:
            source = Image.open(BytesIO(await image.read()))
            frame = source.convert("RGBA")
            output = BytesIO()
            frame.save(output, format="GIF", save_all=True, loop=0, disposal=2)
            output.seek(0)
        except (UnidentifiedImageError, OSError):
            await ctx.send("❌ I could not decode that image.", ephemeral=True)
            return
        await ctx.send(file=discord.File(output, filename=f"{image.filename.rsplit('.', 1)[0]}.gif"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageTools())
