"""Pure presentation helpers for the anniversary cog — parsing, ordinals, and
embed builders with zero Discord interaction objects.

Everything here takes plain values (or a duck-typed ``entry`` exposing the
``Anniversary`` model's columns) and returns plain values or a freshly built
``nextcord.Embed``, so it is unit-testable without a live gateway — the same shape
as starboard's ``board_summary`` / ``build_list_embeds`` module-level builders.

Tone is deliberate: this feature holds both joyful anniversaries and the
remembrance of loss, so ``post_embed`` carries NO gif and NO "happy" — the
submitter's own ``title``/``message`` carry the tone. Mention rendering and the
channel/member guards live in the cog (Phase 3) and loop (Phase 4), where the live
objects exist.
"""
import calendar
import re

import nextcord

from bot.utils import messages

# Separators tolerated between month and day in `MM/DD` input: '/', '-', '.'.
_DATE_SEPARATORS = re.compile(r'[/.\-]')
# Greatest valid day per month. February is 29 so a leap-day entry can be stored;
# the Feb 28 fallback in non-leap years lives in Phase 1's ``get_todays``.
_MAX_DAY = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
            7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
# Entries per page in the list/upcoming embeds (one Markdown line each). The
# paginator adds ◀️/▶️ buttons only when there is more than one page.
_LIST_ENTRIES_PER_PAGE = 10


def parse_month_day(raw: str) -> tuple[int, int]:
    """Parse ``MM/DD`` (or ``M/D``) into a validated ``(month, day)`` pair.

    Tolerates ``-`` and ``.`` as separators alongside ``/``. Validates
    ``1 <= month <= 12`` and that the day is in range for that month, accepting
    ``2/29`` so a leap-day entry can be stored. Raises ``ValueError`` on anything
    that does not parse to exactly two integers or falls out of range, so the cog
    can surface a friendly error instead of persisting garbage.
    """
    if raw is None:
        raise ValueError('Enter a date as MM/DD.')
    parts = _DATE_SEPARATORS.split(raw.strip())
    if len(parts) != 2:
        raise ValueError(f'`{raw}` is not a valid date — use MM/DD.')
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f'`{raw}` is not a valid date — use MM/DD.')
    if not 1 <= month <= 12:
        raise ValueError(f'`{month}` is not a valid month.')
    if not 1 <= day <= _MAX_DAY[month]:
        raise ValueError(f'`{day}` is not a valid day for month {month}.')
    return month, day


def parse_year(raw: str | None) -> int | None:
    """Parse an optional 4-digit year that is not in the future.

    Blank/``None`` input yields ``None`` (the year is optional and only drives the
    quiet "Nth" count). Anything else must be a 4-digit year no later than the
    current one; a non-number, a 2-digit year like ``"19"``, or a future year
    raises ``ValueError``.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        year = int(stripped)
    except ValueError:
        raise ValueError(f'`{raw}` is not a valid year — use YYYY.')
    if not 1000 <= year <= 9999:
        raise ValueError(f'`{raw}` is not a valid year — use YYYY.')
    if year > nextcord.utils.utcnow().year:
        raise ValueError('That year is in the future.')
    return year


def ordinal(n: int) -> str:
    """Render ``n`` with its English ordinal suffix: ``1 -> "1st"``, ``2 -> "2nd"``,
    ``3 -> "3rd"``, ``11 -> "11th"``, ``21 -> "21st"``. The 11–13 teens always take
    ``th`` regardless of their last digit."""
    if 11 <= n % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def anniversary_count(year: int | None, current_year: int) -> int | None:
    """The "Nth" count to show, or ``None`` to suppress it.

    Returns ``None`` when no year was recorded or when the count would be ``0`` or
    negative (the year-of and any future-dated year), so the footer never reads a
    meaningless "0th"."""
    if year is None:
        return None
    count = current_year - year
    if count < 1:
        return None
    return count


def title_or_default(title) -> str:
    """The entry's heading, falling back to "Anniversary" when blank/whitespace."""
    return (title or '').strip() or 'Anniversary'


def label_or_default(label) -> str:
    """The word after the ordinal (e.g. "3rd **Anniversary**"), falling back to
    "Anniversary" when blank/whitespace."""
    return (label or '').strip() or 'Anniversary'


def post_embed(entry, current_year: int) -> nextcord.Embed:
    """Build the gentle remembrance embed posted on the day.

    The title is the entry's heading (or "Anniversary"); the body is the
    submitter's message rendered italic, or omitted when blank. The footer always
    carries the ``Month Day`` and, only when a recorded year yields a positive
    count, is prefixed with ``{ordinal} {label} · `` — e.g. ``3rd Anniversary ·
    June 25``. Deliberately carries NO gif and NO "happy": the tone is the
    submitter's to set."""
    message = (entry.message or '').strip()
    embed = nextcord.Embed(
        color=messages.INFO_COLOR,
        title=title_or_default(entry.title),
        description=f'*{message}*' if message else None,
    )
    footer = f'{calendar.month_name[entry.month]} {entry.day}'
    count = anniversary_count(entry.year, current_year)
    if count is not None:
        footer = f'{ordinal(count)} {label_or_default(entry.count_label)} · {footer}'
    embed.set_footer(text=footer)
    return embed


def build_list_embeds(entries, *, guild, title) -> list[nextcord.Embed]:
    """Build one embed per page of ``entries`` as a Markdown list, chunked at
    ``_LIST_ENTRIES_PER_PAGE`` each. Each line is ``heading · MM/DD · <#channel> ·
    year`` (the year only when set). Reused by ``/anniversary list`` and
    ``/anniversary upcoming``.

    A FRESH embed is built per page and never aliased: ``EmbedPaginatorView``
    stamps the footer onto whatever embed it is handed, so a shared embed would
    have its footer clobbered across pages. ``guild`` is accepted for parity with
    the cog's call site (channel references render as raw ``<#id>`` mentions, which
    need no resolution)."""
    embeds = []
    for start in range(0, len(entries), _LIST_ENTRIES_PER_PAGE):
        chunk = entries[start:start + _LIST_ENTRIES_PER_PAGE]
        lines = []
        for entry in chunk:
            parts = [title_or_default(entry.title),
                     f'{entry.month:02d}/{entry.day:02d}',
                     f'<#{entry.channel_id}>']
            if entry.year:
                parts.append(str(entry.year))
            lines.append(' · '.join(parts))
        embeds.append(nextcord.Embed(color=messages.INFO_COLOR, title=title,
                                     description='\n'.join(lines)))
    return embeds
