"""Pure starboard helpers with no Discord/DB I/O, tested in isolation.

``reaction_count`` extracts a board's live reaction tally from a (usually cached)
message; the reaction handler reads counts from here rather than from the gateway
payload, which carries no count. ``parse_emoji_input`` turns the raw string an
admin types into ``/starboard add`` into the ``(name, id)`` pair the config stores.
``member_has_bypass_role`` answers whether a reactor carries a board's optional
bypass role.
"""
import re

from db.helpers import starboard_helper

# Matches a Discord custom-emoji mention: <:name:id> or <a:name:id> (animated).
_CUSTOM_EMOJI_RE = re.compile(r'^<a?:(\w+):(\d+)>$')


def parse_emoji_input(raw: str) -> tuple[str, int | None]:
    """Parse an emoji string into ``(name_or_char, id)``.

    A custom-emoji mention ``<:book:123>`` (or animated ``<a:book:123>``) yields
    ``('book', 123)``; any other non-empty string is treated as a unicode emoji and
    yields ``(stripped, None)``. Raises ``ValueError`` on empty/whitespace input or
    on a malformed custom-emoji tag (something that opens like a mention but does
    not fully parse), so the cog can surface a clear error instead of persisting
    garbage.
    """
    if raw is None:
        raise ValueError('No emoji provided.')
    stripped = raw.strip()
    if not stripped:
        raise ValueError('No emoji provided.')

    match = _CUSTOM_EMOJI_RE.match(stripped)
    if match:
        return match.group(1), int(match.group(2))

    # Looks like a custom-emoji mention but did not fully parse → reject rather
    # than storing the broken tag as a "unicode" emoji that can never match.
    if stripped.startswith('<') and stripped.endswith('>'):
        raise ValueError(f'`{stripped}` is not a valid emoji.')

    return stripped, None


def reaction_count(message, config) -> int:
    """Return the count of the reaction on ``message`` matching ``config``'s emoji.

    Reuses the canonical ``starboard_helper.emoji_matches`` so the handler and the
    cog agree on what counts. ``message.reactions`` holds the live tally for cached
    messages (zero REST calls). Returns ``0`` when the emoji is not present — e.g.
    the last matching reaction was just removed.
    """
    for reaction in message.reactions:
        if starboard_helper.emoji_matches(reaction.emoji, config):
            return reaction.count
    return 0


def member_has_bypass_role(member, config) -> bool:
    """True when ``config`` names a bypass role and ``member`` carries it.

    ``member`` is ``payload.member``: nextcord populates it only for REACTION_ADD
    inside a guild, built from the gateway event's own member object
    (nextcord/state.py:1298-1306), so this needs no privileged members intent —
    bot/app.py:19 does not request one. Read defensively rather than as
    ``member.roles``: the reaction-remove path always passes ``None``, and treating
    any member we cannot read roles off as simply not privileged fails closed — the
    board falls back to its ordinary threshold instead of raising on the hot path.
    """
    if config.bypass_role_id is None or member is None:
        return False
    return any(getattr(role, 'id', None) == config.bypass_role_id
               for role in getattr(member, 'roles', None) or [])
