"""CRUD + in-memory per-guild config cache for starboards, plus the canonical
emoji-matching function reused by the reaction handler and the command cog.

The cache mirrors ``db/helpers/activity_module_settings_helper.py``: a module-level
dict keyed by ``guild_id``, populated on the read hot path
(``get_enabled_configs``) and dropped for a guild on every mutation so the next
read reflects the change.
"""
from typing import Dict, List, Optional

import sqlalchemy as sa

from db import DB
from db.model.starboard_config import StarboardConfig
from db.model.starboard_entry import StarboardEntry


# guild_id -> list of that guild's enabled StarboardConfig rows.
__CACHE: Dict[int, List[StarboardConfig]] = {}


def _invalidate(guild_id: int):
    __CACHE.pop(guild_id, None)


# --- Config CRUD ------------------------------------------------------------


def get_enabled_configs(guild_id: int) -> List[StarboardConfig]:
    """Cached hot path for the reaction handler: only enabled configs.

    Returns a shallow copy so a caller mutating the list in place cannot corrupt
    the cached entry.
    """
    if guild_id not in __CACHE:
        __CACHE[guild_id] = DB.s.all(StarboardConfig, guild_id=guild_id, enabled=True)
    return list(__CACHE[guild_id])


def get_configs(guild_id: int) -> List[StarboardConfig]:
    """All configs for a guild, enabled or not (for /starboard list)."""
    return DB.s.all(StarboardConfig, guild_id=guild_id)


def get_config(config_id: int) -> Optional[StarboardConfig]:
    return DB.s.first(StarboardConfig, id=config_id)


def find_duplicate_config(guild_id: int, target_channel_id: int, emoji: str,
                          emoji_id: Optional[int],
                          exclude_id: Optional[int] = None) -> Optional[StarboardConfig]:
    """Return an existing config for this guild with the same target channel AND
    emoji, or ``None``. Custom emoji are matched by id, unicode by string (the same
    rule as ``emoji_matches``). ``exclude_id`` skips the row being edited so an edit
    does not collide with itself. Used to keep two boards from sharing a
    channel+emoji, which would be redundant and confusing to manage."""
    for config in get_configs(guild_id):
        if exclude_id is not None and config.id == exclude_id:
            continue
        if config.target_channel_id != target_channel_id:
            continue
        if emoji_id is not None:
            if config.emoji_id == emoji_id:
                return config
        elif config.emoji_id is None and config.emoji == emoji:
            return config
    return None


def add_config(guild_id: int, target_channel_id: int, emoji: str,
               emoji_id: Optional[int] = None, threshold: int = 5,
               enabled: bool = True, name: Optional[str] = None) -> StarboardConfig:
    config = StarboardConfig(
        guild_id=guild_id,
        target_channel_id=target_channel_id,
        emoji=emoji,
        emoji_id=emoji_id,
        threshold=threshold,
        enabled=enabled,
        name=name,
    )
    DB.s.add(config)
    DB.s.commit()
    _invalidate(guild_id)
    return config


def update_config(config_id: int, **kw) -> Optional[StarboardConfig]:
    config = DB.s.first(StarboardConfig, id=config_id)
    if config is None:
        return None
    old_guild_id = config.guild_id
    for key, value in kw.items():
        setattr(config, key, value)
    DB.s.commit()
    # Invalidate both guilds in case the update moved the config between guilds,
    # so neither guild's cache is left stale.
    _invalidate(old_guild_id)
    _invalidate(config.guild_id)
    return config


def remove_config(config_id: int) -> bool:
    config = DB.s.first(StarboardConfig, id=config_id)
    if config is None:
        return False
    guild_id = config.guild_id
    DB.s.delete(config)
    DB.s.commit()
    _invalidate(guild_id)
    return True


# --- Entry (per-board, per-message) -----------------------------------------


def get_entry(config_id: int, message_id: int) -> Optional[StarboardEntry]:
    return DB.s.first(StarboardEntry, starboard_config_id=config_id,
                      original_message_id=message_id)


def upsert_entry(config_id: int, guild_id: int, original_message_id: int,
                 original_channel_id: int, author_id: int,
                 posted_message_id: Optional[int] = None,
                 star_count: Optional[int] = None) -> Optional[StarboardEntry]:
    """Insert a new entry, or update the existing one for ``(config, message)``.

    Idempotent on ``(starboard_config_id, original_message_id)``. ``posted_message_id``
    and ``star_count`` are only overwritten on an update when explicitly provided
    (not ``None``), so a partial upsert never silently clears a field. A racing
    duplicate insert hits the unique constraint; we catch ``IntegrityError``,
    roll back, and fall back to updating the row the other writer created.
    """
    def _apply_update(row: StarboardEntry) -> StarboardEntry:
        row.original_channel_id = original_channel_id
        row.author_id = author_id
        if posted_message_id is not None:
            row.posted_message_id = posted_message_id
        if star_count is not None:
            row.star_count = star_count
        DB.s.commit()
        return row

    entry = get_entry(config_id, original_message_id)
    if entry is not None:
        return _apply_update(entry)

    entry = StarboardEntry(
        starboard_config_id=config_id,
        guild_id=guild_id,
        original_message_id=original_message_id,
        original_channel_id=original_channel_id,
        author_id=author_id,
        posted_message_id=posted_message_id,
        star_count=star_count if star_count is not None else 0,
    )
    try:
        DB.s.add(entry)
        DB.s.commit()
        return entry
    except sa.exc.IntegrityError:
        # Another writer inserted the same (config, message) first. Recover by
        # updating their row instead of leaving the caller with a dead session.
        DB.s.rollback()
        existing = get_entry(config_id, original_message_id)
        if existing is not None:
            return _apply_update(existing)
        return existing


def delete_entries_for_message(guild_id: int, message_id: int) -> List[StarboardEntry]:
    """Delete every entry for ``message_id`` across all of the guild's boards and
    return the removed rows so the delete handler knows which posts to remove."""
    entries = DB.s.all(StarboardEntry, guild_id=guild_id,
                       original_message_id=message_id)
    for entry in entries:
        DB.s.delete(entry)
    DB.s.commit()
    return entries


# --- Matching ---------------------------------------------------------------


def emoji_matches(payload_emoji, config: StarboardConfig) -> bool:
    """Canonical emoji matcher, reused by the handler and the cog.

    Custom emoji are matched by id (names can be reused across emoji), unicode
    emoji are matched by their string form. ``payload_emoji`` is shaped like a
    ``nextcord.PartialEmoji`` (``.id`` is ``None`` for unicode emoji).
    """
    if config.emoji_id is not None:
        return getattr(payload_emoji, 'id', None) == config.emoji_id
    return str(payload_emoji) == config.emoji
