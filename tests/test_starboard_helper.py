"""Tests for db/helpers/starboard_helper.py — config CRUD, the per-guild cache,
entry upsert/delete, and the canonical ``emoji_matches`` function.

The config cache is a module-level dict that survives the per-test DB rollback,
so the autouse fixture below clears it between tests to prevent leakage.
"""
import pytest
import sqlalchemy as sa

from db import DB
from db.model.starboard_config import StarboardConfig
from db.model.starboard_entry import StarboardEntry
from db.helpers import starboard_helper


@pytest.fixture(autouse=True)
def _clear_cache():
    starboard_helper.__CACHE.clear()
    yield
    starboard_helper.__CACHE.clear()


# --- Config persistence + fan-out -------------------------------------------


def test_add_and_read_config():
    cfg = starboard_helper.add_config(
        guild_id=1, target_channel_id=10, emoji='⭐', threshold=5, name='Star')
    assert cfg.id is not None
    got = starboard_helper.get_config(cfg.id)
    assert got is not None
    assert got.emoji == '⭐'
    assert got.target_channel_id == 10
    assert got.threshold == 5
    assert got.name == 'Star'
    assert got.enabled is True


def test_multiple_configs_per_guild():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    starboard_helper.add_config(guild_id=1, target_channel_id=11, emoji='🔥')
    configs = starboard_helper.get_configs(1)
    assert len(configs) == 2
    assert {c.emoji for c in configs} == {'⭐', '🔥'}


def test_emoji_fan_out_same_emoji_multiple_boards():
    # No (guild_id, emoji) uniqueness — one emoji may feed several boards.
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    starboard_helper.add_config(guild_id=1, target_channel_id=11, emoji='⭐')
    configs = starboard_helper.get_configs(1)
    assert len([c for c in configs if c.emoji == '⭐']) == 2
    assert {c.target_channel_id for c in configs} == {10, 11}


def test_get_config_missing_returns_none():
    assert starboard_helper.get_config(99999) is None


# --- find_duplicate_config (channel + emoji uniqueness) ---------------------


def test_find_duplicate_config_matches_same_channel_and_unicode_emoji():
    existing = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    dup = starboard_helper.find_duplicate_config(1, 10, '⭐', None)
    assert dup is not None and dup.id == existing.id


def test_find_duplicate_config_none_when_channel_differs():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    assert starboard_helper.find_duplicate_config(1, 11, '⭐', None) is None


def test_find_duplicate_config_none_when_emoji_differs():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    assert starboard_helper.find_duplicate_config(1, 10, '🔥', None) is None


def test_find_duplicate_config_matches_custom_emoji_by_id_not_name():
    existing = starboard_helper.add_config(
        guild_id=1, target_channel_id=10, emoji='book', emoji_id=123)
    # Same id, different stored name still collides; a unicode lookup does not.
    dup = starboard_helper.find_duplicate_config(1, 10, 'renamed', 123)
    assert dup is not None and dup.id == existing.id
    assert starboard_helper.find_duplicate_config(1, 10, 'book', None) is None


def test_find_duplicate_config_excludes_self():
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    # The row being edited is skipped so it doesn't collide with itself.
    assert starboard_helper.find_duplicate_config(
        1, 10, '⭐', None, exclude_id=cfg.id) is None


def test_find_duplicate_config_scoped_to_guild():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    # Same channel+emoji in another guild is not a duplicate.
    assert starboard_helper.find_duplicate_config(2, 10, '⭐', None) is None


# --- emoji_matches (canonical matcher) --------------------------------------


def test_emoji_matches_unicode(emoji_factory):
    cfg = StarboardConfig(emoji='⭐', emoji_id=None)
    assert starboard_helper.emoji_matches(emoji_factory('⭐'), cfg) is True
    assert starboard_helper.emoji_matches(emoji_factory('🔥'), cfg) is False


