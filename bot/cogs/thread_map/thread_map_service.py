"""Rebuilding a thread map from live guild data.

The only module in the cog that mixes DB sessions and HTTP. Sessions are kept
short and never held across an ``await``: read, close, fetch, then a fresh
session to write.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import nextcord

from bot.cogs.thread_map.thread_map_render import build_embed, build_pages, collect_blocks, fetch_active_threads
from db import DB
from db.helpers import thread_map_helper


@dataclass(frozen=True)
class Rendered:
    pages: list[str]
    channel_count: int
    thread_count: int


@dataclass(frozen=True)
class Rebuilt:
    embed: nextcord.Embed
    page_count: int
    channel_count: int
    thread_count: int


def render_with_threads(guild, threads, role_ids: Iterable[int], excluded_ids: Iterable[int]) -> Rendered:
    roles = [role for role in map(guild.get_role, role_ids) if role is not None]  # deleted roles skipped
    blocks = collect_blocks(guild, threads, roles, set(excluded_ids))
    return Rendered(
        pages=build_pages(blocks),
        channel_count=len(blocks),
        thread_count=sum(block.count('\n') for block in blocks),  # one line per thread under each header
    )


async def render_pages(guild, role_ids: Iterable[int], excluded_ids: Iterable[int]) -> Rendered:
    threads = await fetch_active_threads(guild)
    return render_with_threads(guild, threads, role_ids, excluded_ids)


async def rebuild(guild, map_id: int) -> Optional[Rebuilt]:
    """Re-render a stored map from live threads and replace its page snapshot.
    Returns ``None`` when the map row no longer exists."""
    with DB.Session() as session:
        if thread_map_helper.get_config(session, map_id) is None:
            return None

    threads = await fetch_active_threads(guild)

    with DB.Session() as session:
        # Re-read after the fetch so an edit committed meanwhile isn't
        # overwritten with pages built from the older config.
        config = thread_map_helper.get_config(session, map_id)
        if config is None:
            return None
        gone_channels = [c for c in config.excluded_ids if guild.get_channel(c) is None]
        gone_roles = [r for r in config.role_ids if guild.get_role(r) is None]
        if gone_channels or gone_roles:
            thread_map_helper.remove_exclusions(session, map_id, gone_channels)
            thread_map_helper.remove_roles(session, map_id, gone_roles)
            config = thread_map_helper.get_config(session, map_id)

        rendered = render_with_threads(guild, threads, config.role_ids, config.excluded_ids)
        now = datetime.now(timezone.utc)
        thread_map_helper.replace_pages(session, map_id, rendered.pages, now)
        session.commit()

    page_count = len(rendered.pages)
    return Rebuilt(
        embed=build_embed(rendered.pages[0], 0, page_count, now),
        page_count=page_count,
        channel_count=rendered.channel_count,
        thread_count=rendered.thread_count,
    )
