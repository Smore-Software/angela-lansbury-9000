from db.model import DB


def recover_session() -> None:
    """Restore the shared scoped session to a usable state after any failure.

    Every bot entry point (task loop error handler, event handler except block,
    application command error handler) must call this before continuing, or a
    failed transaction poisons DB.s for the whole process (see the 2026-07-10
    PendingRollbackError incident).
    """
    try:
        DB.s.rollback()
    except Exception:
        # rollback itself failed; discard the session so the scoped-session
        # registry mints a fresh one on next access
        DB.s.remove()
