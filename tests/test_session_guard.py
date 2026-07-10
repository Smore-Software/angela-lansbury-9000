"""Tests for db/session_guard.py — the single recovery primitive that restores
the process-global scoped session (``DB.s``) after a failed transaction.

Real temp-sqlite engine + the autouse savepoint rollback in ``conftest.py``.

CRITICAL: never trigger the real ``DB.s.remove()``. The conftest ``db_tx``
fixture rebinds the scoped-session registry via ``DB.test_transaction`` and a
real ``remove()`` discards that savepoint-bound session, breaking isolation for
every subsequent test. The fallback branch is exercised against a stub only.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from db import DB
from db.model.guild_config import GuildConfig
from db import session_guard
from db.session_guard import recover_session


def test_recover_session_unpoisons_after_integrity_error():
    """A duplicate-PK insert poisons DB.s; recover_session() restores it."""
    DB.s.add(GuildConfig(guild_id=777))
    DB.s.commit()

    # Second row with the same primary key violates the unique constraint.
    with pytest.raises(IntegrityError):
        DB.s.add(GuildConfig(guild_id=777))
        DB.s.commit()

    # The failed transaction poisons the session: any further use raises until
    # someone rolls back.
    with pytest.raises(PendingRollbackError):
        DB.s.query(GuildConfig).all()

    recover_session()

    # After recovery the session is usable again and sees the committed row.
    rows = DB.s.query(GuildConfig).filter_by(guild_id=777).all()
    assert len(rows) == 1


def test_recover_session_falls_back_to_remove_when_rollback_raises(monkeypatch):
    """If rollback() itself raises, recover_session() discards the session via
    remove(). Exercised against a stub so the real registry is never touched."""
    calls = []
    stub_session = SimpleNamespace(
        rollback=lambda: (_ for _ in ()).throw(RuntimeError('rollback boom')),
        remove=lambda: calls.append('remove'),
    )
    monkeypatch.setattr(session_guard, 'DB', SimpleNamespace(s=stub_session))

    recover_session()

    assert calls == ['remove']
