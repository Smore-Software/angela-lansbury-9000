"""Phase 1 — connection-pool hygiene.

The engine must check out connections with a pre-ping and retire them after 30
minutes. Prod points at the Supabase session pooler, which kills idle
connections; without these the first use of a dead connection fails mid-operation
and can poison the shared session for the rest of the process's life.

``_pre_ping``/``_recycle`` are private, but they're the only readback of what
``create_engine`` actually applied, and they're stable across SQLAlchemy 2.0.x.
"""
from db import DB


def test_engine_pool_pre_pings():
    assert DB.engine.pool._pre_ping is True


def test_engine_pool_recycles_after_30_minutes():
    assert DB.engine.pool._recycle == 1800
