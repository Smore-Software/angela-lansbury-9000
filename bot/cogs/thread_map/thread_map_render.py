"""Pure rendering for ``/thread-map``: which channels and threads a map lists,
how they are laid out, and how the result is split into embed pages.

No DB and no cog state here, so everything except ``fetch_active_threads`` is
synchronous and unit-testable against fakes.
"""
import re
from datetime import datetime

import nextcord

# Discord rejects embed descriptions over 4096 chars and nextcord doesn't
# check, so the packer is the only guard. The margin absorbs off-by-a-few slop.
DESCRIPTION_BUDGET = 3900
SCOPED_TYPES = (nextcord.TextChannel, nextcord.ForumChannel)  # TextChannel covers announcement

EMPTY_MAP_TEXT = 'No active threads match this map.'
CONTINUED_SUFFIX = ' (continued)'

# One pass, every markdown-significant char (backslash included) escaped exactly
# once. Layering on escape_markdown would double backslashes and let a crafted
# `\]` close the label. `<`/`#` neutralise channel mentions, `:` URL detection.
_LABEL_SPECIALS = re.compile(r'([\\*_~`|>\[\]()<#:])')


def channel_in_scope(channel, roles, default_role, excluded_ids) -> bool:
    """Exclusion beats visibility; otherwise shown if @everyone or any audience role can view it."""
    if channel.id in excluded_ids:
        return False
    if channel.permissions_for(default_role).view_channel:
        return True
    return any(channel.permissions_for(r).view_channel for r in roles)


def thread_listed(thread) -> bool:
    # The thread cache never evicts archived threads, so check explicitly.
    return not thread.archived and not thread.is_private()


def is_admin_role(role) -> bool:
    # permissions_for returns Permissions.all() for these, so they see every channel.
    return role.permissions.administrator


def escape_label(name: str) -> str:
    return _LABEL_SPECIALS.sub(r'\\\1', name)


def _channel_header(channel) -> str:
    return f'**[#{escape_label(channel.name)}]({channel.jump_url})**'


def collect_blocks(guild, threads, roles, excluded_ids) -> list[str]:
    """One block per in-scope channel with listed threads, in Discord sidebar order.

    Threads are grouped by ``parent_id`` under channels already judged in scope;
    ``thread.permissions_for`` is never called because it raises for threads
    whose parent isn't cached.
    """
    by_parent: dict[int, list] = {}
    for thread in threads:
        if thread_listed(thread):
            by_parent.setdefault(thread.parent_id, []).append(thread)

    blocks = []
    for _, channels in guild.by_category():
        for channel in channels:
            if not isinstance(channel, SCOPED_TYPES):
                continue
            listed = by_parent.get(channel.id)
            if not listed or not channel_in_scope(channel, roles, guild.default_role, excluded_ids):
                continue
            lines = [_channel_header(channel)]
            lines += [f'- [{escape_label(t.name)}]({t.jump_url})'
                      for t in sorted(listed, key=lambda t: t.id, reverse=True)]
            blocks.append('\n'.join(lines))
    return blocks


def _split_block(block: str, budget: int) -> list[str]:
    header, *entries = block.split('\n')
    chunks, current, has_entries = [], header, False
    for entry in entries:
        if has_entries and len(current) + 1 + len(entry) > budget:
            chunks.append(current)
            current, has_entries = header + CONTINUED_SUFFIX, False
        current += '\n' + entry
        has_entries = True
    chunks.append(current)
    return chunks


def build_pages(blocks: list[str], budget: int = DESCRIPTION_BUDGET) -> list[str]:
    """Greedily pack blocks into pages, breaking only between blocks. A block
    too long for one page is split by lines, repeating its header."""
    pieces = []
    for block in blocks:
        pieces += _split_block(block, budget) if len(block) > budget else [block]

    pages, current = [], ''
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 2 + len(piece) <= budget:
            current += '\n\n' + piece
        else:
            pages.append(current)
            current = piece
    pages.append(current)
    return pages


def build_embed(page: str, page_index: int, page_count: int, refreshed_at: datetime) -> nextcord.Embed:
    # timestamp renders after the footer in each viewer's own timezone.
    embed = nextcord.Embed(title='Thread map',
                           description=page or EMPTY_MAP_TEXT,
                           color=nextcord.Color.dark_blue(),
                           timestamp=refreshed_at)
    embed.set_footer(text=f'Page {page_index + 1}/{page_count} · Last refreshed')
    return embed


async def fetch_active_threads(guild) -> list:
    try:
        return await guild.active_threads()
    except nextcord.HTTPException:
        # Cache fallback may hold stale archived threads; collect_blocks filters them.
        return guild.threads
