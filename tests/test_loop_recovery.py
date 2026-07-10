"""Tests for bot/utils/loop_recovery.py — the shared ``@tasks.loop`` recovery.

``recover_loop`` must, in this exact order, capture the exception to Sentry,
recover the poisoned process-global session (so the restarted iteration runs on
a clean ``DB.s``), back off with a per-loop capped exponential delay, then
restart the loop. The recovery behavior is proven here against a fake loop —
the real ``@tasks.loop`` machinery needs a live gateway — while the four cog
handlers are exercised only for delegation (each calls ``recover_loop`` with its
own loop object).

Every dependency is patched surgically on the ``loop_recovery`` module namespace
(never on the real ``asyncio``/``time``/``sentry_sdk`` modules) so nothing leaks
into pytest's own event loop or clock.
"""
import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bot.utils import loop_recovery
from bot.utils.loop_recovery import recover_loop


@pytest.fixture(autouse=True)
def _clear_failures():
    """``recover_loop`` keys backoff state in a module global; reset it per test
    so counters never bleed across cases."""
    loop_recovery._failures.clear()
    yield
    loop_recovery._failures.clear()


@pytest.fixture
def harness(monkeypatch):
    """Patch capture/recover/sleep/clock on the loop_recovery namespace and record
    a global call order plus the delays actually slept. ``now['t']`` is the fake
    monotonic clock — tests advance it to exercise the reset window."""
    events = []
    slept = []
    now = {'t': 0.0}

    async def fake_sleep(delay):
        events.append('sleep')
        slept.append(delay)

    monkeypatch.setattr(loop_recovery, 'sentry_sdk',
                        SimpleNamespace(capture_exception=lambda exc: events.append('capture')))
    monkeypatch.setattr(loop_recovery, 'recover_session',
                        lambda: events.append('recover'))
    monkeypatch.setattr(loop_recovery, 'asyncio', SimpleNamespace(sleep=fake_sleep))
    monkeypatch.setattr(loop_recovery, 'time',
                        SimpleNamespace(monotonic=lambda: now['t']))

    def make_loop(name):
        # Fake nextcord Loop: recover_loop only reads .coro.__name__ and .restart().
        return SimpleNamespace(
            coro=SimpleNamespace(__name__=name),
            restart=Mock(side_effect=lambda: events.append('restart')))

    return SimpleNamespace(events=events, slept=slept, now=now, make_loop=make_loop)


async def test_call_order_capture_recover_sleep_restart(harness):
    loop = harness.make_loop('loop_a')

    await recover_loop(loop, RuntimeError('boom'))

    assert harness.events == ['capture', 'recover', 'sleep', 'restart']
    assert harness.slept == [60]
    loop.restart.assert_called_once_with()


async def test_recover_runs_before_sleep(harness):
    # The whole point of the helper: the session is restored BEFORE we wait or
    # restart, so the next iteration never touches a poisoned DB.s.
    loop = harness.make_loop('loop_a')

    await recover_loop(loop, RuntimeError('boom'))

    assert harness.events.index('recover') < harness.events.index('sleep')
    assert harness.events.index('recover') < harness.events.index('restart')


async def test_backoff_progression_caps_at_max(harness):
    # Clock never advances, so every failure lands inside _RESET_AFTER and the
    # counter climbs: 60, 120, 240, 480, then capped at _MAX_DELAY (900).
    loop = harness.make_loop('loop_b')

    for _ in range(6):
        await recover_loop(loop, RuntimeError('x'))

    assert harness.slept == [60, 120, 240, 480, 900, 900]


async def test_quiet_period_longer_than_reset_after_resets_to_base(harness):
    loop = harness.make_loop('loop_c')

    await recover_loop(loop, RuntimeError('x'))   # count 1 -> 60
    await recover_loop(loop, RuntimeError('x'))   # count 2 -> 120
    # Jump the clock past the reset window since the last failure.
    harness.now['t'] = loop_recovery._RESET_AFTER + 1
    await recover_loop(loop, RuntimeError('x'))   # gap > _RESET_AFTER -> back to 60

    assert harness.slept == [60, 120, 60]


async def test_failure_exactly_at_reset_boundary_still_backs_off(harness):
    # The window is inclusive (``<= _RESET_AFTER``): a failure landing exactly on
    # the boundary is still "recent" and keeps climbing rather than resetting.
    loop = harness.make_loop('loop_d')

    await recover_loop(loop, RuntimeError('x'))   # count 1 -> 60
    harness.now['t'] = loop_recovery._RESET_AFTER
    await recover_loop(loop, RuntimeError('x'))   # gap == _RESET_AFTER -> 120

    assert harness.slept == [60, 120]


async def test_independent_loops_track_independent_counters(harness):
    a = harness.make_loop('loop_a')
    b = harness.make_loop('loop_b')

    await recover_loop(a, RuntimeError('x'))   # a -> 60
    await recover_loop(a, RuntimeError('x'))   # a -> 120
    await recover_loop(b, RuntimeError('x'))   # b's own counter -> 60

    assert harness.slept == [60, 120, 60]


# --- the four registered cog handlers delegate to recover_loop --------------


@pytest.mark.parametrize('module_path, cls_name, handler, loop_attr', [
    ('bot.cogs.auto_delete.auto_delete_commands', 'AutoDeleteCommands',
     'check_for_stale_messages_error', 'check_for_stale_messages'),
    ('bot.cogs.image_message_delete.image_message_delete_commands',
     'ImageMessageDeleteCommands',
     'check_for_expired_messages_error', 'check_for_expired_messages'),
    ('bot.cogs.birthday.birthday_commands', 'BirthdayCommands',
     'post_birthdays_error', 'post_birthdays'),
    ('bot.cogs.anniversary.anniversary_commands', 'AnniversaryCommands',
     'post_anniversaries_error', 'post_anniversaries'),
])
async def test_cog_handler_delegates_to_recover_loop(
        monkeypatch, module_path, cls_name, handler, loop_attr):
    mod = importlib.import_module(module_path)
    calls = []

    async def fake_recover_loop(loop, exc):
        calls.append((loop, exc))

    monkeypatch.setattr(mod, 'recover_loop', fake_recover_loop)

    # The handler reads exactly one attribute off ``self`` — its own loop — and
    # passes it straight through, so a stub self with a sentinel proves the wiring.
    sentinel = object()
    stub = SimpleNamespace(**{loop_attr: sentinel})
    exc = RuntimeError('boom')

    await getattr(getattr(mod, cls_name), handler)(stub, exc)

    assert calls == [(sentinel, exc)]
