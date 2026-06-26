"""CRUD for the per-guild registry of channels eligible to receive anniversary
posts. Follows ``db/helpers/starboard_helper.py``'s CRUD shape minus the cache:
the registry is read only on slash interactions and once/day by the loop, so a
cache would add invalidation complexity for no hot path.
"""
from typing import List, Optional

import sqlalchemy as sa

from db import DB
from db.model.anniversary_channel import AnniversaryChannel


def add_channel(guild_id: int, channel_id: int, label: str) -> Optional[AnniversaryChannel]:
    """Register a channel, or return ``None`` if ``(guild, channel)`` is already
    registered (unique guard caught + rolled back)."""
    channel = AnniversaryChannel(guild_id=guild_id, channel_id=channel_id, label=label)
    try:
        DB.s.add(channel)
        DB.s.commit()
        return channel
    except sa.exc.IntegrityError:
        DB.s.rollback()
        return None


def get_channels(guild_id: int) -> List[AnniversaryChannel]:
    return DB.s.all(AnniversaryChannel, guild_id=guild_id)


def get_channel(id: int) -> Optional[AnniversaryChannel]:
    return DB.s.first(AnniversaryChannel, id=id)


def find_by_channel_id(guild_id: int, channel_id: int) -> Optional[AnniversaryChannel]:
    return DB.s.first(AnniversaryChannel, guild_id=guild_id, channel_id=channel_id)


def remove_channel(id: int) -> bool:
    channel = DB.s.first(AnniversaryChannel, id=id)
    if channel is None:
        return False
    DB.s.delete(channel)
    DB.s.commit()
    return True
