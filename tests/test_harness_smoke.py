"""Smoke tests proving the harness itself works:

(a) the active DB is the throwaway temp file, not prod ``bumper-db.sqlite``;
(b) a trivial model insert + query round-trips inside the rollback transaction;
(c) an ``async def`` test executes (proves ``asyncio_mode = auto``).
"""
from db import DB
from db.model.birthday import Birthday

from tests.conftest import make_emoji, make_payload, make_message


def test_uses_temp_db_not_prod():
    url = str(DB.engine.url)
    assert 'bumper-db.sqlite' not in url, f'tests are pointed at the prod DB: {url}'
    assert url.endswith('.sqlite')


def test_model_insert_and_query_round_trip():
    DB.s.add(Birthday(guild_id=1, user_id=2, name='Smoke', month=6, day=23, year=1990))
    DB.s.commit()

    row = DB.s.first(Birthday, guild_id=1, user_id=2, name='Smoke')
    assert row is not None
    assert row.month == 6
    assert row.day == 23


def test_rollback_isolates_tests():
    # The row from the previous test must not be visible here — each test runs
    # inside its own savepoint that rolls back on teardown.
    assert DB.s.first(Birthday, guild_id=1, user_id=2, name='Smoke') is None


async def test_async_test_runs():
    # If asyncio_mode were not 'auto', pytest would skip/error this coroutine
    # instead of awaiting it.
    assert True


def test_discord_fakes_render():
    unicode_emoji = make_emoji('⭐')
    assert str(unicode_emoji) == '⭐'
    assert unicode_emoji.is_custom_emoji() is False

    custom = make_emoji('blobcat', id=123)
    assert str(custom) == '<:blobcat:123>'
    assert custom.is_custom_emoji() is True

    animated = make_emoji('party', id=456, animated=True)
    assert str(animated) == '<a:party:456>'

    payload = make_payload(emoji=custom, channel_id=10, message_id=20, guild_id=30)
    assert payload.emoji is custom
    assert payload.guild_id == 30

    msg = make_message(reactions=[(custom, 7)], content='hi')
    assert msg.content == 'hi'
    assert msg.reactions[0].emoji is custom
    assert msg.reactions[0].count == 7
