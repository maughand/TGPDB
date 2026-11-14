"""Guild-scoped SQLite persistence for TGPDB.

The connection is intentionally not opened at import time.  The bot opens it
after Discord reports that it is ready, while commands can also initialize it
on demand if they are used during a reconnect.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any, Optional

import discord
import settings


_connection: Optional[sqlite3.Connection] = None
_lock = asyncio.Lock()


def is_initialized() -> bool:
    return _connection is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def setup_database() -> None:
    """Open the database and create the schema if it does not exist."""
    global _connection
    async with _lock:
        if _connection is not None:
            return
        settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _connection = sqlite3.connect(settings.DATABASE_PATH)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA foreign_keys = ON")
        _connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id INTEGER,
                member_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guild_channels (
                guild_id INTEGER NOT NULL,
                feature TEXT NOT NULL,
                channel_id INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, feature),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS guild_members (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                joined_at TEXT,
                is_bot INTEGER NOT NULL DEFAULT 0,
                role_ids TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER NOT NULL,
                setting TEXT NOT NULL,
                value TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, setting),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS notification_subscriptions (
                subscription_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                identifier TEXT NOT NULL,
                label TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_item_id TEXT,
                last_live_state INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (guild_id, platform, identifier),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_notification_subscriptions_enabled
                ON notification_subscriptions (enabled, platform);
            CREATE TABLE IF NOT EXISTS moderation_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                moderator_user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                duration_seconds INTEGER,
                delete_days INTEGER,
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_moderation_actions_member
                ON moderation_actions (guild_id, target_user_id, created_at);
            CREATE TABLE IF NOT EXISTS member_moderation (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                mute_count INTEGER NOT NULL DEFAULT 0,
                ban_count INTEGER NOT NULL DEFAULT 0,
                softban_count INTEGER NOT NULL DEFAULT 0,
                warn_count INTEGER NOT NULL DEFAULT 0,
                kick_count INTEGER NOT NULL DEFAULT 0,
                currently_muted INTEGER NOT NULL DEFAULT 0,
                currently_banned INTEGER NOT NULL DEFAULT 0,
                last_action TEXT,
                last_reason TEXT,
                last_action_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS reaction_roles (
                guild_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, message_id, emoji),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_reaction_roles_message
                ON reaction_roles (guild_id, message_id);
            CREATE TABLE IF NOT EXISTS message_snapshots (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                attachments TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                deleted_at TEXT,
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS message_deletion_claims (
                message_id INTEGER PRIMARY KEY,
                claimed_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                send_at TEXT NOT NULL,
                repeat_minutes INTEGER,
                created_by INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS daily_analytics (
                guild_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                joins INTEGER NOT NULL DEFAULT 0,
                leaves INTEGER NOT NULL DEFAULT 0,
                commands INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, day),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS member_xp (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 0,
                last_awarded_at TEXT,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS level_rewards (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, level),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS starboard_messages (
                guild_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                starboard_message_id INTEGER NOT NULL,
                claimed_at TEXT,
                PRIMARY KEY (guild_id, message_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS cs2_updates (
                update_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                release INTEGER NOT NULL,
                url TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cs2_update_deliveries (
                guild_id INTEGER NOT NULL,
                update_id TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, update_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                FOREIGN KEY (update_id) REFERENCES cs2_updates(update_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS cs2_update_claims (
                guild_id INTEGER NOT NULL,
                update_id TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, update_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                FOREIGN KEY (update_id) REFERENCES cs2_updates(update_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS incident_cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                opened_by INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                closed_at TEXT,
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS incident_evidence (
                evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                attachment_url TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES incident_cases(case_id) ON DELETE CASCADE,
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_incident_cases_guild
                ON incident_cases (guild_id, status, created_at);
            """
        )
        starboard_columns = {row["name"] for row in _connection.execute("PRAGMA table_info(starboard_messages)").fetchall()}
        if "claimed_at" not in starboard_columns:
            _connection.execute("ALTER TABLE starboard_messages ADD COLUMN claimed_at TEXT")
        deletion_claim_columns = {row["name"] for row in _connection.execute("PRAGMA table_info(message_deletion_claims)").fetchall()}
        if "completed_at" not in deletion_claim_columns:
            _connection.execute("ALTER TABLE message_deletion_claims ADD COLUMN completed_at TEXT")
        moderation_columns = {row["name"] for row in _connection.execute("PRAGMA table_info(member_moderation)").fetchall()}
        if "warn_count" not in moderation_columns:
            _connection.execute("ALTER TABLE member_moderation ADD COLUMN warn_count INTEGER NOT NULL DEFAULT 0")
            if "warning_count" in moderation_columns:
                _connection.execute("UPDATE member_moderation SET warn_count = warning_count WHERE warning_count > 0")
        if "kick_count" not in moderation_columns:
            _connection.execute("ALTER TABLE member_moderation ADD COLUMN kick_count INTEGER NOT NULL DEFAULT 0")
        stale_claim_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        _connection.execute(
            "DELETE FROM starboard_messages WHERE starboard_message_id = 0 "
            "AND (claimed_at IS NULL OR claimed_at < ?)",
            (stale_claim_cutoff,),
        )
        _connection.commit()


