"""Tests for db/helpers/anniversary_channel_helper.py — the per-guild registry
of channels eligible to receive anniversary posts.

No mocks: real temp-sqlite engine + the autouse savepoint rollback in
``conftest.py``. No module-level cache, so no cache-clear fixture is needed.
"""
from db.helpers import anniversary_channel_helper as helper


GUILD = 100
OTHER_GUILD = 101
CHANNEL = 300
OTHER_CHANNEL = 301


def test_add_and_get_channels():
    chan = helper.add_channel(GUILD, CHANNEL, 'Remembrances')
    assert chan is not None
    assert chan.id is not None
    channels = helper.get_channels(GUILD)
    assert len(channels) == 1
    assert channels[0].channel_id == CHANNEL
    assert channels[0].label == 'Remembrances'


def test_duplicate_channel_returns_none():
    assert helper.add_channel(GUILD, CHANNEL, 'First') is not None
    # Same (guild, channel) -> IntegrityError caught -> None, session still usable.
    assert helper.add_channel(GUILD, CHANNEL, 'Second') is None
    assert len(helper.get_channels(GUILD)) == 1


def test_same_channel_different_guild_coexists():
    assert helper.add_channel(GUILD, CHANNEL, 'A') is not None
    assert helper.add_channel(OTHER_GUILD, CHANNEL, 'B') is not None
    assert len(helper.get_channels(GUILD)) == 1
    assert len(helper.get_channels(OTHER_GUILD)) == 1


def test_get_channels_scopes_to_guild():
    helper.add_channel(GUILD, CHANNEL, 'Mine')
    helper.add_channel(OTHER_GUILD, OTHER_CHANNEL, 'Theirs')
    channels = helper.get_channels(GUILD)
    assert {c.channel_id for c in channels} == {CHANNEL}


def test_get_channel_by_id_and_missing():
    chan = helper.add_channel(GUILD, CHANNEL, 'Remembrances')
    assert helper.get_channel(chan.id).channel_id == CHANNEL
    assert helper.get_channel(999999) is None


def test_find_by_channel_id():
    helper.add_channel(GUILD, CHANNEL, 'Remembrances')
    found = helper.find_by_channel_id(GUILD, CHANNEL)
    assert found is not None
    assert found.channel_id == CHANNEL
    # Wrong guild / wrong channel -> None.
    assert helper.find_by_channel_id(OTHER_GUILD, CHANNEL) is None
    assert helper.find_by_channel_id(GUILD, OTHER_CHANNEL) is None


def test_remove_channel_and_missing_returns_false():
    chan = helper.add_channel(GUILD, CHANNEL, 'Remembrances')
    assert helper.remove_channel(chan.id) is True
    assert helper.get_channels(GUILD) == []
    assert helper.remove_channel(chan.id) is False
    assert helper.remove_channel(999999) is False
