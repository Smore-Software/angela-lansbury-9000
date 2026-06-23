"""Tests for the button-driven ``/starboard list`` pagination.

Covers the shared ``EmbedPaginatorView`` abstraction (single-page controls,
wrap-around navigation, footer stamping, timeout disabling) plus the ``list``
subcommand wiring through it — empty guild, single board, and multi-board paging.
The slash callback is reached via ``cog.list.callback`` with a fake interaction,
the same minimal stand-in the rest of the suite leans on.
"""
from types import SimpleNamespace

import nextcord
import pytest

from bot.cogs.starboard import starboard_commands as sc
from bot.utils.views import EmbedPaginatorView
from db.helpers import starboard_helper


@pytest.fixture(autouse=True)
def _clear_cache():
    # Same module-level config cache the command tests guard against leaking.
    starboard_helper.__CACHE.clear()
    yield
    starboard_helper.__CACHE.clear()


class FakeInteraction:
    """Records what the command/view would send or edit, with no live gateway.

    ``send`` is the initial ephemeral response, ``response.edit_message`` is what
    the nav buttons call, and ``edit_original_message`` is what ``on_timeout``
    reaches for — each just appends its kwargs for assertions.
    """

    def __init__(self, guild_id=1):
        self.guild_id = guild_id
        self.sent = []
        self.edited = []
        self.response = SimpleNamespace(edit_message=self._record_edit)

    async def send(self, **kwargs):
        self.sent.append(kwargs)

    async def _record_edit(self, **kwargs):
        self.edited.append(kwargs)

    async def edit_original_message(self, **kwargs):
        self.edited.append(kwargs)


def _embeds(n):
    return [nextcord.Embed(title=f'Board {i}') for i in range(n)]


# --- EmbedPaginatorView: construction & contract ----------------------------


@pytest.mark.asyncio
async def test_paginator_rejects_empty():
    # The list command guards the empty guild upstream; the view treats an empty
    # page set as a programming error rather than rendering a blank page.
    with pytest.raises(ValueError):
        EmbedPaginatorView([])


@pytest.mark.asyncio
async def test_paginator_single_page_has_no_nav_buttons():
    view = EmbedPaginatorView(_embeds(1))
    # A lone board has nowhere to page to, so the controls are dropped entirely.
    assert view.children == []


@pytest.mark.asyncio
async def test_paginator_multi_page_has_both_nav_buttons():
    view = EmbedPaginatorView(_embeds(3))
    emojis = {str(child.emoji) for child in view.children}
    assert emojis == {'◀️', '▶️'}


@pytest.mark.asyncio
async def test_paginator_footer_stamps_position_with_noun():
    view = EmbedPaginatorView(_embeds(3), noun='Board')
    assert view.current_embed.footer.text == 'Board 1/3'


# --- EmbedPaginatorView: navigation -----------------------------------------


@pytest.mark.asyncio
async def test_paginator_next_advances_and_edits_in_place():
    view = EmbedPaginatorView(_embeds(3), noun='Board')
    interaction = FakeInteraction()
    await view.next_page.callback(interaction)
    assert view.current_index == 1
    # Navigation edits the existing message (not a fresh send) with this view.
    assert interaction.edited[-1]['view'] is view
    assert interaction.edited[-1]['embed'].footer.text == 'Board 2/3'


@pytest.mark.asyncio
async def test_paginator_next_wraps_from_last_to_first():
    view = EmbedPaginatorView(_embeds(3))
    view.current_index = 2
    interaction = FakeInteraction()
    await view.next_page.callback(interaction)
    assert view.current_index == 0


@pytest.mark.asyncio
async def test_paginator_prev_wraps_from_first_to_last():
    view = EmbedPaginatorView(_embeds(3))
    interaction = FakeInteraction()
    await view.previous_page.callback(interaction)
    assert view.current_index == 2


@pytest.mark.asyncio
async def test_paginator_prev_then_next_round_trip():
    view = EmbedPaginatorView(_embeds(4))
    interaction = FakeInteraction()
    await view.next_page.callback(interaction)
    await view.next_page.callback(interaction)
    assert view.current_index == 2
    await view.previous_page.callback(interaction)
    assert view.current_index == 1


# --- EmbedPaginatorView: timeout --------------------------------------------


@pytest.mark.asyncio
async def test_paginator_timeout_disables_buttons_in_place():
    view = EmbedPaginatorView(_embeds(2))
    interaction = FakeInteraction()
    await view.send(interaction)
    await view.on_timeout()
    assert all(child.disabled for child in view.children)
    # The stale ephemeral message is edited so the dead buttons reflect it.
    assert interaction.edited[-1]['view'] is view


# --- /starboard list wiring -------------------------------------------------


def _add(guild_id=1, **kw):
    return starboard_helper.add_config(
        guild_id=guild_id, target_channel_id=kw.pop('target_channel_id', 10),
        emoji=kw.pop('emoji', '⭐'), **kw)


@pytest.mark.asyncio
async def test_list_empty_guild_sends_info_without_view():
    cog = sc.StarboardCommands(bot=None)
    interaction = FakeInteraction(guild_id=1)
    await cog.list.callback(cog, interaction)
    assert len(interaction.sent) == 1
    payload = interaction.sent[0]
    # Empty guild: a plain info embed, no paginator attached.
    assert 'view' not in payload
    assert payload['ephemeral'] is True


@pytest.mark.asyncio
async def test_list_single_board_pages_without_nav():
    _add(name='Solo')
    cog = sc.StarboardCommands(bot=None)
    interaction = FakeInteraction(guild_id=1)
    await cog.list.callback(cog, interaction)
    payload = interaction.sent[0]
    view = payload['view']
    assert isinstance(view, EmbedPaginatorView)
    assert payload['ephemeral'] is True
    assert view.children == []  # single board → no nav buttons
    assert payload['embed'].footer.text == 'Board 1/1'


@pytest.mark.asyncio
async def test_list_multi_board_paginates_one_per_page():
    _add(name='First', target_channel_id=10)
    _add(name='Second', target_channel_id=11)
    _add(name='Third', target_channel_id=12)
    cog = sc.StarboardCommands(bot=None)
    interaction = FakeInteraction(guild_id=1)
    await cog.list.callback(cog, interaction)
    payload = interaction.sent[0]
    view = payload['view']
    # One board per page (one field each), three pages, no 10-embed cap involved.
    assert len(view.embeds) == 3
    assert all(len(embed.fields) == 1 for embed in view.embeds)
    assert payload['embed'].footer.text == 'Board 1/3'
    # Wrap-around paging works through the live view the command handed off.
    nav = FakeInteraction()
    await view.previous_page.callback(nav)
    assert view.current_index == 2
    assert nav.edited[-1]['embed'].footer.text == 'Board 3/3'
