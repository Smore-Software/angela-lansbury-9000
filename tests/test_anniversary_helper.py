"""Tests for db/helpers/anniversary_helper.py — entry CRUD, the unique guard,
today-matching (incl. the Feb 29 -> Feb 28 leap-year fallback), and the
upcoming-window query (incl. year-end wrap).

No mocks: real temp-sqlite engine + the autouse savepoint rollback in
``conftest.py`` isolates each test. ``anniversary_helper`` has no module-level
cache, so no cache-clear fixture is needed.
"""
from datetime import datetime

from db.helpers import anniversary_helper


GUILD = 100
OTHER_GUILD = 101
USER = 200
OTHER_USER = 201
CHANNEL = 300


def _add(guild=GUILD, user=USER, channel=CHANNEL, title='Wedding',
         count_label='Anniversary', message='note', month=6, day=25, year=2020):
    return anniversary_helper.add(guild, user, channel, title, count_label,
                                  message, month, day, year)


# --- add + unique guard -----------------------------------------------------


def test_add_returns_row():
    entry = _add()
    assert entry is not None
    assert entry.id is not None
    assert entry.guild_id == GUILD
    assert entry.title == 'Wedding'
    assert entry.month == 6 and entry.day == 25 and entry.year == 2020


def test_duplicate_add_returns_none():
    first = _add(title='Wedding', month=6, day=25)
    assert first is not None
    # Same (guild, user, title, month, day) -> IntegrityError caught -> None.
    dup = _add(title='Wedding', month=6, day=25, message='different body')
    assert dup is None
    # The rollback left exactly the first row, and the session is still usable.
    assert len(anniversary_helper.list_for_user(GUILD, USER)) == 1


def test_add_differing_title_coexists():
    assert _add(title='Wedding', month=6, day=25) is not None
    assert _add(title='Engagement', month=6, day=25) is not None
    assert len(anniversary_helper.list_for_user(GUILD, USER)) == 2


# --- get / list scoping -----------------------------------------------------


def test_get_returns_entry_and_missing_is_none():
    entry = _add()
    assert anniversary_helper.get(entry.id).id == entry.id
    assert anniversary_helper.get(999999) is None


def test_list_for_user_scopes_and_orders():
    _add(title='B', month=8, day=1)
    _add(title='A', month=3, day=2)
    _add(guild=GUILD, user=OTHER_USER, title='Other', month=1, day=1)
    _add(guild=OTHER_GUILD, user=USER, title='Elsewhere', month=1, day=1)

    rows = anniversary_helper.list_for_user(GUILD, USER)
    assert [r.title for r in rows] == ['A', 'B']  # ordered by month, day
    assert all(r.guild_id == GUILD and r.user_id == USER for r in rows)


def test_list_for_guild_includes_all_users_excludes_other_guild():
    _add(user=USER, title='Mine', month=2, day=2)
    _add(user=OTHER_USER, title='Theirs', month=1, day=1)
    _add(guild=OTHER_GUILD, user=USER, title='Elsewhere', month=1, day=1)

    rows = anniversary_helper.list_for_guild(GUILD)
    assert {r.title for r in rows} == {'Mine', 'Theirs'}
    assert [r.title for r in rows] == ['Theirs', 'Mine']  # Jan before Feb


# --- update / delete --------------------------------------------------------


def test_update_mutates_and_persists():
    entry = _add(title='Old', message='old body', month=6, day=25)
    updated = anniversary_helper.update(entry.id, title='New', message='new body',
                                        month=7, day=4, channel_id=999)
    assert updated is not None
    refetched = anniversary_helper.get(entry.id)
    assert refetched.title == 'New'
    assert refetched.message == 'new body'
    assert refetched.month == 7 and refetched.day == 4
    assert refetched.channel_id == 999


def test_update_missing_returns_none():
    assert anniversary_helper.update(999999, title='x') is None


