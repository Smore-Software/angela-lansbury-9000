"""Phase 6 — unit tests for the pure transform helpers of the migration script.

Only the database-free logic is tested here (CSV expansion + FK-safe ordering);
the full SQLite → Postgres copy is validated end-to-end in Phase 7 against live
databases. Importing the module is itself part of the contract: it must not
require a live database connection.
"""
import pytest

from scripts.migrate_sqlite_to_postgres import (
    FK_DEPENDENCIES,
    TABLE_COPY_ORDER,
    TARGET_TABLES,
    fk_safe_order,
    split_excluded_channels,
)


# --- CSV split --------------------------------------------------------------


@pytest.mark.parametrize(
    'value, expected',
    [
        ('1234,5678', [1234, 5678]),          # normal multi
        ('42', [42]),                          # single
        ('', []),                              # empty string
        (None, []),                            # NULL column
        ('   ', []),                           # whitespace only
        (' 7 , 8 , 9 ', [7, 8, 9]),            # surrounding whitespace trimmed
        ('1,,2', [1, 2]),                      # empty element between ids
        (',5,', [5]),                          # leading/trailing separators
        (1234, [1234]),                        # non-string scalar coerced
    ],
)
def test_split_excluded_channels(value, expected):
    assert split_excluded_channels(value) == expected


def test_split_excluded_channels_returns_ints():
    result = split_excluded_channels('1,2,3')
    assert all(isinstance(x, int) for x in result)


# --- FK-safe ordering -------------------------------------------------------


def test_every_parent_precedes_its_children():
    """For each declared dependency, the parent is copied before the child."""
    position = {table: i for i, table in enumerate(TABLE_COPY_ORDER)}
    for child, parents in FK_DEPENDENCIES.items():
        for parent in parents:
            assert position[parent] < position[child], (
                f'{parent} must be copied before {child}'
            )


def test_copy_order_covers_every_target_table_exactly_once():
    assert sorted(TABLE_COPY_ORDER) == sorted(TARGET_TABLES)
    assert len(TABLE_COPY_ORDER) == len(set(TABLE_COPY_ORDER))


def test_known_fk_chains_are_ordered():
    """Spot-check the chains the task calls out explicitly."""
    order = TABLE_COPY_ORDER
    assert order.index('starboard_config') < order.index('starboard_entry')
    assert order.index('poll_question') < order.index('poll_choice') < order.index('poll_response')
    assert order.index('activity_module_settings') < order.index('activity_excluded_channel')


def test_fk_safe_order_is_stable_for_independent_tables():
    """Tables without dependencies keep their input order."""
    tables = ['a', 'b', 'c', 'd']
    assert fk_safe_order(tables, {}) == ['a', 'b', 'c', 'd']


def test_fk_safe_order_promotes_parent_before_child():
    """A parent declared after its child is still ordered first."""
    tables = ['child', 'parent']
    assert fk_safe_order(tables, {'child': ('parent',)}) == ['parent', 'child']


def test_fk_safe_order_raises_on_cycle():
    with pytest.raises(ValueError):
        fk_safe_order(['x', 'y'], {'x': ('y',), 'y': ('x',)})


def test_fk_safe_order_ignores_dependency_outside_table_set():
    """A dependency on a table not being copied is treated as satisfied."""
    assert fk_safe_order(['only'], {'only': ('absent',)}) == ['only']
