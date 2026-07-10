"""Shared recovery for ``@tasks.loop`` error handlers.

Every registered task loop wires its ``@loop.error`` handler to
``recover_loop``: capture the exception, recover the process-global session (so
the restarted iteration gets a clean ``DB.s`` instead of a poisoned one — the
2026-07-10 ``PendingRollbackError`` incident), back off with an exponential
delay, then restart the loop.

A custom ``@.error`` handler is required to keep a nextcord loop alive after a
non-network exception — nextcord's default behavior kills the loop permanently —
so the helper must ALWAYS end with ``loop.restart()``.
"""
import asyncio
import time

import sentry_sdk

from db.session_guard import recover_session

_BASE_DELAY = 60      # load-bearing: guarantees time= loops land past the
                      # scheduled second and wait for the next occurrence
_MAX_DELAY = 900
_RESET_AFTER = 3600
_failures: dict[str, tuple[int, float]] = {}


async def recover_loop(loop, exc) -> None:
    """Standard recovery for @tasks.loop error handlers."""
    sentry_sdk.capture_exception(exc)
    recover_session()
    name = loop.coro.__name__
    count, last = _failures.get(name, (0, 0.0))
    now = time.monotonic()
    count = count + 1 if now - last <= _RESET_AFTER else 1
    _failures[name] = (count, now)
    await asyncio.sleep(min(_BASE_DELAY * 2 ** (count - 1), _MAX_DELAY))
    loop.restart()
