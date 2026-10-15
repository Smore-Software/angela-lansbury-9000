"""``db_tx`` covers unit-of-work sessions, not just ``DB.s``.

A ``DB.Session()`` commit must be visible to ``DB.s`` within the same test and
rolled back before the next one. The isolation pair relies on pytest running a
file's tests in definition order.
"""
from datetime import datetime, timezone

from db import DB
from db.model.thread_map import ThreadMap

SENTINEL_MESSAGE_ID = 990011223344556677


def _make_map(message_id):
    now = datetime.now(timezone.utc)
    return ThreadMap(guild_id=1, channel_id=2, message_id=message_id, created_by=3,
                     last_refreshed_at=now)


def test_session_commit_visible_to_scoped_session():
    with DB.Session() as session:
        session.add(_make_map(SENTINEL_MESSAGE_ID - 1))
        session.commit()

    assert DB.s.first(ThreadMap, message_id=SENTINEL_MESSAGE_ID - 1) is not None


def test_isolation_a_commits_sentinel():
    with DB.Session() as session:
        session.add(_make_map(SENTINEL_MESSAGE_ID))
        session.commit()

    assert DB.s.first(ThreadMap, message_id=SENTINEL_MESSAGE_ID) is not None


def test_isolation_b_sentinel_rolled_back():
    assert DB.s.first(ThreadMap, message_id=SENTINEL_MESSAGE_ID) is None
    with DB.Session() as session:
        assert session.query(ThreadMap).filter_by(message_id=SENTINEL_MESSAGE_ID).first() is None