def test_emoji_matches_custom_by_id_even_when_names_differ(emoji_factory):
    cfg = StarboardConfig(emoji='partyblob', emoji_id=12345)
    # Same id, different name → still a match (names can be reused).
    assert starboard_helper.emoji_matches(
        emoji_factory('renamed_party', id=12345), cfg) is True
    # Same name, different id → not a match.
    assert starboard_helper.emoji_matches(
        emoji_factory('partyblob', id=99999), cfg) is False


def test_emoji_matches_custom_vs_unicode_mismatch(emoji_factory):
    # Config wants a custom emoji; a unicode reaction (id is None) must not match.
    custom_cfg = StarboardConfig(emoji='blob', emoji_id=12345)
    assert starboard_helper.emoji_matches(emoji_factory('⭐'), custom_cfg) is False
    # Config wants a unicode emoji; a custom reaction must not match.
    unicode_cfg = StarboardConfig(emoji='⭐', emoji_id=None)
    assert starboard_helper.emoji_matches(
        emoji_factory('blob', id=12345), unicode_cfg) is False


# --- get_enabled_configs + cache --------------------------------------------


def test_get_enabled_configs_returns_only_enabled():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐', enabled=True)
    starboard_helper.add_config(guild_id=1, target_channel_id=11, emoji='🔥', enabled=False)
    enabled = starboard_helper.get_enabled_configs(1)
    assert len(enabled) == 1
    assert enabled[0].emoji == '⭐'
    assert all(c.enabled for c in enabled)


def test_get_enabled_configs_is_cached():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    first = starboard_helper.get_enabled_configs(1)  # populates cache
    assert len(first) == 1
    # Insert a row WITHOUT going through a mutator, so the cache is NOT invalidated.
    DB.s.add(StarboardConfig(guild_id=1, target_channel_id=11, emoji='🔥',
                             threshold=5, enabled=True))
    DB.s.commit()
    # The cached result is returned unchanged — proves the read hit the cache.
    assert len(starboard_helper.get_enabled_configs(1)) == 1


def test_mutator_invalidates_cache():
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    assert len(starboard_helper.get_enabled_configs(1)) == 1  # cache populated
    # A mutator must drop the guild's cache so the next read reflects the change.
    starboard_helper.update_config(cfg.id, enabled=False)
    assert starboard_helper.get_enabled_configs(1) == []


def test_add_config_invalidates_cache():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    assert len(starboard_helper.get_enabled_configs(1)) == 1  # cache populated
    # Adding another config must drop the cache so the next read reflects it.
    starboard_helper.add_config(guild_id=1, target_channel_id=11, emoji='🔥')
    assert len(starboard_helper.get_enabled_configs(1)) == 2


def test_get_enabled_configs_returns_a_copy():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    configs = starboard_helper.get_enabled_configs(1)
    configs.clear()  # mutating the returned list must not corrupt the cache
    assert len(starboard_helper.get_enabled_configs(1)) == 1


def test_remove_config_invalidates_cache():
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    assert len(starboard_helper.get_enabled_configs(1)) == 1
    assert starboard_helper.remove_config(cfg.id) is True
    assert starboard_helper.get_enabled_configs(1) == []
    assert starboard_helper.get_config(cfg.id) is None


def test_update_config_missing_returns_none():
    assert starboard_helper.update_config(99999, enabled=False) is None


def test_remove_config_missing_returns_false():
    assert starboard_helper.remove_config(99999) is False


# --- Entry upsert / delete --------------------------------------------------


def _add_cfg(guild_id=1, target_channel_id=10, emoji='⭐'):
    return starboard_helper.add_config(
        guild_id=guild_id, target_channel_id=target_channel_id, emoji=emoji)


def test_upsert_entry_inserts_then_is_idempotent():
    cfg = _add_cfg()
    e1 = starboard_helper.upsert_entry(
        config_id=cfg.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=900, star_count=5)
    assert e1.id is not None
    # Second upsert on the same (config, message) updates in place, no new row.
    e2 = starboard_helper.upsert_entry(
        config_id=cfg.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=900, star_count=8)
    assert e2.id == e1.id
    assert e2.star_count == 8
    rows = DB.s.all(StarboardEntry, starboard_config_id=cfg.id, original_message_id=500)
    assert len(rows) == 1


