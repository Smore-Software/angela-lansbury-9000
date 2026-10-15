"""``/thread-map`` command, view registration, and delete cleanup.

The slash callback is driven via ``cog.thread_map.callback(cog, interaction, role)``
with fakes; ``render_pages`` is patched so nothing touches HTTP.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import nextcord
import pytest
from sqlalchemy import func, select

from bot.cogs.thread_map import thread_map_commands as tc
from bot.cogs.thread_map import thread_map_view as tv
from bot.cogs.thread_map.thread_map_service import Rendered
from bot.cogs.thread_map.thread_map_view import ThreadMapView
from db import DB
from db.helpers import thread_map_helper as h
from db.model.thread_map import ThreadMap, ThreadMapPage, ThreadMapRole
from tests.conftest import FAKE_GUILD_ID, FakeVoiceChannel, make_channel, make_guild, make_role

SENT_MESSAGE_ID = 4242


def _http_error():
    return nextcord.HTTPException(SimpleNamespace(status=403, reason='Forbidden'), 'nope')


def _host(send_messages=True, embed_links=True, cls=None):
    channel = make_channel(1, 'general') if cls is None else make_channel(1, 'voice', cls=cls)
    channel.permissions_for = lambda _: SimpleNamespace(send_messages=send_messages, embed_links=embed_links)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=SENT_MESSAGE_ID, delete=AsyncMock()))
    return channel


def _interaction(channel):
    return SimpleNamespace(
        channel=channel,
        guild=make_guild(),
        user=SimpleNamespace(id=77),
        response=SimpleNamespace(defer=AsyncMock()),
        send=AsyncMock(),
    )


@pytest.fixture
def cog():
    return tc.ThreadMapCommands(MagicMock())


@pytest.fixture
def rendered(monkeypatch):
    render = AsyncMock(return_value=Rendered(pages=['page one', 'page two'], channel_count=2, thread_count=5))
    monkeypatch.setattr(tc, 'render_pages', render)
    return render


@pytest.fixture
def sentry(monkeypatch):
    captured = []
    monkeypatch.setattr(tc.sentry_sdk, 'capture_exception', captured.append)
    return captured


def _maps():
    with DB.Session() as s:
        return s.scalars(select(ThreadMap)).all()


def _count(model):
    with DB.Session() as s:
        return s.scalar(select(func.count()).select_from(model))


def _reply(interaction):
    return interaction.send.await_args.kwargs['embed'].description


# --- refusals ----------------------------------------------------------------

async def test_refuses_non_text_channel(cog, rendered):
    channel = _host(cls=FakeVoiceChannel)
    interaction = _interaction(channel)
    await cog.thread_map.callback(cog, interaction, None)

    assert 'text or announcement channel' in _reply(interaction)
    channel.send.assert_not_awaited()
    rendered.assert_not_awaited()


@pytest.mark.parametrize('send_messages, embed_links', [(True, False), (False, True)])
async def test_refuses_without_bot_permissions(cog, rendered, send_messages, embed_links):
    channel = _host(send_messages=send_messages, embed_links=embed_links)
    interaction = _interaction(channel)
    await cog.thread_map.callback(cog, interaction, None)

    assert 'Embed Links' in _reply(interaction)
    channel.send.assert_not_awaited()
    assert _count(ThreadMap) == 0


# --- happy path --------------------------------------------------------------

async def test_posts_then_records_map(cog, rendered):
    channel = _host()
    interaction = _interaction(channel)
    role = make_role(500)
    role.is_default = lambda: False

    await cog.thread_map.callback(cog, interaction, role)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    rendered.assert_awaited_once_with(interaction.guild, [500], [])
    channel.send.assert_awaited_once()
    sent = channel.send.await_args.kwargs
    assert sent['embed'].description == 'page one'
    assert sent['embed'].footer.text == 'Page 1 of 2, last refreshed'
    assert sent['view'].is_persistent()

    (thread_map,) = _maps()
    with DB.Session() as s:
        config = h.get_config_by_message(s, SENT_MESSAGE_ID)
        assert h.get_page(s, config.id, 1) == 'page two'
    assert (config.guild_id, config.channel_id, config.role_ids) == (FAKE_GUILD_ID, 1, {500})
    assert config.last_refreshed_at == sent['embed'].timestamp
    assert _reply(interaction) == 'Thread map posted.'


async def test_everyone_role_stores_no_role_row(cog, rendered):
    interaction = _interaction(_host())
    everyone = make_role(FAKE_GUILD_ID)
    everyone.is_default = lambda: True

    await cog.thread_map.callback(cog, interaction, everyone)

    rendered.assert_awaited_once_with(interaction.guild, [], [])
    assert _count(ThreadMap) == 1
    assert _count(ThreadMapRole) == 0


async def test_administrator_role_warns(cog, rendered):
    interaction = _interaction(_host())
    admin = make_role(600, administrator=True)
    admin.is_default = lambda: False
    admin.mention = '<@&600>'

    await cog.thread_map.callback(cog, interaction, admin)

    assert 'Thread map posted.' in _reply(interaction)
    assert '<@&600> has Administrator' in _reply(interaction)


# --- failures ----------------------------------------------------------------

async def test_send_failure_writes_nothing(cog, rendered, sentry):
    channel = _host()
    channel.send = AsyncMock(side_effect=_http_error())
    interaction = _interaction(channel)

    await cog.thread_map.callback(cog, interaction, None)

    assert _count(ThreadMap) == 0
    assert "couldn't post" in _reply(interaction)
    assert len(sentry) == 1


async def test_db_failure_after_send_deletes_message(cog, rendered, sentry, monkeypatch):
    channel = _host()
    interaction = _interaction(channel)
    monkeypatch.setattr(tc.thread_map_helper, 'create_map', MagicMock(side_effect=RuntimeError('db down')))

    await cog.thread_map.callback(cog, interaction, None)

    channel.send.return_value.delete.assert_awaited_once()
    assert _count(ThreadMap) == 0
    assert 'Something went wrong' in _reply(interaction)
    assert len(sentry) == 1


async def test_db_failure_survives_failed_delete(cog, rendered, sentry, monkeypatch):
    channel = _host()
    channel.send.return_value.delete = AsyncMock(side_effect=_http_error())
    interaction = _interaction(channel)
    monkeypatch.setattr(tc.thread_map_helper, 'create_map', MagicMock(side_effect=RuntimeError('db down')))

    await cog.thread_map.callback(cog, interaction, None)
    assert 'Something went wrong' in _reply(interaction)


# --- registration ------------------------------------------------------------

def test_command_registration():
    command = tc.ThreadMapCommands.thread_map
    assert command.name == 'thread-map'
    assert command.force_global is True
    assert command.default_member_permissions == nextcord.Permissions(manage_guild=True)
    assert command.options['role'].required is False


async def test_on_ready_registers_persistent_view(cog):
    await cog.on_ready()

    cog.bot.add_view.assert_called_once()
    (view,), _ = cog.bot.add_view.call_args
    assert isinstance(view, ThreadMapView) and view.is_persistent()
    assert len(view.children) == 4


async def test_on_ready_reports_failure(cog, monkeypatch):
    messages = []
    monkeypatch.setattr(tc.sentry_sdk, 'capture_message', messages.append)
    monkeypatch.setattr(tc.sentry_sdk, 'capture_exception', lambda e: None)
    cog.bot.add_view.side_effect = RuntimeError('boom')

    await cog.on_ready()
    assert len(messages) == 1


def test_cog_is_exported_from_bot_cogs():
    from bot.cogs import ThreadMapCommands
    assert ThreadMapCommands is tc.ThreadMapCommands
    assert 'ThreadMapCommands' in __import__('bot.cogs', fromlist=['__all__']).__all__


# --- delete cleanup ----------------------------------------------------------

async def test_message_delete_removes_map_and_cooldown(cog):
    with DB.Session() as s:
        map_id = h.create_map(s, guild_id=1, channel_id=1, message_id=SENT_MESSAGE_ID, created_by=1,
                              role_ids=[500], pages=['a', 'b'], refreshed_at=tc.datetime.now(tc.timezone.utc))
        s.commit()
    tv._last_refresh[map_id] = 1.0

    await cog.on_raw_message_delete(SimpleNamespace(message_id=SENT_MESSAGE_ID))

    assert _count(ThreadMap) == _count(ThreadMapRole) == _count(ThreadMapPage) == 0
    assert map_id not in tv._last_refresh


async def test_message_delete_unknown_id_is_noop(cog, sentry):
    await cog.on_raw_message_delete(SimpleNamespace(message_id=123456))
    assert sentry == []


async def test_message_delete_swallows_errors(cog, sentry, monkeypatch):
    monkeypatch.setattr(tc.thread_map_helper, 'delete_by_message', MagicMock(side_effect=RuntimeError('db down')))
    await cog.on_raw_message_delete(SimpleNamespace(message_id=1))
    assert len(sentry) == 1