def test_update_collision_returns_none_and_rolls_back():
    # Two of the same user's entries; editing one onto the other's
    # (title, month, day) trips the unique guard.
    keep = _add(title='Wedding', month=6, day=25)
    victim = _add(title='Engagement', month=3, day=2)
    assert keep is not None and victim is not None

    collided = anniversary_helper.update(victim.id, title='Wedding', month=6, day=25)
    assert collided is None
    # The rollback left the victim untouched and the session still usable.
    refetched = anniversary_helper.get(victim.id)
    assert refetched.title == 'Engagement'
    assert refetched.month == 3 and refetched.day == 2
    # Session is not stuck in a failed transaction: a subsequent add succeeds.
    survivor = _add(title='Birthday', month=1, day=1)
    assert survivor is not None
    assert len(anniversary_helper.list_for_user(GUILD, USER)) == 3


def test_delete_removes_row_and_missing_returns_false():
    entry = _add()
    assert anniversary_helper.delete(entry.id) is True
    assert anniversary_helper.get(entry.id) is None
    assert anniversary_helper.delete(entry.id) is False
    assert anniversary_helper.delete(999999) is False


# --- get_todays -------------------------------------------------------------


def test_get_todays_matches_day_and_groups_by_guild():
    a = _add(guild=GUILD, user=USER, title='G1', month=6, day=25)
    b = _add(guild=GUILD, user=OTHER_USER, title='G1b', month=6, day=25)
    c = _add(guild=OTHER_GUILD, user=USER, title='G2', month=6, day=25)
    _add(title='NotToday', month=6, day=26)  # excluded

    todays = anniversary_helper.get_todays(datetime(2021, 6, 25))
    assert set(todays.keys()) == {GUILD, OTHER_GUILD}
    assert {e.id for e in todays[GUILD]} == {a.id, b.id}
    assert {e.id for e in todays[OTHER_GUILD]} == {c.id}


def test_get_todays_feb28_nonleap_includes_feb29():
    feb29 = _add(title='Feb29', month=2, day=29)
    feb28 = _add(title='Feb28', month=2, day=28)

    # 2025 is NOT a leap year: a Feb 28 run also surfaces Feb 29 entries.
    todays = anniversary_helper.get_todays(datetime(2025, 2, 28))
    assert {e.id for e in todays[GUILD]} == {feb28.id, feb29.id}


def test_get_todays_feb28_leap_excludes_feb29():
    _add(title='Feb29', month=2, day=29)
    feb28 = _add(title='Feb28', month=2, day=28)

    # 2024 IS a leap year: Feb 29 has its own day, so Feb 28 must NOT pull it in.
    todays = anniversary_helper.get_todays(datetime(2024, 2, 28))
    assert {e.id for e in todays[GUILD]} == {feb28.id}


# --- get_upcoming -----------------------------------------------------------


def test_get_upcoming_includes_near_excludes_far():
    soon = _add(title='Soon', month=6, day=30)   # 5 days out
    far = _add(title='Far', month=9, day=1)       # ~2 months out

    now = datetime(2026, 6, 25)
    upcoming = anniversary_helper.get_upcoming(GUILD, within_days=31, now=now)
    ids = {e.id for e in upcoming}
    assert soon.id in ids
    assert far.id not in ids


def test_get_upcoming_sorted_by_next_occurrence():
    later = _add(title='Later', month=7, day=20)
    sooner = _add(title='Sooner', month=7, day=1)

    now = datetime(2026, 6, 25)
    upcoming = anniversary_helper.get_upcoming(GUILD, within_days=40, now=now)
    assert [e.title for e in upcoming] == ['Sooner', 'Later']


def test_get_upcoming_wraps_year_end():
    # "Now" is late December; an early-January entry is the next occurrence and
    # must be within a ~31-day window despite the year rollover.
    jan = _add(title='NewYear', month=1, day=3)
    now = datetime(2026, 12, 28)
    upcoming = anniversary_helper.get_upcoming(GUILD, within_days=31, now=now)
    assert jan.id in {e.id for e in upcoming}


def test_get_upcoming_scopes_to_guild():
    mine = _add(guild=GUILD, title='Mine', month=6, day=30)
    _add(guild=OTHER_GUILD, title='Theirs', month=6, day=30)

    now = datetime(2026, 6, 25)
    upcoming = anniversary_helper.get_upcoming(GUILD, within_days=31, now=now)
    assert {e.id for e in upcoming} == {mine.id}
