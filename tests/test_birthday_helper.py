"""Tests for db/helpers/birthday_helper.py.

Phase 4 of the SQLite->Postgres migration replaced the ``birthdays`` composite
PK ``(guild_id, user_id, name)`` with a surrogate autoincrement ``id`` plus a
``UniqueConstraint('guild_id', 'user_id', 'name')``. These tests pin the
no-duplicate guarantee (now enforced by the unique constraint rather than the
PK) and the basic add/list/delete behaviour through the public helpers.

``birthday_helper`` has no module-level cache, so no cache-clear fixture is
needed — the autouse savepoint rollback in conftest isolates each test.
"""
from db.helpers import birthday_helper


GUILD = 100
USER = 200


def test_duplicate_birthday_returns_false():
    # The unique constraint stands in for the old composite PK.
    assert birthday_helper.add_birthday(GUILD, USER, 'Alice', 4, 1, 1990) is True
    assert birthday_helper.add_birthday(GUILD, USER, 'Alice', 4, 1, 1990) is False
    rows = birthday_helper.list_birthdays(GUILD, USER)
    assert len(rows) == 1


def test_different_names_for_one_user_coexist():
    assert birthday_helper.add_birthday(GUILD, USER, 'Alice', 4, 1, 1990) is True
    assert birthday_helper.add_birthday(GUILD, USER, 'Bob', 5, 2, 1991) is True
    rows = birthday_helper.list_birthdays(GUILD, USER)
    names = {row.Birthday.name for row in rows}
    assert names == {'Alice', 'Bob'}


def test_delete_by_name_removes_exactly_one_row():
    birthday_helper.add_birthday(GUILD, USER, 'Alice', 4, 1, 1990)
    birthday_helper.add_birthday(GUILD, USER, 'Bob', 5, 2, 1991)

    assert birthday_helper.delete_birthday(GUILD, USER, 'Alice') is True

    rows = birthday_helper.list_birthdays(GUILD, USER)
    assert len(rows) == 1
    assert rows[0].Birthday.name == 'Bob'


def test_list_birthdays_returns_users_rows():
    # Rows for another user in the same guild must not leak in.
    birthday_helper.add_birthday(GUILD, USER, 'Alice', 4, 1, 1990)
    birthday_helper.add_birthday(GUILD, USER, 'Bob', 5, 2, 1991)
    birthday_helper.add_birthday(GUILD, 999, 'Carol', 6, 3, 1992)

    rows = birthday_helper.list_birthdays(GUILD, USER)
    assert len(rows) == 2
    assert all(row.Birthday.guild_id == GUILD and row.Birthday.user_id == USER
               for row in rows)
