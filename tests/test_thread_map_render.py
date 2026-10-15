import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nextcord
import pytest

from bot.cogs.thread_map import thread_map_render as r
from tests.conftest import (FAKE_GUILD_ID, FakeForumChannel, FakeVoiceChannel, make_channel, make_guild,
                            make_role, make_thread)

EVERYONE = make_role(FAKE_GUILD_ID)
MEMBERS = make_role(2000)


# --- channel_in_scope --------------------------------------------------------

def test_public_channel_in_scope():
    assert r.channel_in_scope(make_channel(1), [], EVERYONE, set())


def test_hidden_from_everyone_visible_to_configured_role():
    channel = make_channel(1, visible_to={MEMBERS.id})
    assert r.channel_in_scope(channel, [MEMBERS], EVERYONE, set())
    assert not r.channel_in_scope(channel, [], EVERYONE, set())


def test_hidden_from_all_out_of_scope():
    channel = make_channel(1, visible_to=set())
    assert not r.channel_in_scope(channel, [MEMBERS], EVERYONE, set())


def test_exclusion_beats_visibility():
    assert not r.channel_in_scope(make_channel(1), [MEMBERS], EVERYONE, {1})


def test_is_admin_role():
    assert r.is_admin_role(make_role(5, administrator=True))
    assert not r.is_admin_role(make_role(5))


# --- thread_listed -----------------------------------------------------------

def test_thread_listed_drops_archived_and_private():
    assert r.thread_listed(make_thread(1, 10))
    assert not r.thread_listed(make_thread(1, 10, archived=True))
    assert not r.thread_listed(make_thread(1, 10, private=True))


# --- collect_blocks ----------------------------------------------------------

def _entry_urls(block):
    return re.findall(r'\]\((https://[^)]*)\)', block)


def test_collect_blocks_follows_by_category_order():
    a, b, c = make_channel(1, 'a'), make_channel(2, 'b'), make_channel(3, 'c')
    guild = make_guild([(None, [c]), (SimpleNamespace(id=50), [a, b])])
    threads = [make_thread(11, 1), make_thread(12, 2), make_thread(13, 3)]

    blocks = r.collect_blocks(guild, threads, [], set())
    assert [b.split('\n')[0] for b in blocks] == [
        f'**[#c]({c.jump_url})**', f'**[#a]({a.jump_url})**', f'**[#b]({b.jump_url})**',
    ]


def test_collect_blocks_omits_empty_and_filtered_channels():
    busy, empty, archived_only = make_channel(1, 'busy'), make_channel(2, 'empty'), make_channel(3, 'old')
    guild = make_guild([(None, [busy, empty, archived_only])])
    threads = [make_thread(11, 1), make_thread(12, 3, archived=True), make_thread(13, 3, private=True)]

    blocks = r.collect_blocks(guild, threads, [], set())
    assert len(blocks) == 1
    assert blocks[0].startswith('**[#busy]')


def test_collect_blocks_scope_and_types():
    forum = make_channel(1, 'forum', cls=FakeForumChannel)
    voice = make_channel(2, 'voice', cls=FakeVoiceChannel)
    hidden = make_channel(3, 'staff', visible_to=set())
    excluded = make_channel(4, 'excluded')
    guild = make_guild([(None, [forum, voice, hidden, excluded])])
    threads = [make_thread(10 + i, i) for i in range(1, 5)]

    blocks = r.collect_blocks(guild, threads, [], {4})
    assert [b.split('\n')[0] for b in blocks] == [f'**[#forum]({forum.jump_url})**']


def test_collect_blocks_threads_newest_first():
    channel = make_channel(1)
    guild = make_guild([(None, [channel])])
    threads = [make_thread(5, 1, 'mid'), make_thread(9, 1, 'new'), make_thread(2, 1, 'old')]

    lines = r.collect_blocks(guild, threads, [], set())[0].split('\n')[1:]
    assert [l.split(']')[0] for l in lines] == ['- [new', '- [mid', '- [old']


def test_collect_blocks_never_calls_thread_permissions_for():
    channel = make_channel(1)
    thread = make_thread(11, 1)
    thread.permissions_for = lambda *_: pytest.fail('thread.permissions_for must not be called')
    orphan = make_thread(12, 999)  # parent not cached (media post / deleted parent)
    orphan.permissions_for = thread.permissions_for

    blocks = r.collect_blocks(make_guild([(None, [channel])]), [thread, orphan], [], set())
    assert len(blocks) == 1 and _entry_urls(blocks[0])[1:] == [thread.jump_url]


