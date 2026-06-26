"""CRUD + daily/upcoming queries for anniversary entries.

Mirrors ``db/helpers/starboard_helper.py``: parameterized SQLAlchemy only (NO
f-string SQL), narrow ``except`` clauses. There is no module-scope ``Date.now()``
— ``get_todays`` takes the reference datetime from the caller (the daily loop),
and ``get_upcoming`` defaults it inside the function body only when omitted.
"""
import calendar
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import sqlalchemy as sa

from db import DB
from db.model.anniversary import Anniversary


def add(guild_id: int, user_id: int, channel_id: int, title: Optional[str],
        count_label: Optional[str], message: Optional[str], month: int, day: int,
        year: Optional[int]) -> Optional[Anniversary]:
    """Insert an entry, or return ``None`` if it collides with the
    ``(guild, user, title, month, day)`` unique guard (caught + rolled back)."""
    entry = Anniversary(
        guild_id=guild_id, user_id=user_id, channel_id=channel_id,
        title=title, count_label=count_label, message=message,
        month=month, day=day, year=year,
    )
    try:
        DB.s.add(entry)
        DB.s.commit()
        return entry
    except sa.exc.IntegrityError:
        DB.s.rollback()
        return None


def get(id: int) -> Optional[Anniversary]:
    return DB.s.first(Anniversary, id=id)


def list_for_user(guild_id: int, user_id: int) -> List[Anniversary]:
    return DB.s.execute(
        sa.select(Anniversary)
        .where(Anniversary.guild_id == guild_id)
        .where(Anniversary.user_id == user_id)
        .order_by(Anniversary.month, Anniversary.day)
    ).scalars().all()


def list_for_guild(guild_id: int) -> List[Anniversary]:
    return DB.s.execute(
        sa.select(Anniversary)
        .where(Anniversary.guild_id == guild_id)
        .order_by(Anniversary.month, Anniversary.day)
    ).scalars().all()


def update(id: int, **fields) -> Optional[Anniversary]:
    """Mutate an entry, or return ``None`` if it is missing or the edit collides
    with the ``(guild, user, title, month, day)`` unique guard (caught + rolled
    back, mirroring ``add``)."""
    entry = DB.s.first(Anniversary, id=id)
    if entry is None:
        return None
    for key, value in fields.items():
        setattr(entry, key, value)
    try:
        DB.s.commit()
        return entry
    except sa.exc.IntegrityError:
        DB.s.rollback()
        return None


def delete(id: int) -> bool:
    entry = DB.s.first(Anniversary, id=id)
    if entry is None:
        return False
    DB.s.delete(entry)
    DB.s.commit()
    return True


def get_todays(now: datetime) -> Dict[int, List[Anniversary]]:
    """Entries whose month+day match ``now``, grouped by ``guild_id``.

    ``now`` is supplied by the caller (the daily loop) — no module-scope clock.
    Decision 6: on a non-leap Feb 28, Feb 29 entries also match so they never
    slip into March.
    """
    conds = [(Anniversary.month == now.month) & (Anniversary.day == now.day)]
    if now.month == 2 and now.day == 28 and not calendar.isleap(now.year):
        conds.append((Anniversary.month == 2) & (Anniversary.day == 29))
    rows = DB.s.execute(sa.select(Anniversary).where(sa.or_(*conds))).scalars().all()
    out: Dict[int, List[Anniversary]] = {}
    for r in rows:
        out.setdefault(r.guild_id, []).append(r)
    return out


def _next_occurrence(today: date, month: int, day: int) -> Optional[date]:
    """The first occurrence of ``month/day`` on or after ``today``, this year or
    next. Feb 29 falls back to Feb 28 in non-leap years (Decision 6)."""
    for year in (today.year, today.year + 1):
        effective_day = day
        if month == 2 and day == 29 and not calendar.isleap(year):
            effective_day = 28
        try:
            candidate = date(year, month, effective_day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def get_upcoming(guild_id: int, within_days: int = 31,
                 now: Optional[datetime] = None) -> List[Anniversary]:
    """Guild entries whose next occurrence is within ``within_days`` of ``now``,
    sorted by that occurrence. Wraps year-end (a date already past this year is
    measured against next year's). ``now`` defaults to the current UTC time when
    omitted — evaluated inside the call, never at module scope."""
    if now is None:
        now = datetime.now(timezone.utc)
    today = now.date()
    upcoming: List = []
    for entry in DB.s.all(Anniversary, guild_id=guild_id):
        candidate = _next_occurrence(today, entry.month, entry.day)
        if candidate is None:
            continue
        if (candidate - today).days <= within_days:
            upcoming.append((candidate, entry))
    upcoming.sort(key=lambda pair: pair[0])
    return [entry for _, entry in upcoming]