async def close_database() -> None:
    """Flush and close the database connection."""
    global _connection
    async with _lock:
        if _connection is not None:
            _connection.commit()
            _connection.close()
            _connection = None


async def backup_database(destination: Any) -> None:
    """Create a consistent SQLite backup while serializing application writes."""
    connection = await _ensure_database()
    async with _lock:
        backup = sqlite3.connect(destination)
        try:
            connection.backup(backup)
        finally:
            backup.close()


async def _ensure_database() -> sqlite3.Connection:
    if _connection is None:
        await setup_database()
    if _connection is None:  # pragma: no cover - defensive guard
        raise RuntimeError("Database could not be initialized")
    return _connection


def _ensure_guild_row(connection: sqlite3.Connection, guild_id: int) -> None:
    """Guarantee a parent guild row before inserting guild-scoped child data."""
    now = _now()
    connection.execute(
        """
        INSERT INTO guilds (guild_id, name, owner_id, member_count, created_at, updated_at)
        VALUES (?, 'Unknown Guild', NULL, 0, ?, ?)
        ON CONFLICT(guild_id) DO NOTHING
        """,
        (guild_id, now, now),
    )


async def upsert_guild(guild: discord.Guild) -> None:
    connection = await _ensure_database()
    now = _now()
    async with _lock:
        connection.execute(
            """
            INSERT INTO guilds (guild_id, name, owner_id, member_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                name=excluded.name, owner_id=excluded.owner_id,
                member_count=excluded.member_count, updated_at=excluded.updated_at
            """,
            (guild.id, guild.name, guild.owner_id, guild.member_count, now, now),
        )
        connection.commit()


async def sync_guild_members(guild: discord.Guild) -> None:
    """Store the current member snapshot for one guild."""
    connection = await _ensure_database()
    await upsert_guild(guild)
    now = _now()
    rows = [
        (
            guild.id,
            member.id,
            str(member),
            member.display_name,
            member.joined_at.isoformat() if member.joined_at else None,
            int(member.bot),
            json.dumps([role.id for role in member.roles]),
            now,
        )
        for member in guild.members
    ]
    async with _lock:
        connection.executemany(
            """
            INSERT INTO guild_members
                (guild_id, user_id, username, display_name, joined_at, is_bot, role_ids, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                username=excluded.username, display_name=excluded.display_name,
                joined_at=excluded.joined_at, is_bot=excluded.is_bot,
                role_ids=excluded.role_ids, updated_at=excluded.updated_at
            """,
            rows,
        )
        connection.execute(
            "DELETE FROM guild_members WHERE guild_id = ? AND updated_at != ?",
            (guild.id, now),
        )
        connection.commit()