def test_collect_blocks_has_no_channel_mentions():
    channel = make_channel(1, 'chat')
    threads = [make_thread(11, 1, 'see <#123> please')]
    blocks = r.collect_blocks(make_guild([(None, [channel])]), threads, [], set())
    assert '<#' not in '\n'.join(blocks)


# --- escape_label ------------------------------------------------------------

@pytest.mark.parametrize('name, expected', [
    ('a]b', r'a\]b'),
    ('*bold*', r'\*bold\*'),
    ('snake_case', r'snake\_case'),
    ('x[y](z)', r'x\[y\]\(z\)'),
    ('trailing\\', 'trailing\\\\'),
    ('<#123>', r'\<\#123\>'),
    ('https://example.com', r'https\://example.com'),
    ('plain name', 'plain name'),
])
def test_escape_label(name, expected):
    assert r.escape_label(name) == expected


@pytest.mark.parametrize('name', [
    'x\\](https://evil.example) [y',
    'x[y](z)',
    'trailing\\',
    'a]b',
    'https://example.com',
])
def test_rendered_entry_has_single_link_to_real_url(name):
    channel = make_channel(1, name)
    thread = make_thread(11, 1, name)
    block = r.collect_blocks(make_guild([(None, [channel])]), [thread], [], set())[0]

    header, entry = block.split('\n')
    assert header.count('](') == 1 and header.endswith(f']({channel.jump_url})**')
    assert entry.count('](') == 1 and entry.endswith(f']({thread.jump_url})')
    assert 'evil.example)' not in _entry_urls(block)
    assert _entry_urls(block) == [channel.jump_url, thread.jump_url]


# --- build_pages -------------------------------------------------------------

def _block(name, n_entries, entry_len=40):
    return '\n'.join([f'**[#{name}](u)**'] + [f'- {name}{i}'.ljust(entry_len, 'x') for i in range(n_entries)])


def test_build_pages_empty():
    assert r.build_pages([]) == ['']


def test_build_pages_breaks_only_between_blocks():
    blocks = [_block(f'c{i}', 10) for i in range(6)]  # ~420 chars each
    pages = r.build_pages(blocks, budget=1000)

    assert len(pages) > 1
    assert all(len(p) <= 1000 for p in pages)
    assert [b for p in pages for b in p.split('\n\n')] == blocks


def test_build_pages_default_budget():
    pages = r.build_pages([_block(f'c{i}', 30) for i in range(20)])
    assert len(pages) > 1
    assert all(len(p) <= r.DESCRIPTION_BUDGET for p in pages)


def test_build_pages_splits_oversize_block_with_continued_header():
    big = _block('big', 60)  # ~2400 chars
    pages = r.build_pages([_block('small', 2), big], budget=1000)

    assert all(len(p) <= 1000 for p in pages)
    continued = [p for p in pages if p.startswith('**[#big](u)** (continued)')]
    assert continued
    entries = [l for p in pages for l in p.split('\n') if l.startswith('- big')]
    assert entries == big.split('\n')[1:]


# --- build_embed -------------------------------------------------------------

REFRESHED = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


def test_build_embed():
    embed = r.build_embed('some page', 1, 3, REFRESHED)
    assert embed.title == 'Thread map'
    assert embed.description == 'some page'
    assert embed.footer.text == 'Page 2 of 3, last refreshed'
    assert embed.timestamp == REFRESHED
    assert embed.color == nextcord.Color.dark_blue()


def test_build_embed_empty_state():
    embed = r.build_embed('', 0, 1, REFRESHED)
    assert embed.description == 'No active threads match this map.'
    assert embed.footer.text == 'Page 1 of 1, last refreshed'


# --- fetch_active_threads ----------------------------------------------------

async def test_fetch_active_threads_returns_http_result():
    live = [make_thread(1, 10)]
    guild = make_guild(threads=[make_thread(2, 10)], active_threads=AsyncMock(return_value=live))
    assert await r.fetch_active_threads(guild) == live


async def test_fetch_active_threads_falls_back_to_cache():
    cached = [make_thread(2, 10)]
    error = nextcord.HTTPException(SimpleNamespace(status=500, reason='Server Error'), 'boom')
    guild = make_guild(threads=cached, active_threads=AsyncMock(side_effect=error))
    assert await r.fetch_active_threads(guild) == cached
