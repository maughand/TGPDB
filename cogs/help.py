"""Paginated help and per-server command organization."""

from __future__ import annotations

from collections import defaultdict

import discord
from discord.ext import commands

import database


DEFAULT_HEADER = "Other commands"
HELP_HEADERS_SETTING = "help_command_headers"


def _command_entries(bot: commands.Bot) -> list[tuple[str, str, str]]:
    """Return unique application commands as (name, description, header) tuples."""
    entries: dict[str, tuple[str, str]] = {}
    for command in bot.tree.walk_commands():
        binding = getattr(command, "binding", None)
        if getattr(command, "hidden", False) or binding.__class__.__name__ == "OwnerCommands":
            continue
        name = command.qualified_name
        description = getattr(command, "description", None) or "No description provided."
        entries[name] = (description, DEFAULT_HEADER)
    return [(name, description, header) for name, (description, header) in sorted(entries.items())]


class HelpPages(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, user_id: int, headers: dict[str, str]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.user_id = user_id
        self.headers = headers
        self.page = 0
        self.entries: list[tuple[str, str, str]] = []
        self.pages: list[tuple[str, str]] = []
        self.previous_button = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
        self.previous_button.callback = self._previous
        self.add_item(self.previous_button)
        self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary)
        self.next_button.callback = self._next
        self.add_item(self.next_button)
        self.close_button = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary)
        self.close_button.callback = self._close
        self.add_item(self.close_button)

    async def load(self) -> None:
        self.entries = []
        for name, description, _ in _command_entries(self.bot):
            header = self.headers.get(name, DEFAULT_HEADER)
            self.entries.append((name, description, header))
        grouped: dict[str, list[str]] = defaultdict(list)
        for name, description, header in self.entries:
            grouped[header or DEFAULT_HEADER].append(f"`/{name}` — {description}")
        self.pages = []
        for header in sorted(grouped, key=str.casefold):
            lines = grouped[header]
            for start in range(0, len(lines), 8):
                suffix = "" if len(lines) <= 8 else f" ({start + 1}-{min(start + 8, len(lines))})"
                self.pages.append((f"{header}{suffix}", "\n".join(lines[start:start + 8])))
        if not self.pages:
            self.pages = [("No commands available", "The bot has not loaded any application commands yet.")]
        self.page = min(self.page, len(self.pages) - 1)

    def embed(self) -> discord.Embed:
        title, description = self.pages[self.page]
        embed = discord.Embed(
            title=f"{self.guild.name} · Help",
            description=f"**{title}**\n\n{description}",
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=f"Page {self.page + 1}/{len(self.pages)} · Use /help_config to organize commands")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help panel belongs to the person who opened it.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self.previous_button.disabled = self.page == 0
        self.next_button.disabled = self.page == len(self.pages) - 1
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(len(self.pages) - 1, self.page + 1)
        await self._refresh(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)


class Help(commands.Cog):
    """Show commands and let each server organize them into custom headers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _headers(self, guild_id: int) -> dict[str, str]:
        value = await database.get_setting(guild_id, HELP_HEADERS_SETTING, {})
        return value if isinstance(value, dict) else {}

    @commands.hybrid_command(name="help", description="Browse the bot's commands by category.")
    @commands.guild_only()
    async def help_command(self, ctx: commands.Context) -> None:
        panel = HelpPages(self.bot, ctx.guild, ctx.author.id, await self._headers(ctx.guild.id))
        await panel.load()
        panel.previous_button.disabled = True
        panel.next_button.disabled = len(panel.pages) == 1
        await ctx.send(embed=panel.embed(), view=panel, ephemeral=ctx.interaction is not None)

    @commands.hybrid_group(name="help_config", description="Organize commands in the help menu.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def help_config(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send(
                "Use `/help_config assign command header` or `/help_config remove command`.",
                ephemeral=True,
            )

    @help_config.command(name="assign", description="Put a command under a custom help header.")
    async def assign(self, ctx: commands.Context, command: str, header: str) -> None:
        command = command.strip().removeprefix("/").casefold()
        available = {name.casefold() for name, _, _ in _command_entries(self.bot)}
        if command not in available:
            await ctx.send(f"I could not find `/{command}`. Use `/help` to see available commands.", ephemeral=True)
            return
        headers = await self._headers(ctx.guild.id)
        headers[command] = header.strip()[:80] or DEFAULT_HEADER
        await database.set_setting(ctx.guild.id, HELP_HEADERS_SETTING, headers)
        await ctx.send(f"`/{command}` is now listed under **{headers[command]}**.", ephemeral=True)

    @help_config.command(name="remove", description="Return a command to the default help header.")
    async def remove(self, ctx: commands.Context, command: str) -> None:
        command = command.strip().removeprefix("/").casefold()
        headers = await self._headers(ctx.guild.id)
        if headers.pop(command, None) is None:
            await ctx.send(f"`/{command}` has no custom header.", ephemeral=True)
            return
        await database.set_setting(ctx.guild.id, HELP_HEADERS_SETTING, headers)
        await ctx.send(f"`/{command}` was returned to **{DEFAULT_HEADER}**.", ephemeral=True)

    @help_config.command(name="clear", description="Clear all custom help headers for this server.")
    async def clear(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, HELP_HEADERS_SETTING, {})
        await ctx.send("All custom help headers were cleared.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