async def upsert_member(member: discord.Member) -> None:
    connection = await _ensure_database()
    await upsert_guild(member.guild)
    now = _now()
    async with _lock:
        _ensure_guild_row(connection, member.guild.id)
        connection.execute(
            """
            INSERT INTO guild_members
                (guild_id, user_id, username, display_name, joined_at, is_bot, role_ids, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                username=excluded.username, display_name=excluded.display_name,
                joined_at=excluded.joined_at, is_bot=excluded.is_bot,
                role_ids=excluded.role_ids, updated_at=excluded.updated_at
            """,
            (
                member.guild.id,
                member.id,
                str(member),
                member.display_name,
                member.joined_at.isoformat() if member.joined_at else None,
                int(member.bot),
                json.dumps([role.id for role in member.roles]),
                now,
            ),
        )
        connection.commit()


async def remove_member(guild_id: int, user_id: int) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute("DELETE FROM guild_members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        connection.commit()


async def set_channel(guild_id: int, feature: str, channel_id: Optional[int]) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            """
            INSERT INTO guild_channels (guild_id, feature, channel_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, feature) DO UPDATE SET
                channel_id=excluded.channel_id, updated_at=excluded.updated_at
            """,
            (guild_id, feature, channel_id, _now()),
        )
        connection.commit()


async def get_channel(guild_id: int, feature: str) -> Optional[int]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT channel_id FROM guild_channels WHERE guild_id = ? AND feature = ?",
            (guild_id, feature),
        ).fetchone()
    return int(row["channel_id"]) if row and row["channel_id"] is not None else None


async def get_channels(guild_id: int) -> dict[str, Optional[int]]:
    connection = await _ensure_database()
    async with _lock:
        rows = connection.execute(
            "SELECT feature, channel_id FROM guild_channels WHERE guild_id = ? ORDER BY feature",
            (guild_id,),
        ).fetchall()
    return {row["feature"]: row["channel_id"] for row in rows}


async def set_setting(guild_id: int, setting: str, value: Any) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            """
            INSERT INTO guild_settings (guild_id, setting, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, setting) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at
            """,
            (guild_id, setting, json.dumps(value), _now()),
        )
        connection.commit()


async def get_setting(guild_id: int, setting: str, default: Any = None) -> Any:
    """Return one guild-scoped setting, or ``default`` when it is not set."""
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND setting = ?",
            (guild_id, setting),
        ).fetchone()
    if row is None or row["value"] is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


async def add_notification_subscription(
    guild_id: int,
    platform: str,
    identifier: str,
    label: str,
    channel_id: int,
    created_by: int,
) -> None:
    connection = await _ensure_database()
    now = _now()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            """
            INSERT INTO notification_subscriptions
                (guild_id, platform, identifier, label, channel_id, created_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, platform, identifier) DO UPDATE SET
                label=excluded.label, channel_id=excluded.channel_id,
                enabled=1, updated_at=excluded.updated_at
            """,
            (guild_id, platform, identifier, label, channel_id, created_by, now),
        )
        connection.commit()


async def remove_notification_subscription(guild_id: int, platform: str, identifier: str) -> bool:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        cursor = connection.execute(
            "DELETE FROM notification_subscriptions WHERE guild_id = ? AND platform = ? AND identifier = ?",
            (guild_id, platform, identifier),
        )
        connection.commit()
    return cursor.rowcount > 0