def test_upsert_entry_partial_update_preserves_star_count():
    cfg = _add_cfg()
    starboard_helper.upsert_entry(
        config_id=cfg.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=900, star_count=7)
    # A later upsert that omits star_count must NOT reset it to 0.
    updated = starboard_helper.upsert_entry(
        config_id=cfg.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=901)
    assert updated.posted_message_id == 901
    assert updated.star_count == 7


def test_get_entry_returns_matching_row_or_none():
    cfg = _add_cfg()
    assert starboard_helper.get_entry(cfg.id, 500) is None
    starboard_helper.upsert_entry(
        config_id=cfg.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=900, star_count=5)
    got = starboard_helper.get_entry(cfg.id, 500)
    assert got is not None
    assert got.posted_message_id == 900


def test_duplicate_entry_insert_raises_integrity_error():
    # The (starboard_config_id, original_message_id) unique constraint is enforced;
    # a duplicate raw insert raises IntegrityError and rollback recovers the session.
    cfg = _add_cfg()
    DB.s.add(StarboardEntry(starboard_config_id=cfg.id, guild_id=1,
                            original_message_id=500, original_channel_id=20,
                            author_id=42, star_count=0))
    DB.s.commit()
    DB.s.add(StarboardEntry(starboard_config_id=cfg.id, guild_id=1,
                            original_message_id=500, original_channel_id=20,
                            author_id=42, star_count=0))
    with pytest.raises(sa.exc.IntegrityError):
        DB.s.commit()
    DB.s.rollback()
    rows = DB.s.all(StarboardEntry, starboard_config_id=cfg.id, original_message_id=500)
    assert len(rows) == 1


def test_upsert_entry_recovers_from_racing_insert(monkeypatch):
    # Exercise upsert_entry's internal IntegrityError + rollback branch: force the
    # pre-insert lookup to miss so it attempts an insert that collides with a row
    # another writer already committed.
    cfg = _add_cfg()
    DB.s.add(StarboardEntry(starboard_config_id=cfg.id, guild_id=1,
                            original_message_id=500, original_channel_id=20,
                            author_id=42, star_count=0))
    DB.s.commit()
    monkeypatch.setattr(starboard_helper, 'get_entry', lambda config_id, message_id: None)
    # The collision is caught, the session rolled back; no duplicate is created.
    result = starboard_helper.upsert_entry(
        config_id=cfg.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=900, star_count=5)
    assert result is None  # patched get_entry returns None in the recovery path
    rows = DB.s.all(StarboardEntry, starboard_config_id=cfg.id, original_message_id=500)
    assert len(rows) == 1  # session is still usable and no duplicate persisted


def test_delete_entries_for_message_across_boards():
    # Fan-out: one message fed two boards; deleting it returns both entries and
    # leaves unrelated entries intact.
    cfg1 = _add_cfg(target_channel_id=10)
    cfg2 = _add_cfg(target_channel_id=11)
    starboard_helper.upsert_entry(
        config_id=cfg1.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=901, star_count=5)
    starboard_helper.upsert_entry(
        config_id=cfg2.id, guild_id=1, original_message_id=500,
        original_channel_id=20, author_id=42, posted_message_id=902, star_count=5)
    # Unrelated entry for a different message on board 1.
    starboard_helper.upsert_entry(
        config_id=cfg1.id, guild_id=1, original_message_id=777,
        original_channel_id=20, author_id=42, posted_message_id=903, star_count=5)

    removed = starboard_helper.delete_entries_for_message(1, 500)
    assert len(removed) == 2
    assert {r.starboard_config_id for r in removed} == {cfg1.id, cfg2.id}
    # The deleted entries are gone; the unrelated one survives.
    assert starboard_helper.get_entry(cfg1.id, 500) is None
    assert starboard_helper.get_entry(cfg2.id, 500) is None
    assert starboard_helper.get_entry(cfg1.id, 777) is not None


def test_delete_entries_for_message_no_entries_is_noop():
    removed = starboard_helper.delete_entries_for_message(1, 12345)
    assert removed == []
