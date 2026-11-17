from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands
import pytest

from cogs.moderation import Moderation


def http_error(code: int, message: str) -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, {"code": code, "message": message})


@pytest.fixture
async def moderation_context():
    async with commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None) as bot:
        cog = Moderation()
        await bot.add_cog(cog)
        response = SimpleNamespace(done=False)
        response.is_done = lambda: response.done

        async def defer(**kwargs):
            assert not response.done, "An interaction can only be acknowledged once"
            response.done = True

        ctx = SimpleNamespace(
            bot=bot, cog=cog,
            interaction=SimpleNamespace(response=response, is_expired=lambda: False),
            defer=AsyncMock(side_effect=defer), send=AsyncMock(),
            guild=SimpleNamespace(id=42, name="Test Guild"),
            author=SimpleNamespace(id=7),
        )
        yield cog, ctx


@pytest.mark.parametrize("name", [
    "warn", "kick", "ban", "unban", "softban", "timeout", "untimeout",
    "role add", "role remove", "purge", "slowmode", "lock", "unlock", "nickname",
])
async def test_moderation_framework_hook_acknowledges_before_command(name, moderation_context):
    _, ctx = moderation_context
    await ctx.bot.get_command(name).call_before_hooks(ctx)
    ctx.defer.assert_awaited_once_with(ephemeral=True)
    assert ctx.interaction.response.is_done()


async def test_already_acknowledged_interaction_is_not_deferred_twice(moderation_context):
    cog, ctx = moderation_context
    ctx.interaction.response.done = True
    await cog.cog_before_invoke(ctx)
    ctx.defer.assert_not_awaited()


async def test_prefix_commands_are_not_deferred(moderation_context):
    cog, ctx = moderation_context
    ctx.interaction = None
    await cog.cog_before_invoke(ctx)
    ctx.defer.assert_not_awaited()


@pytest.mark.parametrize("dm_fails", [False, True])
async def test_warn_acknowledges_before_dm_and_records_once(moderation_context, monkeypatch, dm_fails):
    cog, ctx = moderation_context
    cog._target_error = AsyncMock(return_value=False)
    records = AsyncMock()
    monkeypatch.setattr("cogs.moderation.database.record_moderation_action", records)
    monkeypatch.setattr("cogs.moderation.database.get_channel", AsyncMock(return_value=None))
    monkeypatch.setattr("cogs.moderation.settings.LOG_CHANNEL_ID", None)

    async def send_dm(message):
        assert ctx.interaction.response.is_done(), "The DM must not delay the initial reply"
        if dm_fails:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DMs are closed")

    member = SimpleNamespace(id=8, send=AsyncMock(side_effect=send_dm))
    await ctx.bot.get_command("warn").call_before_hooks(ctx)
    await cog.warn.callback(cog, ctx, member, reason="Test warning")
    records.assert_awaited_once_with(42, 8, 7, "Warning", reason="Test warning", duration_seconds=None, delete_days=None)
    member.send.assert_awaited_once()
    ctx.send.assert_awaited_once()
    assert ctx.send.call_args.kwargs["ephemeral"] is True
    assert ("could not DM" if dm_fails else "notified by DM") in ctx.send.call_args.args[0]


async def test_purge_uses_shared_acknowledgement_once(moderation_context):
    cog, ctx = moderation_context
    cog._log_action = AsyncMock()
    ctx.channel = SimpleNamespace(mention="<#10>", purge=AsyncMock(return_value=[1, 2]))
    await ctx.bot.get_command("purge").call_before_hooks(ctx)
    await cog.purge.callback(cog, ctx, 2)
    ctx.defer.assert_awaited_once_with(ephemeral=True)
    ctx.channel.purge.assert_awaited_once()
    ctx.send.assert_awaited_once()


@pytest.mark.parametrize("code", [10062, 10015])
async def test_nested_expired_interaction_error_is_not_retried(moderation_context, code):
    cog, ctx = moderation_context
    original = http_error(code, "Unknown interaction")
    application_error = discord.app_commands.CommandInvokeError(cog.warn.app_command, original)
    error = commands.HybridCommandError(application_error)
    await cog.on_command_error(ctx, error)
    ctx.send.assert_not_awaited()


async def test_expired_token_skips_error_reply(moderation_context):
    cog, ctx = moderation_context
    ctx.interaction.is_expired = lambda: True
    await cog.on_command_error(ctx, commands.BadArgument("Invalid input"))
    ctx.send.assert_not_awaited()


async def test_error_response_http_failure_does_not_escape_handler(moderation_context):
    cog, ctx = moderation_context
    ctx.send.side_effect = http_error(10062, "Unknown interaction")
    await cog.on_command_error(ctx, commands.MissingPermissions(["moderate_members"]))
    ctx.send.assert_awaited_once_with("You do not have permission to use that command.", ephemeral=True)


async def test_action_http_error_still_gets_reported(moderation_context):
    cog, ctx = moderation_context
    error = commands.CommandInvokeError(http_error(10007, "Unknown member"))
    await cog.on_command_error(ctx, error)
    ctx.send.assert_awaited_once_with("Something went wrong while running that command.", ephemeral=True)


async def test_prefix_permission_error_still_gets_reported(moderation_context):
    cog, ctx = moderation_context
    ctx.interaction = None
    await cog.on_command_error(ctx, commands.BotMissingPermissions(["ban_members"]))
    ctx.send.assert_awaited_once_with("I am missing a required Discord permission.", ephemeral=True)
