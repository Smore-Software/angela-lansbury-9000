"""Tests for entry-point session recovery (Phase 4 of the DB resilience epic).

Two outer entry points are proven here:

* ``bot.app.on_application_command_error`` — the global slash-command error
  handler. A non-cooldown failure must recover the poisoned process-global
  session, attempt an ephemeral "something went wrong" reply, and STILL re-raise
  so prod Sentry captures via the SDK integration. The recover must happen
  *before* the reply so an expired interaction (send raises) can't skip it, and
  the re-raise must survive a failed send. The cooldown branch is untouched:
  it replies and returns without recovering or re-raising.

* ``bot.events.on_message_event`` — ``on_message`` now guards each handler call
  independently, so an ``image_message_handler`` failure recovers the session
  AND cannot starve ``activity_handler``.

Everything is patched surgically on the module namespace under test (never the
real ``sentry_sdk``/``db`` modules) so nothing leaks across tests.
"""
import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cooldowns
import pytest

import bot.app as app
from bot.events import on_message_event


def _make_cooldown_error(retry_after_seconds: float = 5.0):
    """Build a real ``CallableOnCooldown`` without its full constructor.

    ``retry_after`` is a read-only property derived from ``resets_at``, so we
    mint a bare instance and set only that attribute — enough for the handler's
    ``isinstance`` check and its ``error.retry_after`` read.
    """
    err = cooldowns.CallableOnCooldown.__new__(cooldowns.CallableOnCooldown)
    utc_now = inspect.getmodule(cooldowns.CallableOnCooldown)._utc_now
    err.resets_at = utc_now() + datetime.timedelta(seconds=retry_after_seconds)
    return err


# --- on_application_command_error ------------------------------------------


async def test_non_cooldown_error_recovers_replies_and_reraises(monkeypatch):
    recovered = []
    monkeypatch.setattr(app, 'recover_session', lambda: recovered.append(True))

    inter = SimpleNamespace(send=AsyncMock())
    boom = RuntimeError('boom')

    with pytest.raises(RuntimeError, match='boom'):
        await app.on_application_command_error(inter, boom)

    # Session recovered, ephemeral reply attempted, error re-raised.
    assert recovered == [True]
    inter.send.assert_awaited_once()
    assert inter.send.await_args.kwargs['ephemeral'] is True


async def test_recover_runs_before_the_reply(monkeypatch):
    # The whole point of the ordering: recovery precedes the send so a failing
    # (expired) interaction can never skip it.
    order = []
    monkeypatch.setattr(app, 'recover_session', lambda: order.append('recover'))

    async def fake_send(*args, **kwargs):
        order.append('send')

    inter = SimpleNamespace(send=fake_send)

    with pytest.raises(RuntimeError):
        await app.on_application_command_error(inter, RuntimeError('boom'))

    assert order == ['recover', 'send']


async def test_expired_interaction_still_reraises(monkeypatch):
    # send raises (interaction already acknowledged / expired) — recovery already
    # happened and the original error must still propagate for prod Sentry.
    recovered = []
    monkeypatch.setattr(app, 'recover_session', lambda: recovered.append(True))

    inter = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError('expired')))
    boom = ValueError('original')

    with pytest.raises(ValueError, match='original'):
        await app.on_application_command_error(inter, boom)

    assert recovered == [True]


async def test_original_attribute_is_unwrapped(monkeypatch):
    # nextcord wraps command errors in ApplicationInvokeError; the handler reads
    # ``.original`` and must recover + re-raise the unwrapped cause.
    monkeypatch.setattr(app, 'recover_session', lambda: None)

    original = KeyError('inner')
    wrapper = SimpleNamespace(original=original)
    inter = SimpleNamespace(send=AsyncMock())

    with pytest.raises(KeyError):
        await app.on_application_command_error(inter, wrapper)

    inter.send.assert_awaited_once()


async def test_cooldown_error_replies_without_recover_or_reraise(monkeypatch):
    # The cooldown branch is unchanged: it replies and returns — no recovery,
    # no re-raise.
    recovered = []
    monkeypatch.setattr(app, 'recover_session', lambda: recovered.append(True))

    inter = SimpleNamespace(send=AsyncMock())
    cooldown_error = _make_cooldown_error()

    # Must not raise.
    await app.on_application_command_error(inter, cooldown_error)

    assert recovered == []
    inter.send.assert_awaited_once()
    assert inter.send.await_args.kwargs['ephemeral'] is True


# --- on_message isolated per-handler guards --------------------------------


async def test_on_message_image_failure_recovers_and_activity_still_runs(monkeypatch):
    recovered = []
    activity_ran = []

    async def failing_image(message):
        raise RuntimeError('image handler exploded')

    async def recording_activity(message):
        activity_ran.append(message)

    monkeypatch.setattr(on_message_event, 'image_message_handler', failing_image)
    monkeypatch.setattr(on_message_event, 'activity_handler', recording_activity)
    monkeypatch.setattr(on_message_event, 'recover_session',
                        lambda: recovered.append(True))
    monkeypatch.setattr(on_message_event, 'sentry_sdk',
                        SimpleNamespace(capture_exception=lambda e: None))

    # Capture the callback the closure registers via @bot.event.
    captured = []
    fake_bot = SimpleNamespace(
        event=lambda f: (captured.append(f), f)[1],
        user=object(),
    )
    on_message_event.register_event(fake_bot)
    on_message = captured[0]

    message = SimpleNamespace(author=object())  # != fake_bot.user, so we proceed
    await on_message(message)

    # Image handler failed -> session recovered; activity handler still ran.
    assert recovered == [True]
    assert activity_ran == [message]


async def test_on_message_skips_own_messages(monkeypatch):
    # Sanity: the author == bot.user short-circuit still holds after the guard
    # rewrite — neither handler is invoked.
    called = []
    monkeypatch.setattr(on_message_event, 'image_message_handler',
                        lambda m: called.append('image'))
    monkeypatch.setattr(on_message_event, 'activity_handler',
                        lambda m: called.append('activity'))

    captured = []
    me = object()
    fake_bot = SimpleNamespace(
        event=lambda f: (captured.append(f), f)[1],
        user=me,
    )
    on_message_event.register_event(fake_bot)
    on_message = captured[0]

    await on_message(SimpleNamespace(author=me))

    assert called == []
