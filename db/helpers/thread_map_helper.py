"""Storage for ``/thread-map`` messages, written in the unit-of-work style.

Every function takes the caller's ``session`` first and never commits: the
entry point owns the transaction. Results are plain values, never ORM objects,
so they stay usable after the session closes (``expire_on_commit`` is on).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.model.thread_map import ThreadMap, ThreadMapExcludedChannel, ThreadMapPage, ThreadMapRole

ROLE_CAP = 25


@dataclass(frozen=True)
class MapConfig:
    id: int
    guild_id: int
    channel_id: int
    message_id: int
    role_ids: frozenset[int]
    excluded_ids: frozenset[int]
    current_page: int
    page_count: int
    last_refreshed_at: datetime  # always tz-aware UTC


def _as_utc(dt: datetime) -> datetime:
    # sqlite drops tzinfo on DateTime(timezone=True); stored values are UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dedupe(ids: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(ids))


def _to_config(thread_map: ThreadMap) -> MapConfig:
    return MapConfig(
        id=thread_map.id,
        guild_id=thread_map.guild_id,
        channel_id=thread_map.channel_id,
        message_id=thread_map.message_id,
        role_ids=frozenset(r.role_id for r in thread_map.roles),
        excluded_ids=frozenset(e.channel_id for e in thread_map.excluded),
        current_page=thread_map.current_page,
        page_count=len(thread_map.pages),
        last_refreshed_at=_as_utc(thread_map.last_refreshed_at),
    )


def _by_message(session: Session, message_id: int) -> Optional[ThreadMap]:
    return session.scalars(select(ThreadMap).where(ThreadMap.message_id == message_id)).first()


def create_map(session: Session, *, guild_id: int, channel_id: int, message_id: int, created_by: int,
               role_ids: Iterable[int], pages: list[str], refreshed_at: datetime) -> int:
    thread_map = ThreadMap(
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        created_by=created_by,
        current_page=0,
        last_refreshed_at=refreshed_at,
        roles=[ThreadMapRole(role_id=r) for r in _dedupe(role_ids)],
        pages=[ThreadMapPage(page_index=i, content=c) for i, c in enumerate(pages)],
    )
    session.add(thread_map)
    session.flush()
    return thread_map.id


def get_config(session: Session, map_id: int) -> Optional[MapConfig]:
    thread_map = session.get(ThreadMap, map_id)
    return _to_config(thread_map) if thread_map else None


def get_config_by_message(session: Session, message_id: int) -> Optional[MapConfig]:
    thread_map = _by_message(session, message_id)
    return _to_config(thread_map) if thread_map else None


def get_page(session: Session, map_id: int, page_index: int) -> Optional[str]:
    page = session.get(ThreadMapPage, (map_id, page_index))
    return page.content if page else None


def set_current_page(session: Session, map_id: int, page_index: int) -> None:
    thread_map = session.get(ThreadMap, map_id)
    if thread_map:
        thread_map.current_page = page_index


def replace_pages(session: Session, map_id: int, pages: list[str], refreshed_at: datetime) -> None:
    """Swap in a fresh snapshot and reset the view to its first page."""
    thread_map = session.get(ThreadMap, map_id)
    if not thread_map:
        return
    thread_map.pages = [ThreadMapPage(page_index=i, content=c) for i, c in enumerate(pages)]
    thread_map.current_page = 0
    thread_map.last_refreshed_at = refreshed_at
    session.flush()


def add_roles(session: Session, map_id: int, role_ids: Iterable[int]) -> tuple[list[int], list[int]]:
    """Add audience roles up to ``ROLE_CAP``, skipping ones already present.
    Returns ``(added, rejected_by_cap)``."""
    thread_map = session.get(ThreadMap, map_id)
    if not thread_map:
        return [], []
    existing = {r.role_id for r in thread_map.roles}
    added, rejected = [], []
    for role_id in _dedupe(role_ids):
        if role_id in existing:
            continue
        if len(existing) >= ROLE_CAP:
            rejected.append(role_id)
            continue
        thread_map.roles.append(ThreadMapRole(role_id=role_id))
        existing.add(role_id)
        added.append(role_id)
    session.flush()
    return added, rejected


def remove_roles(session: Session, map_id: int, role_ids: Iterable[int]) -> None:
    thread_map = session.get(ThreadMap, map_id)
    if not thread_map:
        return
    drop = set(role_ids)
    thread_map.roles = [r for r in thread_map.roles if r.role_id not in drop]
    session.flush()


def add_exclusions(session: Session, map_id: int, channel_ids: Iterable[int]) -> list[int]:
    """Exclude channels, skipping ones already excluded. Returns the newly added ids."""
    thread_map = session.get(ThreadMap, map_id)
    if not thread_map:
        return []
    existing = {e.channel_id for e in thread_map.excluded}
    added = [c for c in _dedupe(channel_ids) if c not in existing]
    thread_map.excluded.extend(ThreadMapExcludedChannel(channel_id=c) for c in added)
    session.flush()
    return added


def remove_exclusions(session: Session, map_id: int, channel_ids: Iterable[int]) -> list[int]:
    """Re-include channels. Returns the ids that were actually excluded before."""
    thread_map = session.get(ThreadMap, map_id)
    if not thread_map:
        return []
    drop = set(channel_ids)
    removed = [e.channel_id for e in thread_map.excluded if e.channel_id in drop]
    thread_map.excluded = [e for e in thread_map.excluded if e.channel_id not in drop]
    session.flush()
    return removed


def delete_map(session: Session, map_id: int) -> bool:
    # session.delete, not a bulk delete, so the ORM cascades reach the child tables.
    thread_map = session.get(ThreadMap, map_id)
    if not thread_map:
        return False
    session.delete(thread_map)
    session.flush()
    return True


def delete_by_message(session: Session, message_id: int) -> Optional[int]:
    """Delete the map posted as ``message_id``; returns its id so callers can
    clear per-map state such as the refresh cooldown."""
    thread_map = _by_message(session, message_id)
    if not thread_map:
        return None
    map_id = thread_map.id
    session.delete(thread_map)
    session.flush()
    return map_id
