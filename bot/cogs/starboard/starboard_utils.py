"""Pure starboard helpers with no Discord/DB I/O, tested in isolation.

``reaction_count`` extracts a board's live reaction tally from a (usually cached)
message; the reaction handler reads counts from here rather than from the gateway
payload, which carries no count.
"""
from db.helpers import starboard_helper


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
