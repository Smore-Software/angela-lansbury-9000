"""Phase 6 — one-off SQLite → Postgres data migration.

Copies every row out of the *old* ``bumper-db.sqlite`` (which still carries the
pre-migration schema: a CSV ``excluded_channels`` column and a composite
birthday PK) into the *new* Postgres schema built in Phase 5, transforming the
two cleaned-up structures during the copy.

Why two engines
---------------
The old SQLite file and the new Postgres database have *different* schemas, so
a single ORM cannot describe both. We therefore:

* READ the old side through a **plain, read-only SQLite engine** with raw
  ``SELECT``s — never the new ORM models, whose columns no longer match.
* WRITE the new side through the **corrected ORM models** so snowflake values
  land in ``BigInteger`` columns and the enum/junction transforms apply.

Transforms applied during the copy
-----------------------------------
* ``activity_module_settings.excluded_channels`` (a CSV string) is expanded into
  one ``activity_excluded_channel`` row per channel id (empty/None skipped).
* ``birthdays`` rows are inserted **without** an explicit ``id`` so Postgres
  assigns the surrogate key; ``(guild_id, user_id, name, month, day, year)`` are
  preserved.
* ``auto_delete_channel_config.auto_delete_type`` is emitted as its enum value.

Operational notes
-----------------
* Run with the bot **OFFLINE** (Phase 7) so no writes land mid-migration. The
  SQLite file is the rollback artifact and is opened read-only.
* The target is selected purely from ``DATABASE_URL`` — no host is hardcoded.
* The full end-to-end run against live databases is **Phase 7**; this module is
  built so the pure transform helpers (CSV split, FK ordering) are unit-tested
  without any database.

Usage::

    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/bumper \\
        python scripts/migrate_sqlite_to_postgres.py

    # Read + transform + count only, no writes, no Postgres needed:
    python scripts/migrate_sqlite_to_postgres.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Iterable, Iterator, Mapping, Sequence

# When this file is run directly (``python scripts/migrate_sqlite_to_postgres.py``)
# the interpreter puts ``scripts/`` on ``sys.path`` instead of the repo root, so
# ``import db`` would fail. Add the repo root explicitly; harmless when it is
# already importable (running as a module or under pytest).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy import create_engine, text

from db import DB
from db.model.activity_excluded_channel import ActivityExcludedChannel
from db.model.activity_module_settings import ActivityModuleSettings
from db.model.activity_role import ActivityRole
from db.model.auto_delete_channel_config import AutoDeleteChannelConfig, AutoDeleteType
from db.model.birthday import Birthday
from db.model.guild_config import GuildConfig
from db.model.image_message_to_delete import ImageMessageToDelete
from db.model.polls import PollChoice, PollQuestion, PollResponse
from db.model.rolling_message_log import RollingMessageLog
from db.model.santa_participant import SantaParticipant
from db.model.starboard_config import StarboardConfig
from db.model.starboard_entry import StarboardEntry
from db.model.user_activity import UserActivity
from db.model.user_channel_settings import UserChannelSettings
from db.model.user_settings import UserSettings

# ---------------------------------------------------------------------------
# Pure transform helpers (no database — unit-tested in isolation)
# ---------------------------------------------------------------------------


def split_excluded_channels(csv: object) -> list[int]:
    """Split an ``excluded_channels`` CSV string into a list of channel ids.

    The old schema stored the excluded-channel set as a single comma-separated
    string (or ``NULL``). Empty, ``None`` and whitespace-only entries are
    skipped so the junction table never gets a meaningless row.

    >>> split_excluded_channels('1,2,3')
    [1, 2, 3]
    >>> split_excluded_channels(None)
    []
    >>> split_excluded_channels(' 7 , , 8 ')
    [7, 8]
    """
    if csv is None:
        return []
    out: list[int] = []
    for part in str(csv).split(','):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


# child table -> the parent tables that must be copied before it, so FK
# references (and surrogate-key parents) always exist when the child is written.
FK_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    'starboard_entry': ('starboard_config',),
    'poll_choice': ('poll_question',),
    'poll_response': ('poll_choice',),
    'activity_excluded_channel': ('activity_module_settings',),
}

# Every table persisted by the new schema, in source-declaration order. The
# FK-safe copy order is derived from this plus FK_DEPENDENCIES — never hand
# ordered, so a new dependency can't silently break the sequence.
TARGET_TABLES: tuple[str, ...] = (
    'activity_module_settings',
    'activity_excluded_channel',
    'activity_role',
    'auto_delete_channel_config',
    'birthdays',
    'guild_config',
    'image_message_to_delete',
    'poll_question',
    'poll_choice',
    'poll_response',
    'rolling_message_log',
    'santa_participant',
    'starboard_config',
    'starboard_entry',
    'user_activity',
    'user_channel_settings',
    'user_settings',
)


def fk_safe_order(
    tables: Sequence[str],
    dependencies: Mapping[str, Sequence[str]],
) -> list[str]:
    """Order ``tables`` so every parent precedes its children (topological sort).

    Stable: independent tables keep their input order, which keeps the copy
    sequence readable and deterministic. Raises ``ValueError`` on a dependency
    cycle. Dependencies on tables outside ``tables`` are ignored (treated as
    already satisfied).
    """
    table_set = set(tables)
    remaining = list(tables)
    placed: set[str] = set()
    ordered: list[str] = []
    while remaining:
        progressed = False
        for table in list(remaining):
            parents = dependencies.get(table, ())
            if all(p in placed or p not in table_set for p in parents):
                ordered.append(table)
                placed.add(table)
                remaining.remove(table)
                progressed = True
        if not progressed:
            raise ValueError(f'FK dependency cycle or missing parent among: {remaining}')
    return ordered


# The canonical, FK-safe copy order. parents always precede their children.
TABLE_COPY_ORDER: list[str] = fk_safe_order(TARGET_TABLES, FK_DEPENDENCIES)


# ---------------------------------------------------------------------------
# Database I/O (kept strictly separate from the pure helpers above)
# ---------------------------------------------------------------------------

# Each target table maps to the ORM model it is written through. The derived
# ``activity_excluded_channel`` table has no same-named source; it is built from
# ``activity_module_settings`` rows, so it is handled by a dedicated builder.
MODEL_BY_TABLE: dict[str, type] = {
    'activity_module_settings': ActivityModuleSettings,
    'activity_excluded_channel': ActivityExcludedChannel,
    'activity_role': ActivityRole,
    'auto_delete_channel_config': AutoDeleteChannelConfig,
    'birthdays': Birthday,
    'guild_config': GuildConfig,
    'image_message_to_delete': ImageMessageToDelete,
    'poll_question': PollQuestion,
    'poll_choice': PollChoice,
    'poll_response': PollResponse,
    'rolling_message_log': RollingMessageLog,
    'santa_participant': SantaParticipant,
    'starboard_config': StarboardConfig,
    'starboard_entry': StarboardEntry,
    'user_activity': UserActivity,
    'user_channel_settings': UserChannelSettings,
    'user_settings': UserSettings,
}


def open_source_engine(sqlite_path: str):
    """Open the old SQLite file **read-only** (it is the rollback artifact).

    Uses SQLite's URI ``mode=ro`` so the migration can never mutate the source,
    even accidentally.
    """
    # ``uri=true`` tells SQLAlchemy's pysqlite dialect to treat the database as a
    # SQLite URI, which is what makes ``mode=ro`` (read-only) take effect.
    return create_engine(f'sqlite:///file:{os.path.abspath(sqlite_path)}?mode=ro&uri=true')


def _project(model: type, row: Mapping) -> dict:
    """Pick just the source columns that exist on ``model``.

    Drops columns the new schema removed (e.g. the CSV ``excluded_channels``)
    and naturally omits ``birthdays.id`` (absent in the source) so Postgres
    assigns the surrogate key.
    """
    columns = {c.name for c in model.__table__.columns}
    return {key: row[key] for key in row.keys() if key in columns}


def _build_default(table: str) -> Callable[[object], Iterator]:
    """A 1:1 column-projecting builder for tables with no structural change."""
    model = MODEL_BY_TABLE[table]

    def build(src_conn) -> Iterator:
        for row in src_conn.execute(text(f'SELECT * FROM {table}')).mappings():
            yield model(**_project(model, row))

    return build


def _build_activity_excluded_channels(src_conn) -> Iterator:
    """Expand each ``excluded_channels`` CSV into one junction row per channel."""
    rows = src_conn.execute(
        text('SELECT guild_id, excluded_channels FROM activity_module_settings')
    ).mappings()
    for row in rows:
        for channel_id in split_excluded_channels(row['excluded_channels']):
            yield ActivityExcludedChannel(guild_id=row['guild_id'], channel_id=channel_id)


def _build_auto_delete(src_conn) -> Iterator:
    """Copy auto-delete config, coercing the stored string to its enum value."""
    for row in src_conn.execute(text('SELECT * FROM auto_delete_channel_config')).mappings():
        data = _project(AutoDeleteChannelConfig, row)
        data['auto_delete_type'] = AutoDeleteType(row['auto_delete_type'])
        yield AutoDeleteChannelConfig(**data)


def builder_for(table: str) -> Callable[[object], Iterator]:
    """Return the row builder for ``table`` (special-cased where it transforms)."""
    if table == 'activity_excluded_channel':
        return _build_activity_excluded_channels
    if table == 'auto_delete_channel_config':
        return _build_auto_delete
    return _build_default(table)


def _write(instances: Iterable, batch_size: int) -> int:
    """Insert ORM instances through the new models in committed batches."""
    total = 0
    batch: list = []
    for obj in instances:
        batch.append(obj)
        if len(batch) >= batch_size:
            DB.s.add_all(batch)
            DB.s.commit()
            total += len(batch)
            batch = []
    if batch:
        DB.s.add_all(batch)
        DB.s.commit()
        total += len(batch)
    return total


# Tables whose surrogate ``id`` we copy explicitly from the source. After such
# inserts a Postgres identity sequence still points at 1, so the first new row
# the bot writes would collide. We bump each sequence past its current max once
# the copy is done. (``birthdays`` is absent: its id is DB-assigned, so its
# sequence advances naturally.)
_EXPLICIT_ID_TABLES: tuple[str, ...] = (
    'starboard_config',
    'starboard_entry',
    'poll_question',
    'poll_choice',
)


def reset_postgres_sequences(echo: Callable[[str], None]) -> None:
    """Advance identity sequences past the max copied id (Postgres only).

    No-op on SQLite (rowid autoincrement has no separate sequence), so the
    test-harness path is unaffected. Exercised end-to-end in Phase 7.
    """
    if DB.engine.dialect.name != 'postgresql':
        return
    with DB.engine.begin() as conn:
        for table in _EXPLICIT_ID_TABLES:
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + table + "), 1))"
                ),
                {'t': table},
            )
    echo(f'Reset identity sequences for: {", ".join(_EXPLICIT_ID_TABLES)}')


def run_migration(
    sqlite_path: str,
    batch_size: int = 1000,
    dry_run: bool = False,
    echo: Callable[[str], None] = print,
) -> dict[str, int]:
    """Copy every table in FK-safe order and return per-table row counts.

    ``dry_run`` reads and transforms every row (validating the source + the
    transforms) but writes nothing and needs no Postgres.
    """
    src_engine = open_source_engine(sqlite_path)
    counts: dict[str, int] = {}
    try:
        with src_engine.connect() as src_conn:
            for table in TABLE_COPY_ORDER:
                build = builder_for(table)
                if dry_run:
                    counts[table] = sum(1 for _ in build(src_conn))
                else:
                    counts[table] = _write(build(src_conn), batch_size)
                echo(f'  {table}: {counts[table]} rows')
    finally:
        src_engine.dispose()

    if not dry_run:
        reset_postgres_sequences(echo)

    echo('')
    echo('=== Per-table row counts ===')
    for table in TABLE_COPY_ORDER:
        echo(f'  {table:<28} {counts[table]:>8}')
    echo(f'  {"TOTAL":<28} {sum(counts.values()):>8}')
    return counts


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Copy data from the old bumper-db.sqlite into the new Postgres schema.',
    )
    parser.add_argument(
        '--sqlite',
        default='bumper-db.sqlite',
        help='Path to the old SQLite file (opened read-only). Default: bumper-db.sqlite',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='Rows per insert/commit batch. Default: 1000',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Read + transform + count only; write nothing (no Postgres needed).',
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    target = 'DRY RUN (no writes)' if args.dry_run else os.environ.get('DATABASE_URL', '(DATABASE_URL unset)')
    print(f'Source SQLite: {args.sqlite}')
    print(f'Target:        {target}')
    print('Copying tables in FK-safe order...')
    run_migration(args.sqlite, batch_size=args.batch_size, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
