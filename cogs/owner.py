"""Bot-owner-only maintenance and administration commands."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import platform
import shutil

import discord
from discord import app_commands
from discord.ext import commands

import database
import settings


logger = logging.getLogger(__name__)


class OwnerCommands(commands.Cog):
    """Bot-wide maintenance commands restricted to the configured owner."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = dt.datetime.now(dt.timezone.utc)

    async def _deny_or_defer(self, interaction: discord.Interaction) -> bool:
        """Reject an unauthorized invocation without exposing command details."""
        if settings.OWNER_USER_ID is not None and interaction.user.id == settings.OWNER_USER_ID:
            return False
        if not interaction.response.is_done():
            await interaction.response.send_message("This command is unavailable.", ephemeral=True)
        return True

    async def _send_result(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="sync", description="Sync the bot's application commands.")
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        synced_global = await self.bot.tree.sync()
        synced_dev = 0
        if settings.DEV_GUILD_ID:
            dev_guild = discord.Object(id=settings.DEV_GUILD_ID)
            self.bot.tree.copy_global_to(guild=dev_guild)
            synced_dev = len(await self.bot.tree.sync(guild=dev_guild))
        scope = f"global ({len(synced_global)})"
        if settings.DEV_GUILD_ID:
            scope += f" and development guild ({synced_dev})"
        await interaction.followup.send(f"Application commands synced to {scope}.", ephemeral=True)

    @app_commands.command(name="reload", description="Reload one bot cog.")
    @app_commands.default_permissions(administrator=True)
    async def reload_cog(self, interaction: discord.Interaction, cog: str) -> None:
        if await self._deny_or_defer(interaction):
            return
        extension = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self.bot.reload_extension(extension)
            await self._send_result(interaction, f"Reloaded `{extension}`.")
        except commands.ExtensionError as error:
            await self._send_result(interaction, f"Could not reload `{extension}`: `{type(error).__name__}`.")

    @app_commands.command(name="reload_all", description="Reload every loaded bot cog.")
    @app_commands.default_permissions(administrator=True)
    async def reload_all(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        failures: list[str] = []
        for extension in list(self.bot.extensions):
            if extension == "cogs.owner":
                continue
            try:
                await self.bot.reload_extension(extension)
            except commands.ExtensionError:
                failures.append(extension)
        result = "All loaded cogs reloaded."
        if failures:
            result += " Failed: " + ", ".join(f"`{name}`" for name in failures)
        await self._send_result(interaction, result)

    @app_commands.command(name="load", description="Load a bot cog by name.")
    @app_commands.default_permissions(administrator=True)
    async def load(self, interaction: discord.Interaction, cog: str) -> None:
        if await self._deny_or_defer(interaction):
            return
        extension = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self.bot.load_extension(extension)
            await self._send_result(interaction, f"Loaded `{extension}`.")
        except commands.ExtensionError as error:
            await self._send_result(interaction, f"Could not load `{extension}`: `{type(error).__name__}`.")

    @app_commands.command(name="unload", description="Unload a bot cog by name.")
    @app_commands.default_permissions(administrator=True)
    async def unload(self, interaction: discord.Interaction, cog: str) -> None:
        if await self._deny_or_defer(interaction):
            return
        extension = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        if extension == "cogs.owner":
            await self._send_result(interaction, "The owner cog cannot be unloaded while this command is running.")
            return
        try:
            await self.bot.unload_extension(extension)
            await self._send_result(interaction, f"Unloaded `{extension}`.")
        except commands.ExtensionError as error:
            await self._send_result(interaction, f"Could not unload `{extension}`: `{type(error).__name__}`.")

    @app_commands.command(name="status", description="Show bot health and runtime status.")
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        uptime = dt.datetime.now(dt.timezone.utc) - self.started_at
        user_count = sum(guild.member_count or 0 for guild in self.bot.guilds)
        await self._send_result(
            interaction,
            "\n".join(
                [
                    f"Latency: `{self.bot.latency * 1000:.0f} ms`",
                    f"Uptime: `{str(uptime).split('.')[0]}`",
                    f"Guilds: `{len(self.bot.guilds)}`",
                    f"Cached members: `{user_count}`",
                    f"Loaded cogs: `{len(self.bot.extensions)}`",
                    f"Database initialized: `{database.is_initialized()}`",
                    f"Maintenance mode: `{getattr(self.bot, 'maintenance_mode', False)}`",
                ]
            ),
        )

    @app_commands.command(name="guilds", description="List guilds where the bot is installed.")
    @app_commands.default_permissions(administrator=True)
    async def guilds(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        lines = [f"`{guild.id}` — {guild.name}" for guild in self.bot.guilds]
        await self._send_result(interaction, "\n".join(lines)[:1900] or "The bot is not in any guilds.")

    @app_commands.command(name="leave_guild", description="Remove the bot from a guild by ID.")
    @app_commands.default_permissions(administrator=True)
    async def leave(self, interaction: discord.Interaction, guild_id: str) -> None:
        if await self._deny_or_defer(interaction):
            return
        try:
            guild = self.bot.get_guild(int(guild_id))
        except ValueError:
            guild = None
        if guild is None:
            await self._send_result(interaction, "Guild not found in the bot cache.")
            return
        await guild.leave()
        await self._send_result(interaction, f"Left `{guild.name}` (`{guild.id}`).")

    @app_commands.command(name="maintenance", description="Enable or disable global maintenance mode.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(enabled="Whether non-owner interactions should be blocked")
    async def maintenance(self, interaction: discord.Interaction, enabled: bool) -> None:
        if await self._deny_or_defer(interaction):
            return
        self.bot.maintenance_mode = enabled
        await self._send_result(interaction, f"Maintenance mode {'enabled' if enabled else 'disabled'}.")

    @app_commands.command(name="database_backup", description="Create a timestamped SQLite database backup.")
    @app_commands.default_permissions(administrator=True)
    async def database_backup(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        if not settings.DATABASE_PATH.exists():
            await self._send_result(interaction, "The database file does not exist yet.")
            return
        backup_dir = settings.DATABASE_PATH.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        destination = backup_dir / f"tgpdb-{timestamp}.sqlite3"
        shutil.copy2(settings.DATABASE_PATH, destination)
        await self._send_result(interaction, f"Database backup created: `{destination.name}`.")

    @app_commands.command(name="database_health", description="Check database initialization health.")
    @app_commands.default_permissions(administrator=True)
    async def database_health(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        await self._send_result(
            interaction,
            f"Database initialized: `{database.is_initialized()}`\nPath: `{settings.DATABASE_PATH.name}`\nExists: `{settings.DATABASE_PATH.exists()}`",
        )

    @app_commands.command(name="cache_clear", description="Ask loaded cogs to clear their in-memory caches.")
    @app_commands.default_permissions(administrator=True)
    async def cache_clear(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        cleared: list[str] = []
        for name, cog in self.bot.cogs.items():
            clear_cache = getattr(cog, "clear_cache", None)
            if clear_cache is not None:
                result = clear_cache()
                if asyncio.iscoroutine(result):
                    await result
                cleared.append(name)
        await self._send_result(interaction, "Cleared: " + ", ".join(cleared) if cleared else "No cache providers are registered.")

    @app_commands.command(name="permissions", description="Inspect the bot's permissions in this server.")
    @app_commands.default_permissions(administrator=True)
    async def permissions(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        if interaction.guild is None or interaction.guild.me is None:
            await self._send_result(interaction, "This command must be used in a server where the bot is cached.")
            return
        permissions = interaction.guild.me.guild_permissions
        missing = [name for name, value in permissions if not value]
        await self._send_result(interaction, "Missing permissions: " + (", ".join(missing) or "none"))

    @app_commands.command(name="announce_update", description="Send a bot announcement to configured announcement channels.")
    @app_commands.default_permissions(administrator=True)
    async def announce_update(self, interaction: discord.Interaction, message: str) -> None:
        if await self._deny_or_defer(interaction):
            return
        sent = 0
        for guild in self.bot.guilds:
            channel_id = await database.get_channel(guild.id, "announcements")
            channel = guild.get_channel(channel_id) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(message)
                    sent += 1
                except discord.HTTPException:
                    logger.warning("Could not announce in guild %s", guild.id)
        await self._send_result(interaction, f"Announcement sent to `{sent}` guild(s).")

    @app_commands.command(name="broadcast", description="Broadcast a message to every configured announcement channel.")
    @app_commands.default_permissions(administrator=True)
    async def broadcast(self, interaction: discord.Interaction, message: str) -> None:
        """Owner alias for sending a bot-wide announcement."""
        await self.announce_update.callback(self, interaction, message)

    @app_commands.command(name="logs", description="Show the most recent persisted bot log lines.")
    @app_commands.default_permissions(administrator=True)
    async def logs(self, interaction: discord.Interaction, lines: int = 20) -> None:
        if await self._deny_or_defer(interaction):
            return
        log_path = settings.DATABASE_PATH.parent / "bot.log"
        if not log_path.exists():
            await self._send_result(interaction, "No persisted bot.log file is configured.")
            return
        recent = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(lines, 50)):]
        output = "\n".join(recent)
        await self._send_result(interaction, f"```text\n{output[-1800:]}\n```")

    @app_commands.command(name="invite", description="Generate the bot invite URL.")
    @app_commands.default_permissions(administrator=True)
    async def invite(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        if self.bot.user is None:
            await self._send_result(interaction, "The bot user is not ready yet.")
            return
        permissions = discord.Permissions(administrator=True)
        url = discord.utils.oauth_url(self.bot.user.id, permissions=permissions, scopes=("bot", "applications.commands"))
        await self._send_result(interaction, url)

    @app_commands.command(name="debug", description="Show safe runtime configuration details.")
    @app_commands.default_permissions(administrator=True)
    async def debug(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        await self._send_result(
            interaction,
            "\n".join(
                [
                    f"Python/runtime: `{platform.python_version()}`",
                    f"Discord.py: `{discord.__version__}`",
                    f"Owner configured: `{settings.OWNER_USER_ID is not None}`",
                    f"Cobalt configured: `{bool(settings.COBALT_REQUEST_URL)}`",
                    f"Database path: `{settings.DATABASE_PATH.name}`",
                ]
            ),
        )

    @app_commands.command(name="shutdown", description="Safely shut down the bot process.")
    @app_commands.default_permissions(administrator=True)
    async def shutdown(self, interaction: discord.Interaction) -> None:
        if await self._deny_or_defer(interaction):
            return
        await self._send_result(interaction, "Shutting down safely.")
        await self.bot.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OwnerCommands(bot))
