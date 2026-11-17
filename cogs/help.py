"""Paginated help and per-server command organization."""

from __future__ import annotations

from collections import defaultdict

import discord
from discord.ext import commands

import database


DEFAULT_HEADER = "Miscellaneous Commands"
ADMIN_HEADER = "Administrator Commands"
MOD_HEADER = "Mod Commands"
HELP_HEADERS_SETTING = "help_command_headers"
# Hide only these group entries; their individual subcommands stay in help.
HIDDEN_HELP_COMMANDS = {
    "assistant", "antiraid", "autorole", "botsetup", "farewell", "health",
    "help_config", "levels", "notify", "role", "schedule", "starboard",
    "welcome", "incident", "infractions",
}
DEFAULT_COMMAND_GROUPS = {
    DEFAULT_HEADER: (
        "help",
        "levels leaderboard",
        "serverinfo",
        "userinfo",
        "assistant infractions",
        "assistant profile",
    ),
    "Useful Commands": (
        "8ball",
        "choose",
        "coinflip",
        "nickname",
        "rate",
        "roll",
        "ship",
        "to_gif",
    ),
    ADMIN_HEADER: (
        # Server setup and help organization.
        "botsetup panel",
        "botsetup show",
        "botsetup channel",
        "botsetup clear",
        "botsetup membercount",
        "botsetup sync",
        "help_config assign",
        "help_config remove",
        "help_config clear",
        # Member roles and join/leave messages.
        "autorole set",
        "autorole clear",
        "role add",
        "role remove",
        "reactionroles",
        "welcome set",
        "welcome preview",
        "welcome disable",
        "farewell set",
        "farewell disable",
        # Server protection and channel controls.
        "automod",
        "automod enable",
        "automod word",
        "automod disable",
        "antiraid enable",
        "antiraid disable",
        "lock",
        "unlock",
        "slowmode",
        "purge",
        # Community features, scheduling, and notifications.
        "levels enable",
        "levels reward",
        "levels disable",
        "starboard set",
        "starboard disable",
        "schedule add",
        "schedule list",
        "schedule remove",
        "notify add",
        "notify list",
        "notify remove",
        "health enable",
        "health disable",
        "check_update",
        "last_update",
    ),
    MOD_HEADER: (
        "ban",
        "infractions view",
        "kick",
        "softban",
        "timeout",
        "unban",
        "warn",
        "untimeout",
        "incident add",
        "incident close",
        "incident open",
        "incident view",
    ),
    "music": (
        "leave",
        "pause",
        "play",
        "queue",
        "resume",
        "skip",
        "stop",
        "volume",
    ),
}
# Match full command names so new commands use Miscellaneous Commands.
DEFAULT_COMMAND_HEADERS = {
    name: header for header, names in DEFAULT_COMMAND_GROUPS.items() for name in names
}


def _command_entries(bot: commands.Bot) -> list[tuple[str, str, str]]:
    """Return unique application commands as (name, description, header) tuples."""
    entries: dict[str, tuple[str, str]] = {}
    for command in bot.tree.walk_commands():
        binding = getattr(command, "binding", None)
        if getattr(command, "hidden", False) or binding.__class__.__name__ == "OwnerCommands":
            continue
        name = command.qualified_name
        if name in HIDDEN_HELP_COMMANDS:
            continue
        description = getattr(command, "description", None) or "No description provided."
        entries[name] = (description, DEFAULT_COMMAND_HEADERS.get(name, DEFAULT_HEADER))
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
        for name, description, default_header in _command_entries(self.bot):
            header = self.headers.get(name, default_header)
            self.entries.append((name, description, header))
        grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for name, description, header in self.entries:
            grouped[header or DEFAULT_HEADER].append((name, description))
        self.pages = []
        header_order = {header: index for index, header in enumerate(DEFAULT_COMMAND_GROUPS)}
        for header in sorted(grouped, key=lambda header: (header_order.get(header, len(header_order)), header.casefold())):
            category_entries = grouped[header]
            if header == ADMIN_HEADER:
                command_order = {name: index for index, name in enumerate(DEFAULT_COMMAND_GROUPS[ADMIN_HEADER])}
                category_entries.sort(key=lambda entry: (command_order.get(entry[0], len(command_order)), entry[0]))
            lines = [f"`/{name}` — {description}" for name, description in category_entries]
            page_size = 20 if header == ADMIN_HEADER else len(lines) if header == MOD_HEADER else 8
            for start in range(0, len(lines), page_size):
                end = min(start + page_size, len(lines))
                suffix = "" if len(lines) <= page_size else f" ({start + 1}-{end})"
                self.pages.append((f"{header}{suffix}", "\n".join(lines[start:end])))
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
        default_header = DEFAULT_COMMAND_HEADERS.get(command, DEFAULT_HEADER)
        await ctx.send(f"`/{command}` was returned to **{default_header}**.", ephemeral=True)

    @help_config.command(name="clear", description="Clear all custom help headers for this server.")
    async def clear(self, ctx: commands.Context) -> None:
        await database.set_setting(ctx.guild.id, HELP_HEADERS_SETTING, {})
        await ctx.send("All custom help headers were cleared. Commands now use their default categories.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
