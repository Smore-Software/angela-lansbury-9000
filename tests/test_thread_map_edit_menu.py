"""The ephemeral moderator edit menu: sub-view swaps, each selection's DB write,
the push to the public map, and the failure paths."""
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nextcord
import pytest

from bot.cogs.thread_map import edit_menu_view as em
from bot.cogs.thread_map import thread_map_view as tv
from bot.cogs.thread_map.thread_map_service import Rebuilt
from db import DB
from db.helpers import thread_map_helper as h
from tests.conftest import FAKE_GUILD_ID, make_channel, make_guild, make_role

REFRESHED = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
ADMIN = make_role(600, administrator=True)
MEMBERS = make_role(500)
for _role, _name in ((ADMIN, 'Admin'), (MEMBERS, 'Members')):
    _role.name = _name

GENERAL = make_channel(1, 'general')
STAFF = make_channel(2, 'staff')


def _guild():
    return make_guild([(None, [GENERAL, STAFF])], roles=[ADMIN, MEMBERS])


def _seed(role_ids=(), excluded=(), message_id=777):
    with DB.Session() as s:
        map_id = h.create_map(s, guild_id=FAKE_GUILD_ID, channel_id=1, message_id=message_id, created_by=3,
                              role_ids=role_ids, pages=['p0'], refreshed_at=REFRESHED)
        h.add_exclusions(s, map_id, excluded)
        s.commit()
    return map_id


def _config(map_id):
    with DB.Session() as s:
        return h.get_config(s, map_id)


