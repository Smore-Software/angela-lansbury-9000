"""Optional Postgres connectivity smoke test.

The default suite stays on throwaway SQLite (see ``conftest.py``). This test is a
parity check against a real Postgres and is **skipped unless** ``TEST_PG_URL`` is
set, e.g. the local Docker instance from ``docker-compose.yml``:

    TEST_PG_URL="postgresql+psycopg://postgres:postgres@localhost:5432/bumper" \
        pipenv run pytest tests/test_pg_connectivity.py

It opens its own engine (rather than the shared ``DB`` singleton, which is bound
to the temp SQLite file at import) and runs ``SELECT 1`` to prove the psycopg
driver can reach Postgres.
"""
import os

import pytest
from sqlalchemy import create_engine, text

TEST_PG_URL = os.environ.get('TEST_PG_URL')


@pytest.mark.skipif(
    not TEST_PG_URL,
    reason='TEST_PG_URL not set; skipping Postgres connectivity smoke test',
)
def test_postgres_select_1():
    engine = create_engine(TEST_PG_URL)
    try:
        with engine.connect() as conn:
            assert conn.execute(text('SELECT 1')).scalar() == 1
    finally:
        engine.dispose()
