"""Tests for the anniversary daily-posting loop's testable units.

The full loop body needs a live gateway (REST member fetches, channel sends), so
those stay thin and the decisions are tested through the pure
``anniversary_utils.partition_postable`` (see ``tests/test_anniversary_utils.py``).
Here we guard the two things that can only be checked on the cog module itself:
the loop is on a real daily *schedule* (never a ``seconds=`` test interval — the
birthday cog shipped that bug), and ``_fetch_member_or_none`` swallows a departed
member into ``None`` rather than raising.
"""
from datetime import time, timezone
from types import SimpleNamespace

import nextcord
import pytest

from bot.cogs.anniversary import anniversary_commands as ac


# --- loop schedule guard ----------------------------------------------------


def test_loop_is_scheduled_at_noon_utc():
    loop = ac.AnniversaryCommands.post_anniversaries
    assert loop.time == [time(hour=12, minute=0, second=0, tzinfo=timezone.utc)]


def test_loop_has_no_interval_schedule():
    # Guard against the birthday `@tasks.loop(seconds=15)` regression: a `time=`
    # schedule leaves every interval component unset.
    loop = ac.AnniversaryCommands.post_anniversaries
    assert loop.seconds is None
    assert loop.minutes is None
    assert loop.hours is None


# --- _fetch_member_or_none --------------------------------------------------


class _FakeGuild:
    def __init__(self, *, raises=False):
        self._raises = raises
        self.fetched = []

    async def fetch_member(self, user_id):
        self.fetched.append(user_id)
        if self._raises:
            raise nextcord.NotFound(SimpleNamespace(status=404, reason='Not Found'),
                                    'Unknown Member')
        return SimpleNamespace(id=user_id)


async def test_fetch_member_or_none_returns_member_when_present():
    guild = _FakeGuild()
    member = await ac._fetch_member_or_none(guild, 42)
    assert member.id == 42
    assert guild.fetched == [42]


async def test_fetch_member_or_none_returns_none_when_departed():
    guild = _FakeGuild(raises=True)
    assert await ac._fetch_member_or_none(guild, 42) is None
