from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from db import DB
from db.helpers import thread_map_helper as h
from db.model.thread_map import ThreadMap, ThreadMapExcludedChannel, ThreadMapPage, ThreadMapRole

REFRESHED = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


@pytest.fixture
def session():
    with DB.Session() as s:
        yield s


def _create(session, message_id=5000, role_ids=(), pages=('p0', 'p1')):
    return h.create_map(session, guild_id=10, channel_id=20, message_id=message_id, created_by=30,
                        role_ids=role_ids, pages=list(pages), refreshed_at=REFRESHED)


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_create_get_round_trip(session):
    map_id = _create(session, role_ids=[100, 101, 100])
    session.commit()

    by_id = h.get_config(session, map_id)
    assert by_id == h.get_config_by_message(session, 5000)
    assert by_id == h.MapConfig(
        id=map_id, guild_id=10, channel_id=20, message_id=5000,
        role_ids=frozenset({100, 101}), excluded_ids=frozenset(),
        current_page=0, page_count=2, last_refreshed_at=REFRESHED,
    )
    assert h.get_page(session, map_id, 0) == 'p0'
    assert h.get_page(session, map_id, 1) == 'p1'
    assert h.get_page(session, map_id, 2) is None


def test_missing_lookups_return_none(session):
    assert h.get_config(session, 999) is None
    assert h.get_config_by_message(session, 999) is None
    assert h.delete_map(session, 999) is False
    assert h.delete_by_message(session, 999) is None


def test_get_config_returns_aware_utc(session):
    map_id = _create(session)
    session.commit()
    session.expire_all()  # force a reload from sqlite, which strips tzinfo

    refreshed = h.get_config(session, map_id).last_refreshed_at
    assert refreshed.tzinfo is not None
    assert refreshed.utcoffset() == timedelta(0)
    assert refreshed == REFRESHED


def test_set_current_page(session):
    map_id = _create(session)
    h.set_current_page(session, map_id, 1)
    session.commit()
    assert h.get_config(session, map_id).current_page == 1


def test_replace_pages_replaces_and_resets_page(session):
    map_id = _create(session, pages=['a', 'b', 'c'])
    h.set_current_page(session, map_id, 2)
    session.commit()

    later = REFRESHED + timedelta(minutes=5)
    h.replace_pages(session, map_id, ['x', 'y'], later)
    session.commit()
    session.expire_all()

    config = h.get_config(session, map_id)
    assert config.page_count == 2
    assert config.current_page == 0
    assert config.last_refreshed_at == later
    assert [h.get_page(session, map_id, i) for i in range(3)] == ['x', 'y', None]
    assert _count(session, ThreadMapPage) == 2


def test_role_cap(session):
    map_id = _create(session, role_ids=range(1, 25))
    added, rejected = h.add_roles(session, map_id, [500, 501, 502])
    assert added == [500]
    assert rejected == [501, 502]
    assert len(h.get_config(session, map_id).role_ids) == h.ROLE_CAP


def test_duplicate_role_add_skipped(session):
    map_id = _create(session, role_ids=[100])
    added, rejected = h.add_roles(session, map_id, [100, 101, 101])
    assert (added, rejected) == ([101], [])
    session.commit()
    assert h.get_config(session, map_id).role_ids == {100, 101}


def test_remove_roles(session):
    map_id = _create(session, role_ids=[100, 101])
    h.remove_roles(session, map_id, [100, 999])
    session.commit()
    assert h.get_config(session, map_id).role_ids == {101}
    assert _count(session, ThreadMapRole) == 1


def test_duplicate_exclusion_add_skipped(session):
    map_id = _create(session)
    assert h.add_exclusions(session, map_id, [7, 8]) == [7, 8]
    assert h.add_exclusions(session, map_id, [8, 9, 9]) == [9]
    session.commit()
    assert h.get_config(session, map_id).excluded_ids == {7, 8, 9}


def test_remove_exclusions(session):
    map_id = _create(session)
    h.add_exclusions(session, map_id, [7, 8])
    assert h.remove_exclusions(session, map_id, [7]) == [7]
    assert h.remove_exclusions(session, map_id, [42]) == []
    session.commit()
    assert h.get_config(session, map_id).excluded_ids == {8}
    assert _count(session, ThreadMapExcludedChannel) == 1


def test_delete_map_cascades(session):
    map_id = _create(session, role_ids=[100])
    h.add_exclusions(session, map_id, [7])
    session.commit()

    assert h.delete_map(session, map_id) is True
    session.commit()
    for model in (ThreadMap, ThreadMapRole, ThreadMapExcludedChannel, ThreadMapPage):
        assert _count(session, model) == 0


def test_delete_by_message_returns_map_id(session):
    map_id = _create(session, message_id=6000, role_ids=[100])
    session.commit()

    assert h.delete_by_message(session, 6000) == map_id
    session.commit()
    assert h.get_config(session, map_id) is None
    assert _count(session, ThreadMapRole) == 0


def test_helpers_never_commit(session):
    map_id = _create(session, role_ids=[100])
    h.add_roles(session, map_id, [101])
    h.add_exclusions(session, map_id, [7])
    h.replace_pages(session, map_id, ['z'], REFRESHED)
    session.rollback()

    for model in (ThreadMap, ThreadMapRole, ThreadMapExcludedChannel, ThreadMapPage):
        assert _count(session, model) == 0
    session.close()  # end its savepoint before DB.s opens one on the same connection
    assert DB.s.first(ThreadMap, message_id=5000) is None
