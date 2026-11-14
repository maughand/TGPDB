from __future__ import annotations

from types import SimpleNamespace

import pytest

import database
import settings


@pytest.fixture
async def isolated_database(tmp_path, monkeypatch):
    await database.close_database()
    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.sqlite3")
    await database.setup_database()
    yield
    await database.close_database()


def fake_guild(guild_id: int = 42):
    return SimpleNamespace(id=guild_id, name="Test Guild", owner_id=7, member_count=3)


@pytest.mark.asyncio
async def test_guild_channels_and_settings_are_isolated(isolated_database):
    await database.upsert_guild(fake_guild(1))
    await database.upsert_guild(fake_guild(2))
    await database.set_channel(1, "welcome", 101)
    await database.set_setting(1, "automod_enabled", False)
    await database.set_channel(2, "welcome", 202)

    assert await database.get_channel(1, "welcome") == 101
    assert await database.get_channel(2, "welcome") == 202
    assert await database.get_channel(1, "farewell") is None
    assert await database.get_setting(1, "automod_enabled") is False
    assert await database.get_setting(2, "automod_enabled", True) is True


@pytest.mark.asyncio
async def test_incident_evidence_and_closing(isolated_database):
    await database.upsert_guild(fake_guild())
    case_id = await database.create_incident_case(42, "Raid investigation", 7)
    evidence_id = await database.add_incident_evidence(42, case_id, 7, "attachment", "Screenshot", "https://cdn.example/image.png")

    assert evidence_id is not None
    evidence = await database.get_incident_evidence(42, case_id)
    assert evidence[0]["attachment_url"].endswith("image.png")
    assert (await database.get_incident_case(42, case_id))["status"] == "open"
    assert await database.close_incident_case(42, case_id) is True
    assert (await database.get_incident_case(42, case_id))["status"] == "closed"


@pytest.mark.asyncio
async def test_xp_rewards_analytics_and_schedules(isolated_database):
    await database.upsert_guild(fake_guild())
    await database.add_daily_stat(42, "2026-11-14", "messages", 12)
    await database.add_daily_stat(42, "2026-11-14", "joins")
    xp = await database.award_xp(42, 99, 125, "2026-11-14T00:00:00+00:00")
    await database.set_level_reward(42, xp["level"], 555)
    schedule_id = await database.add_scheduled_message(42, 100, "Hello", "2026-11-14T00:00:00+00:00", None, 7)

    stats = await database.get_daily_stat(42, "2026-11-14")
    assert stats["messages"] == 12
    assert stats["joins"] == 1
    assert await database.get_level_reward(42, xp["level"]) == 555
    assert any(item["schedule_id"] == schedule_id for item in await database.get_scheduled_messages(42))
