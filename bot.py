import logging
import discord
from discord.ext import commands
import settings
import database

logger = logging.getLogger("bot")

APP_COMMAND_PERMISSIONS: dict[str, str] = {
    "botsetup": "manage_guild", "botsetup panel": "manage_guild", "botsetup channel": "manage_guild", "botsetup clear": "manage_guild",
    "botsetup membercount": "manage_guild", "botsetup show": "manage_guild", "botsetup sync": "manage_guild",
    "welcome": "manage_guild", "welcome set": "manage_guild", "welcome disable": "manage_guild", "welcome preview": "manage_guild",
    "farewell": "manage_guild", "farewell set": "manage_guild", "farewell disable": "manage_guild",
    "autorole": "manage_roles", "autorole set": "manage_roles", "autorole clear": "manage_roles",
    "automod": "manage_guild", "automod enable": "manage_guild", "automod disable": "manage_guild", "automod word": "manage_guild",
    "antiraid": "manage_guild", "antiraid enable": "manage_guild", "antiraid disable": "manage_guild",
    "schedule": "manage_messages", "schedule add": "manage_messages", "schedule list": "manage_messages", "schedule remove": "manage_messages",
    "starboard": "manage_guild", "starboard set": "manage_guild", "starboard disable": "manage_guild",
    "levels enable": "manage_guild", "levels disable": "manage_guild", "levels reward": "manage_roles",
    "infractions": "moderate_members", "infractions view": "moderate_members",
    "help_config": "manage_guild", "help_config assign": "manage_guild", "help_config remove": "manage_guild", "help_config clear": "manage_guild",
    "notify": "manage_guild", "notify add": "manage_guild", "notify remove": "manage_guild", "notify list": "manage_guild",
    "health enable": "manage_guild", "health disable": "manage_guild",
    "incident": "moderate_members", "incident open": "moderate_members", "incident add": "moderate_members", "incident view": "moderate_members", "incident close": "moderate_members",
    "reactionroles": "manage_roles", "check_update": "manage_guild",
    "kick": "kick_members", "ban": "ban_members", "unban": "ban_members", "softban": "ban_members",
    "timeout": "moderate_members", "untimeout": "moderate_members", "warn": "moderate_members",
    "role": "manage_roles", "role add": "manage_roles", "role remove": "manage_roles",
    "purge": "manage_messages", "slowmode": "manage_channels", "lock": "manage_channels", "unlock": "manage_channels",
    "nickname": "manage_nicknames",
}


class osbot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            help_command=None,
            intents=intents,
        )
        self.maintenance_mode = False
        self.guilds_synchronized = False
        self.tree.interaction_check = self._tree_interaction_check

    async def _tree_interaction_check(self, interaction: discord.Interaction) -> bool:
        """Block regular interactions while owner-enabled maintenance is active."""
        if not self.maintenance_mode:
            maintenance_allowed = True
        else:
            maintenance_allowed = bool(settings.OWNER_USER_ID and interaction.user.id == settings.OWNER_USER_ID)
        if not maintenance_allowed:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "The bot is currently in maintenance mode. Please try again later.",
                    ephemeral=True,
                )
            return False

        command = interaction.command
        required = APP_COMMAND_PERMISSIONS.get(command.qualified_name if command else "")
        if required and not getattr(interaction.user, "guild_permissions", discord.Permissions.none()).is_superset(discord.Permissions(**{required: True})):
            if not interaction.response.is_done():
                await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return False
        return True

    async def process_commands(self, message: discord.Message) -> None:
        """Apply maintenance mode to prefix commands as well as slash commands."""
        if (
            self.maintenance_mode
            and message.author.id != settings.OWNER_USER_ID
        ):
            return
        await super().process_commands(message)

    async def setup_hook(self) -> None:
        await database.setup_database()
        # Load cogs using pathlib's .stem and isolate failures.
        for cog_file in settings.COGS_DIR.glob("*.py"):
            if not cog_file.name.startswith("_"):
                ext_name = f"cogs.{cog_file.stem}"
                try:
                    await self.load_extension(ext_name)
                    logger.info(f"Loaded extension: {ext_name}")
                except commands.ExtensionError as e:
                    logger.error(f"Failed to load extension {ext_name}: {e}", exc_info=True)
        # Optional: Sync only to a specific dev guild on startup if in debug mode
        if getattr(settings, "DEV_GUILD_ID", None):
            dev_guild = discord.Object(id=settings.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=dev_guild)
            await self.tree.sync(guild=dev_guild)
            logger.info(f"Synced commands to dev guild {settings.DEV_GUILD_ID}")
        else:
            logger.info("Skipped global command sync on startup; use the owner-only /sync command when ready")

    async def on_ready(self) -> None:
        # Discord is fully connected here; defer database creation until this point.
        if not database.is_initialized():
            await database.setup_database()
        if not self.guilds_synchronized:
            for guild in self.guilds:
                await database.sync_guild_members(guild)
            logger.info("Initialized guild database and synchronized %d guild(s)", len(self.guilds))
            self.guilds_synchronized = True
        if self.user:
            logger.info(f"Logged in as {self.user} (ID: {self.user.id})")

    async def close(self) -> None:
        await database.close_database()
        await super().close()

    async def on_guild_available(self, guild: discord.Guild) -> None:
        if not database.is_initialized():
            await database.setup_database()
        await database.sync_guild_members(guild)

if __name__ == "__main__":
    # discord.py automatically configures the root logger when using bot.run()
    bot = osbot()
    bot.run(settings.TOKEN, root_logger=True)
