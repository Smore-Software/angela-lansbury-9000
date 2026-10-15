"""Public thread map view (paging, Refresh, errors) and the service's rebuild."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nextcord
import pytest
from nextcord.components import _component_factory

from bot.cogs.thread_map import thread_map_service as service
from bot.cogs.thread_map import thread_map_view as tv
from bot.cogs.thread_map.thread_map_view import ThreadMapView
from db import DB
from db.helpers import thread_map_helper as h
from tests.conftest import make_channel, make_guild, make_role, make_thread

REFRESHED = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
MESSAGE_ID = 777
ALL_IDS = {'thread_map:prev', 'thread_map:next', 'thread_map:refresh', 'thread_map:edit'}


@pytest.fixture(autouse=True)
def _clear_cooldowns():
    tv._last_refresh.clear()
    yield
    tv._last_refresh.clear()


def _seed(pages=('p0', 'p1', 'p2'), current_page=0, role_ids=(), message_id=MESSAGE_ID):
    with DB.Session() as s:
        map_id = h.create_map(s, guild_id=1000, channel_id=1, message_id=message_id, created_by=3,
                              role_ids=role_ids, pages=list(pages), refreshed_at=REFRESHED)
        h.set_current_page(s, map_id, current_page)
        s.commit()
    return map_id


def _config(map_id):
    with DB.Session() as s:
        return h.get_config(s, map_id)


def _interaction(guild=None, message_id=MESSAGE_ID):
    return SimpleNamespace(
        message=SimpleNamespace(id=message_id, edit=AsyncMock()),
        guild=guild or make_guild(),
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock()),
        send=AsyncMock(),
    )


def _sent_description(interaction):
    return interaction.send.await_args.kwargs['embed'].description


def _refresh_rebuilt(page_count=1):
    return service.Rebuilt(embed=nextcord.Embed(title='Thread map'), page_count=page_count,
                           channel_count=0, thread_count=0)


# --- construction ------------------------------------------------------------

async def test_registration_form_is_persistent_with_all_buttons():
    view = ThreadMapView()
    assert view.is_persistent()
    assert {c.custom_id for c in view.children} == ALL_IDS
    assert not any(c.disabled for c in view.children)


async def test_single_page_drops_nav_buttons():
    view = ThreadMapView(0, 1)
    assert [c.custom_id for c in view.children] == ['thread_map:refresh', 'thread_map:edit']


async def test_first_of_many_disables_prev_only():
    view = ThreadMapView(0, 3)
    assert view.prev_page.disabled and not view.next_page.disabled
    last = ThreadMapView(2, 3)
    assert last.next_page.disabled and not last.prev_page.disabled


async def test_view_refresh_is_not_shadowed_by_a_button():
    # nextcord calls View.refresh(components) on every MESSAGE_UPDATE for a
    # tracked message; a button callback named ``refresh`` replaces it and the
    # resulting TypeError crashes the gateway loop.
    view = ThreadMapView(0, 3)
    components = [_component_factory(row) for row in view.to_components()]
    view.refresh(components)
    assert {c.custom_id for c in view.children} == ALL_IDS


# --- paging ------------------------------------------------------------------

async def test_next_page_updates_and_edits_with_stored_snapshot(monkeypatch):
    map_id = _seed()
    fetch = AsyncMock()
    monkeypatch.setattr(service, 'fetch_active_threads', fetch)
    interaction = _interaction()

    await ThreadMapView().next_page.callback(interaction)

    assert _config(map_id).current_page == 1
    assert _config(map_id).last_refreshed_at == REFRESHED
    kwargs = interaction.response.edit_message.await_args.kwargs
    assert kwargs['embed'].description == 'p1'
    assert kwargs['embed'].timestamp == REFRESHED
    assert kwargs['embed'].footer.text == 'Page 2 of 3, last refreshed'
    assert not kwargs['view'].prev_page.disabled
    fetch.assert_not_awaited()


async def test_prev_page_clamps_at_first_page():
    map_id = _seed(current_page=0)
    interaction = _interaction()
    await ThreadMapView().prev_page.callback(interaction)

    assert _config(map_id).current_page == 0
    assert interaction.response.edit_message.await_args.kwargs['embed'].description == 'p0'


async def test_next_page_clamps_at_last_page():
    map_id = _seed(current_page=2)
    interaction = _interaction()
    await ThreadMapView().next_page.callback(interaction)

    assert _config(map_id).current_page == 2
    assert interaction.response.edit_message.await_args.kwargs['embed'].description == 'p2'


async def test_stale_out_of_range_page_is_clamped():
    map_id = _seed(pages=['only'], current_page=4)
    interaction = _interaction()
    await ThreadMapView().next_page.callback(interaction)

    assert _config(map_id).current_page == 0
    assert interaction.response.edit_message.await_args.kwargs['embed'].description == 'only'


@pytest.mark.parametrize('button', ['prev_page', 'next_page', 'refresh_map'])
async def test_unknown_message_reports_untracked(button):
    interaction = _interaction(message_id=999)
    await getattr(ThreadMapView(), button).callback(interaction)

    assert _sent_description(interaction) == tv.UNTRACKED_TEXT
    assert interaction.send.await_args.kwargs['ephemeral'] is True
    interaction.response.edit_message.assert_not_awaited()


# --- refresh -----------------------------------------------------------------

async def test_refresh_rebuilds_and_edits(monkeypatch):
    map_id = _seed()
    rebuild = AsyncMock(return_value=_refresh_rebuilt(page_count=2))
    monkeypatch.setattr(tv, 'rebuild', rebuild)
    interaction = _interaction()

    await ThreadMapView().refresh_map.callback(interaction)

    rebuild.assert_awaited_once_with(interaction.guild, map_id)
    interaction.response.defer.assert_awaited_once()
    kwargs = interaction.message.edit.await_args.kwargs
    assert kwargs['embed'] is rebuild.return_value.embed
    assert kwargs['view'].prev_page.disabled and not kwargs['view'].next_page.disabled
    assert map_id in tv._last_refresh


async def test_refresh_within_cooldown_skips_rebuild(monkeypatch):
    _seed()
    rebuild = AsyncMock(return_value=_refresh_rebuilt())
    monkeypatch.setattr(tv, 'rebuild', rebuild)

    await ThreadMapView().refresh_map.callback(_interaction())
    second = _interaction()
    await ThreadMapView().refresh_map.callback(second)

    assert rebuild.await_count == 1
    assert _sent_description(second) == 'Refreshed moments ago.'
    second.response.defer.assert_not_awaited()


async def test_refresh_after_cooldown_rebuilds_again(monkeypatch):
    map_id = _seed()
    rebuild = AsyncMock(return_value=_refresh_rebuilt())
    monkeypatch.setattr(tv, 'rebuild', rebuild)
    clock = [1000.0]
    monkeypatch.setattr(tv.time, 'monotonic', lambda: clock[0])

    await ThreadMapView().refresh_map.callback(_interaction())
    clock[0] += tv.REFRESH_COOLDOWN + 1
    await ThreadMapView().refresh_map.callback(_interaction())

    assert rebuild.await_count == 2
    assert tv._last_refresh[map_id] == clock[0]


async def test_failed_rebuild_does_not_start_cooldown(monkeypatch):
    map_id = _seed()
    monkeypatch.setattr(tv, 'rebuild', AsyncMock(side_effect=RuntimeError('boom')))

    with pytest.raises(RuntimeError):
        await ThreadMapView().refresh_map.callback(_interaction())
    assert map_id not in tv._last_refresh


async def test_rebuild_none_reports_untracked(monkeypatch):
    map_id = _seed()
    monkeypatch.setattr(tv, 'rebuild', AsyncMock(return_value=None))
    interaction = _interaction()

    await ThreadMapView().refresh_map.callback(interaction)

    assert _sent_description(interaction) == tv.UNTRACKED_TEXT
    interaction.message.edit.assert_not_awaited()
    assert map_id not in tv._last_refresh


async def test_refresh_swallows_http_exception_on_edit(monkeypatch):
    _seed()
    monkeypatch.setattr(tv, 'rebuild', AsyncMock(return_value=_refresh_rebuilt()))
    captured = []
    monkeypatch.setattr(tv.sentry_sdk, 'capture_exception', captured.append)
    interaction = _interaction()
    error = nextcord.HTTPException(SimpleNamespace(status=429, reason='Too Many Requests'), 'slow down')
    interaction.message.edit = AsyncMock(side_effect=error)

    await ThreadMapView().refresh_map.callback(interaction)
    assert captured == [error]


# --- on_error ----------------------------------------------------------------

async def test_on_error_captures_and_replies(monkeypatch):
    captured = []
    monkeypatch.setattr(tv.sentry_sdk, 'capture_exception', captured.append)
    interaction = _interaction()
    error = RuntimeError('boom')

    await ThreadMapView().on_error(error, None, interaction)

    assert captured == [error]
    interaction.send.assert_awaited_once()
    assert interaction.send.await_args.kwargs['ephemeral'] is True


async def test_on_error_survives_failed_reply(monkeypatch):
    monkeypatch.setattr(tv.sentry_sdk, 'capture_exception', lambda e: None)
    interaction = _interaction()
    interaction.send = AsyncMock(side_effect=nextcord.HTTPException(SimpleNamespace(status=404, reason='x'), 'gone'))

    await ThreadMapView().on_error(RuntimeError('boom'), None, interaction)
    interaction.send.assert_awaited_once()


# --- service.rebuild ---------------------------------------------------------

async def test_rebuild_missing_map_returns_none(monkeypatch):
    monkeypatch.setattr(service, 'fetch_active_threads', AsyncMock(return_value=[]))
    assert await service.rebuild(make_guild(), 12345) is None


async def test_rebuild_uses_config_read_after_fetch(monkeypatch):
    a, b = make_channel(1, 'a'), make_channel(2, 'b')
    guild = make_guild([(None, [a, b])])
    map_id = _seed()

    async def fetch_and_edit(_guild):
        # A moderator's exclusion commits while the thread fetch is in flight.
        with DB.Session() as s:
            h.add_exclusions(s, map_id, [2])
            s.commit()
        return [make_thread(11, 1, 'in-a'), make_thread(12, 2, 'in-b')]

    monkeypatch.setattr(service, 'fetch_active_threads', fetch_and_edit)
    rebuilt = await service.rebuild(guild, map_id)

    assert 'in-a' in rebuilt.embed.description and 'in-b' not in rebuilt.embed.description
    assert (rebuilt.channel_count, rebuilt.thread_count, rebuilt.page_count) == (1, 1, 1)
    config = _config(map_id)
    assert config.excluded_ids == {2}
    assert config.current_page == 0 and config.page_count == 1
    assert config.last_refreshed_at > REFRESHED
    assert rebuilt.embed.timestamp == config.last_refreshed_at


async def test_rebuild_prunes_deleted_channels_and_roles(monkeypatch):
    live_role = make_role(500)
    channel = make_channel(1)
    guild = make_guild([(None, [channel])], roles=[live_role])
    map_id = _seed(role_ids=[500, 501])
    with DB.Session() as s:
        h.add_exclusions(s, map_id, [1, 99])
        s.commit()
    monkeypatch.setattr(service, 'fetch_active_threads', AsyncMock(return_value=[]))

    await service.rebuild(guild, map_id)

    config = _config(map_id)
    assert config.role_ids == {500}
    assert config.excluded_ids == {1}


def test_render_with_threads_skips_unknown_roles():
    hidden = make_channel(1, 'staff', visible_to={500})
    guild = make_guild([(None, [hidden])], roles=[make_role(500)])
    threads = [make_thread(11, 1)]

    assert service.render_with_threads(guild, threads, [500, 999], []).channel_count == 1
    assert service.render_with_threads(guild, threads, [999], []).pages == ['']


# --- edit button -------------------------------------------------------------

def _member(manage_guild):
    return SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=manage_guild))


async def test_edit_refused_without_manage_guild():
    _seed()
    interaction = _interaction()
    interaction.user = _member(False)
    interaction.response.send_message = AsyncMock()

    await ThreadMapView().edit_map.callback(interaction)

    assert 'Manage Server' in _sent_description(interaction)
    assert interaction.send.await_args.kwargs['ephemeral'] is True
    interaction.response.send_message.assert_not_awaited()


async def test_edit_opens_ephemeral_menu_with_summary():
    from bot.cogs.thread_map.edit_menu_view import EditMenuView

    map_id = _seed()
    interaction = _interaction()
    interaction.user = _member(True)
    interaction.response.send_message = AsyncMock()

    await ThreadMapView().edit_map.callback(interaction)

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs['ephemeral'] is True
    assert isinstance(kwargs['view'], EditMenuView) and kwargs['view'].map_id == map_id
    assert kwargs['view']._interaction is interaction
    assert kwargs['embed'].title == 'Edit thread map'
    assert '**Showing channels visible to**: @everyone' in kwargs['embed'].description


async def test_edit_unknown_message_reports_untracked():
    interaction = _interaction(message_id=999)
    interaction.user = _member(True)
    await ThreadMapView().edit_map.callback(interaction)
    assert _sent_description(interaction) == tv.UNTRACKED_TEXT