def _interaction(values=(), guild=None):
    return SimpleNamespace(
        guild=guild or _guild(),
        data={'values': [str(v) for v in values]},
        client=SimpleNamespace(),
        response=SimpleNamespace(edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_message=AsyncMock(),
        send=AsyncMock(),
    )


REBUILT = Rebuilt(embed=nextcord.Embed(title='Thread map'), page_count=1, channel_count=3, thread_count=7)


@pytest.fixture
def pushed(monkeypatch):
    """Patch rebuild + push_to_message; returns the push mock (result 'ok')."""
    monkeypatch.setattr(em, 'rebuild', AsyncMock(return_value=REBUILT))
    push = AsyncMock(return_value='ok')
    monkeypatch.setattr(em, 'push_to_message', push)
    return push


def _edited(interaction):
    return interaction.response.edit_message.await_args.kwargs


# --- main menu ---------------------------------------------------------------

async def test_remove_role_disabled_without_roles():
    assert em.EditMenuView(_config(_seed())).remove_role.disabled
    assert not em.EditMenuView(_config(_seed(role_ids=[500], message_id=778))).remove_role.disabled


@pytest.mark.parametrize('button, view_type', [
    ('exclude_channel', em.ExcludeChannelsView),
    ('include_channel', em.IncludeChannelsView),
    ('add_role', em.AddRolesView),
    ('remove_role', em.RemoveRolesView),
])
async def test_buttons_swap_to_sub_view_and_stop_menu(button, view_type):
    menu = em.EditMenuView(_config(_seed(role_ids=[500])))
    interaction = _interaction()

    await getattr(menu, button).callback(interaction)

    assert menu.is_finished()
    sub = _edited(interaction)['view']
    assert isinstance(sub, view_type)
    assert sub._interaction is interaction
    assert not sub.is_finished()


async def test_back_returns_to_menu_without_writes():
    map_id = _seed(excluded=[2])
    sub = em.ExcludeChannelsView(map_id)
    interaction = _interaction()

    await sub.back.callback(interaction)

    assert sub.is_finished()
    kwargs = _edited(interaction)
    assert isinstance(kwargs['view'], em.EditMenuView)
    assert 'Excluded channels: #staff' in kwargs['embed'].description
    assert _config(map_id).excluded_ids == {2}


# --- selections --------------------------------------------------------------

async def test_exclude_writes_rows_and_pushes(pushed):
    map_id = _seed()
    sub = em.ExcludeChannelsView(map_id)
    interaction = _interaction(values=[2, 1])

    await sub.channels.callback(interaction)

    assert _config(map_id).excluded_ids == {1, 2}
    assert sub.is_finished()
    kwargs = _edited(interaction)
    assert isinstance(kwargs['view'], em.EditMenuView)
    assert 'Excluded channels: #general, #staff' in kwargs['embed'].description
    pushed.assert_awaited_once()
    assert pushed.await_args.args[1].excluded_ids == {1, 2}
    final = interaction.edit_original_message.await_args.kwargs['embed']
    assert 'Map is showing 3 channels with 7 threads' in final.description


async def test_include_removes_exclusion(pushed):
    map_id = _seed(excluded=[1, 2])
    await em.IncludeChannelsView(map_id).channels.callback(_interaction(values=[2]))
    assert _config(map_id).excluded_ids == {1}


async def test_include_of_non_excluded_channel_is_noop_success(pushed):
    map_id = _seed(excluded=[1])
    interaction = _interaction(values=[2])

    await em.IncludeChannelsView(map_id).channels.callback(interaction)

    assert _config(map_id).excluded_ids == {1}
    assert isinstance(_edited(interaction)['view'], em.EditMenuView)
    pushed.assert_awaited_once()


async def test_add_role_ignores_everyone_and_warns_on_admin(pushed):
    map_id = _seed()
    interaction = _interaction(values=[FAKE_GUILD_ID, 500, 600])

    await em.AddRolesView(map_id).roles.callback(interaction)

    assert _config(map_id).role_ids == {500, 600}
    description = _edited(interaction)['embed'].description
    assert '**Showing channels visible to**: @everyone, @Admin, @Members' in description
    assert '@Admin has Administrator' in description
    assert '@Members has Administrator' not in description


async def test_add_role_reports_cap_rejection(pushed):
    map_id = _seed(role_ids=range(1, 25))
    interaction = _interaction(values=[500, 501, 502])

    await em.AddRolesView(map_id).roles.callback(interaction)

    assert len(_config(map_id).role_ids) == h.ROLE_CAP
    assert '2 role(s) not added' in _edited(interaction)['embed'].description


async def test_remove_roles_view_lists_configured_roles():
    view = em.RemoveRolesView(_guild(), _config(_seed(role_ids=[500, 999])))
    options = {o.value: o.label for o in view.roles.options}

    assert options == {'500': '@Members', '999': 'deleted role'}
    assert view.roles.max_values == 2
    assert not any(o.default for o in view.roles.options)


async def test_remove_role_deletes_rows(pushed):
    map_id = _seed(role_ids=[500, 600])
    view = em.RemoveRolesView(_guild(), _config(map_id))
    interaction = _interaction(values=[600])

    await view.roles.callback(interaction)

    assert _config(map_id).role_ids == {500}
    assert not _edited(interaction)['view'].remove_role.disabled


async def test_summary_labels_deleted_channels():
    cfg = _config(_seed(excluded=[2, 99]))
    assert 'Excluded channels: #staff, deleted channel' in em.summary_embed(_guild(), cfg).description
    assert 'Excluded channels: none' in em.summary_embed(_guild(), _config(_seed(message_id=778))).description


# --- failure paths -----------------------------------------------------------

async def test_map_deleted_while_menu_open(pushed):
    map_id = _seed()
    sub = em.ExcludeChannelsView(map_id)
    with DB.Session() as s:
        h.delete_map(s, map_id)
        s.commit()
    interaction = _interaction(values=[1])

    await sub.channels.callback(interaction)

    kwargs = _edited(interaction)
    assert kwargs['embed'].description == 'This map no longer exists.'
    assert kwargs['view'] is None
    pushed.assert_not_awaited()
    with DB.Session() as s:
        assert s.query(h.ThreadMapExcludedChannel).count() == 0


async def test_rebuild_none_follows_up(monkeypatch):
    map_id = _seed()
    monkeypatch.setattr(em, 'rebuild', AsyncMock(return_value=None))
    push = AsyncMock()
    monkeypatch.setattr(em, 'push_to_message', push)
    interaction = _interaction(values=[1])

    await em.ExcludeChannelsView(map_id).channels.callback(interaction)

    assert interaction.followup.send.await_args.kwargs['embed'].description == 'This map no longer exists.'
    push.assert_not_awaited()


async def test_push_gone_deletes_map_and_cooldown(pushed):
    map_id = _seed()
    tv._last_refresh[map_id] = 1.0
    pushed.return_value = 'gone'
    interaction = _interaction(values=[1])

    await em.ExcludeChannelsView(map_id).channels.callback(interaction)

    assert _config(map_id) is None
    assert map_id not in tv._last_refresh
    sent = interaction.followup.send.await_args.kwargs
    assert 'stopped tracking it' in sent['embed'].description and sent['ephemeral'] is True
    interaction.edit_original_message.assert_not_awaited()


async def test_push_forbidden_keeps_map(pushed):
    map_id = _seed()
    pushed.return_value = 'forbidden'
    interaction = _interaction(values=[1])

    await em.ExcludeChannelsView(map_id).channels.callback(interaction)

    assert _config(map_id).excluded_ids == {1}
    assert 'Check my permissions' in interaction.followup.send.await_args.kwargs['embed'].description


async def test_final_summary_edit_tolerates_expired_token(pushed):
    map_id = _seed()
    interaction = _interaction(values=[1])
    interaction.edit_original_message = AsyncMock(
        side_effect=nextcord.HTTPException(SimpleNamespace(status=401, reason='x'), 'expired'))
    await em.ExcludeChannelsView(map_id).channels.callback(interaction)


# --- push_to_message ---------------------------------------------------------

def _bot():
    # The real Client.get_partial_messageable, so PartialMessage's channel-type check runs for real.
    client = SimpleNamespace(_connection=SimpleNamespace())
    return SimpleNamespace(get_partial_messageable=types.MethodType(nextcord.Client.get_partial_messageable, client))


@pytest.mark.parametrize('error, expected', [
    (None, 'ok'),
    (nextcord.NotFound, 'gone'),
    (nextcord.Forbidden, 'forbidden'),
])
async def test_push_to_message(monkeypatch, error, expected):
    calls = []

    async def fake_edit(message, **kwargs):
        calls.append((message, kwargs))
        if error:
            raise error(SimpleNamespace(status=404, reason='x'), 'nope')

    monkeypatch.setattr(nextcord.PartialMessage, 'edit', fake_edit)
    cfg = _config(_seed())

    assert await em.push_to_message(_bot(), cfg, REBUILT) == expected

    ((message, kwargs),) = calls
    assert (message.id, message.channel.id) == (777, 1)
    assert message.channel.type == nextcord.ChannelType.text
    assert kwargs['embed'] is REBUILT.embed
    assert kwargs['view'].is_persistent()


# --- timeout / errors --------------------------------------------------------

async def test_on_timeout_disables_controls_and_tolerates_http_errors():
    menu = em.EditMenuView(_config(_seed(role_ids=[500])))
    menu._interaction = SimpleNamespace(edit_original_message=AsyncMock(
        side_effect=nextcord.HTTPException(SimpleNamespace(status=404, reason='x'), 'gone')))

    await menu.on_timeout()

    assert all(child.disabled for child in menu.children)
    menu._interaction.edit_original_message.assert_awaited_once()


async def test_sub_view_on_error_reports(monkeypatch):
    captured = []
    monkeypatch.setattr(tv.sentry_sdk, 'capture_exception', captured.append)
    interaction = _interaction()
    error = RuntimeError('boom')

    await em.AddRolesView(1).on_error(error, None, interaction)

    assert captured == [error]
    interaction.send.assert_awaited_once()


@pytest.mark.parametrize('view_cls', [
    tv.ThreadMapView, em.EditMenuView, em.ExcludeChannelsView, em.IncludeChannelsView,
    em.AddRolesView, em.RemoveRolesView,
])
def test_no_callback_shadows_a_view_attribute(view_cls):
    # A decorated callback named like a View attribute (e.g. ``refresh``) replaces
    # it on the class; nextcord then calls the Button and crashes the gateway loop.
    names = {f.__name__ for f in view_cls.__view_children_items__}
    assert not names & set(dir(nextcord.ui.View))
