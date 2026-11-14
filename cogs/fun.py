"""Small, dependency-free entertainment commands."""

from __future__ import annotations

import hashlib
import random

import discord
from discord.ext import commands


class Fun(commands.Cog):
    """Lighthearted commands that do not call external services."""

    @commands.hybrid_command(name="8ball", description="Ask the magic 8-ball a question.")
    async def eight_ball(self, ctx: commands.Context, question: str) -> None:
        answers = (
            "Absolutely.", "Definitely not.", "Probably.", "Ask me again later.",
            "The outlook is surprisingly good.", "The stars say no.",
        )
        await ctx.send(f"🎱 **Question:** {question}\n**Answer:** {random.choice(answers)}")

    @commands.hybrid_command(name="roll", description="Roll a die.")
    async def roll(self, ctx: commands.Context, sides: commands.Range[int, 2, 1000] = 6) -> None:
        await ctx.send(f"🎲 {ctx.author.mention} rolled **{random.randint(1, sides)}** (d{sides}).")

    @commands.hybrid_command(name="coinflip", description="Flip a coin.")
    async def coinflip(self, ctx: commands.Context) -> None:
        await ctx.send(f"🪙 **{random.choice(('Heads', 'Tails'))}**")

    @commands.hybrid_command(name="choose", description="Choose one option separated by |.")
    async def choose(self, ctx: commands.Context, options: str) -> None:
        choices = [choice.strip() for choice in options.split("|") if choice.strip()]
        if len(choices) < 2:
            await ctx.send("❌ Give me at least two options separated by `|`.", ephemeral=True)
            return
        await ctx.send(f"🤔 I choose **{random.choice(choices)}**.")

    @commands.hybrid_command(name="rate", description="Give something a completely scientific rating.")
    async def rate(self, ctx: commands.Context, thing: str) -> None:
        digest = hashlib.sha256(thing.casefold().encode()).digest()
        score = digest[0] % 101
        await ctx.send(f"📊 I rate **{thing}** a **{score}/100**.")

    @commands.hybrid_command(name="ship", description="Calculate the totally scientific compatibility of two members.")
    async def ship(self, ctx: commands.Context, first: discord.Member, second: discord.Member) -> None:
        pair = ":".join(sorted((str(first.id), str(second.id))))
        score = hashlib.sha256(pair.encode()).digest()[0] % 101
        await ctx.send(f"💞 **{first.display_name}** + **{second.display_name}** = **{score}%** compatibility.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun())
