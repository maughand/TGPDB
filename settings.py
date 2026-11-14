"""Runtime configuration loaded from environment variables."""

from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent
COGS_DIR = BASE_DIR / "cogs"


def _load_local_env() -> None:
    """Load a small .env file without adding a runtime dependency."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()

DATABASE_PATH = Path(os.getenv("DATABASE_PATH") or str(BASE_DIR / "tgpdb.sqlite3"))

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN or DISCORD_TOKEN must be set before starting the bot")

DEV_GUILD_ID = int(os.environ["DEV_GUILD_ID"]) if os.getenv("DEV_GUILD_ID") else None
OWNER_USER_ID = int(os.environ["OWNER_USER_ID"]) if os.getenv("OWNER_USER_ID") else None
LOG_CHANNEL_ID = int(os.environ["LOG_CHANNEL_ID"]) if os.getenv("LOG_CHANNEL_ID") else None
COBALT_API_URL = os.getenv("COBALT_API_URL")
COBALT_REQUEST_URL = os.getenv("COBALT_REQUEST_URL") or COBALT_API_URL
COBALT_API_KEY = os.getenv("COBALT_API_KEY")
COBALT_TIMEOUT_SECONDS = int(os.getenv("COBALT_TIMEOUT_SECONDS", "45"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_APP_TOKEN = os.getenv("TWITCH_APP_TOKEN")
KICK_API_TOKEN = os.getenv("KICK_API_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
