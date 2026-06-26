"""Tests for ActivityModuleSettingsWrapper — the excluded-channels set is now
backed by the ``activity_excluded_channel`` junction table instead of a CSV
column, but the wrapper's public API is unchanged.

The wrapper is cached per guild in ``activity_module_settings_helper.__CACHE``,
which survives the per-test DB rollback, so the autouse fixture below clears it
between tests to prevent leakage.
"""
import pytest

from db import DB
from db.model.activity_excluded_channel import ActivityExcludedChannel
from db.helpers import activity_module_settings_helper


@pytest.fixture(autouse=True)
def _clear_cache():
    activity_module_settings_helper.__CACHE.clear()
    yield
    activity_module_settings_helper.__CACHE.clear()


def test_excluded_channels_empty_by_default():
    settings = activity_module_settings_helper.get_settings(guild_id=1)
    assert settings.excluded_channels == set()


def test_add_excluded_channel_then_read_returns_id():
    settings = activity_module_settings_helper.get_settings(guild_id=1)
    settings.add_excluded_channel(42)
    assert 42 in settings.excluded_channels
    # Persisted to the junction table, not just the in-memory set.
    assert DB.s.first(ActivityExcludedChannel, guild_id=1, channel_id=42) is not None


def test_add_excluded_channel_survives_cache_reload():
    settings = activity_module_settings_helper.get_settings(guild_id=1)
    settings.add_excluded_channel(42)
    # Drop the cache so the next get_settings rebuilds the wrapper from the table.
    activity_module_settings_helper.__CACHE.clear()
    reloaded = activity_module_settings_helper.get_settings(guild_id=1)
    assert reloaded.excluded_channels == {42}


def test_remove_excluded_channel_deletes_it():
    settings = activity_module_settings_helper.get_settings(guild_id=1)
    settings.add_excluded_channel(42)
    settings.remove_excluded_channel(42)
    assert 42 not in settings.excluded_channels
    assert DB.s.first(ActivityExcludedChannel, guild_id=1, channel_id=42) is None


def test_add_excluded_channel_is_idempotent():
    settings = activity_module_settings_helper.get_settings(guild_id=1)
    settings.add_excluded_channel(42)
    settings.add_excluded_channel(42)
    assert settings.excluded_channels == {42}
    rows = DB.s.all(ActivityExcludedChannel, guild_id=1, channel_id=42)
    assert len(rows) == 1


def test_remove_excluded_channel_missing_is_noop():
    settings = activity_module_settings_helper.get_settings(guild_id=1)
    # Removing a channel that was never excluded must not raise.
    settings.remove_excluded_channel(99)
    assert settings.excluded_channels == set()


def test_two_guilds_stay_isolated():
    g1 = activity_module_settings_helper.get_settings(guild_id=1)
    g2 = activity_module_settings_helper.get_settings(guild_id=2)
    g1.add_excluded_channel(42)
    assert g1.excluded_channels == {42}
    assert g2.excluded_channels == set()
    assert DB.s.first(ActivityExcludedChannel, guild_id=2, channel_id=42) is None