async def get_notification_subscriptions(guild_id: Optional[int] = None) -> list[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        if guild_id is None:
            rows = connection.execute(
                "SELECT * FROM notification_subscriptions WHERE enabled = 1"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM notification_subscriptions WHERE guild_id = ? AND enabled = 1",
                (guild_id,),
            ).fetchall()
    return [dict(row) for row in rows]


async def update_notification_state(
    subscription_id: int,
    *,
    last_item_id: Optional[str] = None,
    last_live_state: Optional[bool] = None,
) -> None:
    connection = await _ensure_database()
    fields = ["updated_at = ?"]
    values: list[Any] = [_now()]
    if last_item_id is not None:
        fields.append("last_item_id = ?")
        values.append(last_item_id)
    if last_live_state is not None:
        fields.append("last_live_state = ?")
        values.append(int(last_live_state))
    values.append(subscription_id)
    async with _lock:
        connection.execute(
            f"UPDATE notification_subscriptions SET {', '.join(fields)} WHERE subscription_id = ?",
            values,
        )
        connection.commit()


async def record_moderation_action(
    guild_id: int,
    target_user_id: int,
    moderator_user_id: int,
    action: str,
    *,
    reason: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    delete_days: Optional[int] = None,
) -> None:
    """Record moderation history and update the member's moderation summary."""
    connection = await _ensure_database()
    normalized = action.casefold().replace("_", " ").strip()
    is_mute = normalized in {"timeout", "mute"}
    is_unmute = normalized in {"untimeout", "remove timeout", "unmute"}
    is_ban = normalized == "ban"
    is_unban = normalized == "unban"
    is_softban = normalized == "softban"
    is_warning = normalized == "warning"
    is_kick = normalized == "kick"
    active = int(is_mute or is_ban)
    now = _now()

    async with _lock:
        _ensure_guild_row(connection, guild_id)
        if is_mute:
            # A new timeout replaces any older active timeout for this member.
            connection.execute(
                "UPDATE moderation_actions SET active = 0 WHERE guild_id = ? AND target_user_id = ? "
                "AND action IN ('Timeout', 'Mute') AND active = 1",
                (guild_id, target_user_id),
            )
        if is_unmute:
            connection.execute(
                "UPDATE moderation_actions SET active = 0 WHERE guild_id = ? AND target_user_id = ? AND action IN ('Timeout', 'Mute') AND active = 1",
                (guild_id, target_user_id),
            )
        if is_unban:
            connection.execute(
                "UPDATE moderation_actions SET active = 0 WHERE guild_id = ? AND target_user_id = ? AND action = 'Ban' AND active = 1",
                (guild_id, target_user_id),
            )

        connection.execute(
            """
            INSERT INTO moderation_actions
                (guild_id, target_user_id, moderator_user_id, action, reason,
                 duration_seconds, delete_days, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, target_user_id, moderator_user_id, action, reason,
             duration_seconds, delete_days, active, now),
        )

        summary = connection.execute(
            "SELECT mute_count, ban_count, softban_count, warn_count, kick_count, currently_muted, currently_banned FROM member_moderation WHERE guild_id = ? AND user_id = ?",
            (guild_id, target_user_id),
        ).fetchone()
        mute_count = summary["mute_count"] if summary else 0
        ban_count = summary["ban_count"] if summary else 0
        softban_count = summary["softban_count"] if summary else 0
        warning_count = summary["warn_count"] if summary else 0
        kick_count = summary["kick_count"] if summary else 0
        currently_muted = summary["currently_muted"] if summary else 0
        currently_banned = summary["currently_banned"] if summary else 0
        if is_mute:
            mute_count += 1
            currently_muted = 1
        elif is_unmute:
            currently_muted = 0
        elif is_ban:
            ban_count += 1
            currently_banned = 1
        elif is_unban:
            currently_banned = 0
        elif is_softban:
            softban_count += 1
        elif is_warning:
            warning_count += 1
        elif is_kick:
            kick_count += 1

        connection.execute(
            """
            INSERT INTO member_moderation
                (guild_id, user_id, mute_count, ban_count, softban_count, warn_count, kick_count,
                 currently_muted, currently_banned, last_action, last_reason, last_action_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                mute_count=excluded.mute_count, ban_count=excluded.ban_count,
                softban_count=excluded.softban_count, currently_muted=excluded.currently_muted,
                warn_count=excluded.warn_count, kick_count=excluded.kick_count,
                currently_banned=excluded.currently_banned, last_action=excluded.last_action,
                last_reason=excluded.last_reason, last_action_at=excluded.last_action_at
            """,
            (guild_id, target_user_id, mute_count, ban_count, softban_count, warning_count, kick_count,
             currently_muted, currently_banned, action, reason, now),
        )
        connection.commit()


async def get_member_moderation(guild_id: int, user_id: int) -> Optional[dict[str, Any]]:
    """Return moderation counts and current mute/ban state for one member."""
    connection = await _ensure_database()
    async with _lock:
        await _expire_member_timeouts(connection, guild_id, user_id)
        row = connection.execute(
            "SELECT * FROM member_moderation WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
    return dict(row) if row else None


async def get_member_actions(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    """Return recent moderation actions for one guild member."""
    connection = await _ensure_database()
    async with _lock:
        await _expire_member_timeouts(connection, guild_id, user_id)
        rows = connection.execute(
            "SELECT * FROM moderation_actions WHERE guild_id = ? AND target_user_id = ? ORDER BY created_at",
            (guild_id, user_id),
        ).fetchall()
    return [dict(row) for row in rows]


async def _expire_member_timeouts(connection: sqlite3.Connection, guild_id: int, user_id: int) -> None:
    """Clear timeout state after its recorded duration has naturally elapsed."""
    rows = connection.execute(
        "SELECT action_id, created_at, duration_seconds FROM moderation_actions "
        "WHERE guild_id = ? AND target_user_id = ? AND action IN ('Timeout', 'Mute') AND active = 1",
        (guild_id, user_id),
    ).fetchall()
    now = datetime.now(timezone.utc)
    expired_ids: list[int] = []
    for row in rows:
        if row["duration_seconds"] is None:
            continue
        try:
            expires_at = datetime.fromisoformat(row["created_at"]) + timedelta(seconds=int(row["duration_seconds"]))
        except (TypeError, ValueError):
            continue
        if expires_at <= now:
            expired_ids.append(int(row["action_id"]))
    if not expired_ids:
        return
    connection.executemany("UPDATE moderation_actions SET active = 0 WHERE action_id = ?", [(item,) for item in expired_ids])
    remaining = connection.execute(
        "SELECT 1 FROM moderation_actions WHERE guild_id = ? AND target_user_id = ? "
        "AND action IN ('Timeout', 'Mute') AND active = 1 LIMIT 1",
        (guild_id, user_id),
    ).fetchone()
    connection.execute(
        "UPDATE member_moderation SET currently_muted = ? WHERE guild_id = ? AND user_id = ?",
        (int(remaining is not None), guild_id, user_id),
    )
    connection.commit()


async def replace_reaction_roles(
    guild_id: int,
    message_id: int,
    channel_id: int,
    mappings: list[tuple[str, int, int]],
) -> None:
    """Replace all reaction-role mappings for one guild message."""
    connection = await _ensure_database()
    now = _now()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            "DELETE FROM reaction_roles WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        )
        connection.executemany(
            """
            INSERT INTO reaction_roles
                (guild_id, message_id, channel_id, emoji, role_id, rank, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(guild_id, message_id, channel_id, emoji, role_id, rank, now) for emoji, role_id, rank in mappings],
        )
        connection.commit()


async def get_reaction_role(guild_id: int, message_id: int, emoji: str) -> Optional[int]:
    """Return the role configured for a reaction on one message."""
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            """
            SELECT role_id FROM reaction_roles
            WHERE guild_id = ? AND message_id = ? AND emoji = ?
            """,
            (guild_id, message_id, emoji),
        ).fetchone()
    return int(row["role_id"]) if row else None


async def save_message_snapshot(message: discord.Message) -> None:
    connection = await _ensure_database()
    attachments = json.dumps([{"url": item.url, "filename": item.filename} for item in message.attachments])
    async with _lock:
        _ensure_guild_row(connection, message.guild.id)
        connection.execute(
            """
            INSERT INTO message_snapshots
                (message_id, guild_id, channel_id, author_id, content, attachments, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                content=excluded.content, attachments=excluded.attachments
            """,
            (message.id, message.guild.id, message.channel.id, message.author.id,
             message.content, attachments, message.created_at.isoformat()),
        )
        connection.commit()


async def get_message_snapshot(message_id: int) -> Optional[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute("SELECT * FROM message_snapshots WHERE message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


async def mark_message_deleted(message_id: int) -> None:
    connection = await _ensure_database()
    async with _lock:
        connection.execute("UPDATE message_snapshots SET deleted_at = ? WHERE message_id = ?", (_now(), message_id))
        connection.execute("UPDATE message_deletion_claims SET completed_at = ? WHERE message_id = ?", (_now(), message_id))
        connection.commit()


async def claim_message_deletion(message_id: int) -> bool:
    """Atomically claim a deletion, allowing abandoned in-progress claims to retry."""
    connection = await _ensure_database()
    now = _now()
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    async with _lock:
        cursor = connection.execute(
            """
            INSERT INTO message_deletion_claims (message_id, claimed_at, completed_at)
            VALUES (?, ?, NULL)
            ON CONFLICT(message_id) DO UPDATE SET
                claimed_at = excluded.claimed_at,
                completed_at = NULL
            WHERE message_deletion_claims.completed_at IS NULL
              AND message_deletion_claims.claimed_at < ?
            """,
            (message_id, now, stale_cutoff),
        )
        connection.commit()
        return cursor.rowcount == 1


async def release_message_deletion_claim(message_id: int) -> None:
    """Release a deletion claim when the audit could not be delivered."""
    connection = await _ensure_database()
    async with _lock:
        connection.execute("DELETE FROM message_deletion_claims WHERE message_id = ?", (message_id,))
        connection.commit()


async def add_daily_stat(guild_id: int, day: str, field: str, amount: int = 1) -> None:
    if field not in {"messages", "joins", "leaves", "commands"}:
        raise ValueError(f"Unsupported analytics field: {field}")
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            "INSERT INTO daily_analytics (guild_id, day) VALUES (?, ?) ON CONFLICT(guild_id, day) DO NOTHING",
            (guild_id, day),
        )
        connection.execute(
            f"UPDATE daily_analytics SET {field} = {field} + ? WHERE guild_id = ? AND day = ?",
            (amount, guild_id, day),
        )
        connection.commit()


async def get_daily_stat(guild_id: int, day: str) -> Optional[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT * FROM daily_analytics WHERE guild_id = ? AND day = ?", (guild_id, day)
        ).fetchone()
    return dict(row) if row else None


async def get_daily_stats(guild_id: int, days: int = 7) -> list[dict[str, Any]]:
    connection = await _ensure_database()
    first_day = (datetime.now(timezone.utc).date() - timedelta(days=max(days - 1, 0))).isoformat()
    async with _lock:
        rows = connection.execute(
            "SELECT * FROM daily_analytics WHERE guild_id = ? AND day >= ? ORDER BY day DESC",
            (guild_id, first_day),
        ).fetchall()
    return [dict(row) for row in rows]


async def get_moderation_count(guild_id: int, since: str) -> int:
    """Count moderation actions recorded since an ISO-8601 timestamp."""
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM moderation_actions WHERE guild_id = ? AND created_at >= ?",
            (guild_id, since),
        ).fetchone()
    return int(row["total"] if row else 0)


async def add_scheduled_message(guild_id: int, channel_id: int, content: str, send_at: str, repeat_minutes: Optional[int], created_by: int) -> int:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        cursor = connection.execute(
            """
            INSERT INTO scheduled_messages (guild_id, channel_id, content, send_at, repeat_minutes, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, content, send_at, repeat_minutes, created_by),
        )
        connection.commit()
    return int(cursor.lastrowid)


async def get_due_scheduled_messages(now: str) -> list[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        rows = connection.execute(
            "SELECT * FROM scheduled_messages WHERE enabled = 1 AND send_at <= ? ORDER BY send_at", (now,)
        ).fetchall()
    return [dict(row) for row in rows]


async def complete_scheduled_message(schedule_id: int, next_send_at: Optional[str]) -> None:
    connection = await _ensure_database()
    async with _lock:
        if next_send_at:
            connection.execute("UPDATE scheduled_messages SET send_at = ? WHERE schedule_id = ?", (next_send_at, schedule_id))
        else:
            connection.execute("UPDATE scheduled_messages SET enabled = 0 WHERE schedule_id = ?", (schedule_id,))
        connection.commit()


async def get_scheduled_messages(guild_id: int) -> list[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        rows = connection.execute(
            "SELECT * FROM scheduled_messages WHERE guild_id = ? AND enabled = 1 ORDER BY send_at",
            (guild_id,),
        ).fetchall()
    return [dict(row) for row in rows]


async def remove_scheduled_message(guild_id: int, schedule_id: int) -> bool:
    connection = await _ensure_database()
    async with _lock:
        cursor = connection.execute(
            "DELETE FROM scheduled_messages WHERE guild_id = ? AND schedule_id = ?",
            (guild_id, schedule_id),
        )
        connection.commit()
    return cursor.rowcount > 0


async def award_xp(guild_id: int, user_id: int, amount: int, now: str) -> dict[str, int]:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        row = connection.execute("SELECT xp, level FROM member_xp WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        xp = (int(row["xp"]) if row else 0) + amount
        level = int(xp ** 0.5 // 5)
        connection.execute(
            """
            INSERT INTO member_xp (guild_id, user_id, xp, level, last_awarded_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp, level=excluded.level, last_awarded_at=excluded.last_awarded_at
            """,
            (guild_id, user_id, xp, level, now),
        )
        connection.commit()
    return {"xp": xp, "level": level}


async def get_xp(guild_id: int, user_id: int) -> Optional[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute("SELECT * FROM member_xp WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    return dict(row) if row else None


async def get_xp_leaderboard(guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        rows = connection.execute("SELECT * FROM member_xp WHERE guild_id = ? ORDER BY xp DESC LIMIT ?", (guild_id, limit)).fetchall()
    return [dict(row) for row in rows]


async def set_level_reward(guild_id: int, level: int, role_id: int) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?, ?, ?) ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
            (guild_id, level, role_id),
        )
        connection.commit()


async def create_incident_case(guild_id: int, title: str, opened_by: int) -> int:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        cursor = connection.execute(
            "INSERT INTO incident_cases (guild_id, title, opened_by, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, title[:200], opened_by, _now()),
        )
        connection.commit()
    return int(cursor.lastrowid)


async def get_incident_case(guild_id: int, case_id: int) -> Optional[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT * FROM incident_cases WHERE guild_id = ? AND case_id = ?",
            (guild_id, case_id),
        ).fetchone()
    return dict(row) if row else None


async def close_incident_case(guild_id: int, case_id: int) -> bool:
    connection = await _ensure_database()
    async with _lock:
        cursor = connection.execute(
            "UPDATE incident_cases SET status = 'closed', closed_at = ? WHERE guild_id = ? AND case_id = ? AND status = 'open'",
            (_now(), guild_id, case_id),
        )
        connection.commit()
    return cursor.rowcount > 0


async def add_incident_evidence(
    guild_id: int,
    case_id: int,
    created_by: int,
    kind: str,
    content: str,
    attachment_url: Optional[str] = None,
) -> Optional[int]:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        exists = connection.execute(
            "SELECT 1 FROM incident_cases WHERE guild_id = ? AND case_id = ?",
            (guild_id, case_id),
        ).fetchone()
        if exists is None:
            return None
        cursor = connection.execute(
            """
            INSERT INTO incident_evidence
                (case_id, guild_id, created_by, kind, content, attachment_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (case_id, guild_id, created_by, kind, content[:4000], attachment_url, _now()),
        )
        connection.commit()
    return int(cursor.lastrowid)


async def get_incident_evidence(guild_id: int, case_id: int) -> list[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        rows = connection.execute(
            "SELECT * FROM incident_evidence WHERE guild_id = ? AND case_id = ? ORDER BY evidence_id",
            (guild_id, case_id),
        ).fetchall()
    return [dict(row) for row in rows]


async def get_level_reward(guild_id: int, level: int) -> Optional[int]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute("SELECT role_id FROM level_rewards WHERE guild_id = ? AND level = ?", (guild_id, level)).fetchone()
    return int(row["role_id"]) if row else None


async def get_starboard_message(guild_id: int, message_id: int) -> Optional[int]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT starboard_message_id FROM starboard_messages WHERE guild_id = ? AND message_id = ?",
            (guild_id, message_id),
        ).fetchone()
    return int(row["starboard_message_id"]) if row else None


async def set_starboard_message(guild_id: int, message_id: int, starboard_message_id: int) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            """
            INSERT INTO starboard_messages (guild_id, message_id, starboard_message_id, claimed_at)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(guild_id, message_id) DO UPDATE SET starboard_message_id=excluded.starboard_message_id, claimed_at=NULL
            """,
            (guild_id, message_id, starboard_message_id),
        )
        connection.commit()


async def claim_starboard_message(guild_id: int, message_id: int) -> bool:
    """Atomically reserve a source message so concurrent reactions post once."""
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        stale_claim_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        connection.execute(
            "DELETE FROM starboard_messages WHERE guild_id = ? AND message_id = ? AND starboard_message_id = 0 "
            "AND (claimed_at IS NULL OR claimed_at < ?)",
            (guild_id, message_id, stale_claim_cutoff),
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO starboard_messages (guild_id, message_id, starboard_message_id, claimed_at) VALUES (?, ?, 0, ?)",
            (guild_id, message_id, _now()),
        )
        connection.commit()
    return cursor.rowcount == 1


async def release_starboard_claim(guild_id: int, message_id: int) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            "DELETE FROM starboard_messages WHERE guild_id = ? AND message_id = ? AND starboard_message_id = 0",
            (guild_id, message_id),
        )
        connection.commit()


async def save_cs2_update(update: dict[str, Any]) -> None:
    connection = await _ensure_database()
    async with _lock:
        connection.execute(
            """INSERT INTO cs2_updates (update_id, title, description, release, url, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(update_id) DO UPDATE SET title=excluded.title, description=excluded.description,
               release=excluded.release, url=excluded.url, updated_at=excluded.updated_at""",
            (update["id"], update["title"], update["description"], update["release"], update["url"], _now()),
        )
        connection.commit()


async def get_cs2_update(update_id: str) -> Optional[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute("SELECT * FROM cs2_updates WHERE update_id = ?", (update_id,)).fetchone()
    return dict(row) if row else None


async def get_latest_cs2_update() -> Optional[dict[str, Any]]:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute("SELECT * FROM cs2_updates ORDER BY release DESC, updated_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


async def is_cs2_update_delivered(guild_id: int, update_id: str) -> bool:
    connection = await _ensure_database()
    async with _lock:
        row = connection.execute(
            "SELECT 1 FROM cs2_update_deliveries WHERE guild_id = ? AND update_id = ?", (guild_id, update_id)
        ).fetchone()
    return row is not None


async def mark_cs2_update_delivered(guild_id: int, update_id: str) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            "INSERT OR IGNORE INTO cs2_update_deliveries (guild_id, update_id, delivered_at) VALUES (?, ?, ?)",
            (guild_id, update_id, _now()),
        )
        connection.execute(
            "DELETE FROM cs2_update_claims WHERE guild_id = ? AND update_id = ?",
            (guild_id, update_id),
        )
        connection.commit()


async def claim_cs2_update_delivery(guild_id: int, update_id: str) -> bool:
    """Atomically reserve one guild's CS2 delivery for concurrent checkers."""
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        connection.execute("DELETE FROM cs2_update_claims WHERE claimed_at < ?", (stale,))
        cursor = connection.execute(
            "INSERT OR IGNORE INTO cs2_update_claims (guild_id, update_id, claimed_at) VALUES (?, ?, ?)",
            (guild_id, update_id, _now()),
        )
        connection.commit()
    return cursor.rowcount == 1


async def release_cs2_update_claim(guild_id: int, update_id: str) -> None:
    connection = await _ensure_database()
    async with _lock:
        _ensure_guild_row(connection, guild_id)
        connection.execute(
            "DELETE FROM cs2_update_claims WHERE guild_id = ? AND update_id = ?",
            (guild_id, update_id),
        )
        connection.commit()
