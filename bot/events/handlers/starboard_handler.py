"""Starboard gateway-event logic: reaction add/remove and original-message
delete cleanup.

Counting is cache-first — nextcord keeps ``Reaction.count`` live in memory for
cached messages, so the common path costs zero REST calls; ``fetch_message`` runs
only on a cache miss. A board posts a message once (first threshold crossing) and
edits the live count thereafter; removals edit, they never delete. Deleting the
original removes every starboard post it fed.

Discord/REST calls (send/edit/fetch/delete) are wrapped in
``try/except (Forbidden, NotFound)`` and swallowed: a missing post or a
permissions gap is an expected consequence of admins editing/deleting boards or
posts, not a bug worth alerting on, and it must not crash the event.
"""
import nextcord

from bot.cogs.starboard.starboard_utils import reaction_count
from bot.utils import bot_utils, messages
from db.helpers import starboard_helper


def _emoji_display(config) -> str:
    """Render a config's emoji for the footer: ``<:name:id>`` for custom, the
    unicode char otherwise."""
    if config.emoji_id is not None:
        return f'<:{config.emoji}:{config.emoji_id}>'
    return config.emoji


def _matching_configs(payload):
    """Enabled configs whose emoji matches the reaction and whose target channel
    is NOT where the reaction happened (a board never restars its own posts)."""
    configs = starboard_helper.get_enabled_configs(payload.guild_id)
    return [c for c in configs
            if starboard_helper.emoji_matches(payload.emoji, c)
            and payload.channel_id != c.target_channel_id]


async def _resolve_message(bot, payload):
    """Cache-first message resolution. Returns ``None`` if the source channel or
    message is gone."""
    cached = nextcord.utils.get(bot.cached_messages, id=payload.message_id)
    if cached is not None:
        return cached
    channel = await bot_utils.get_or_fetch_channel(bot, payload.channel_id)
    if channel is None:
        return None
    try:
        return await channel.fetch_message(payload.message_id)
    except nextcord.NotFound:
        return None


async def handle_reaction_add(bot, payload):
    if payload.guild_id is None:
        return
    matches = _matching_configs(payload)
    if not matches:
        return  # common case: no matching board — no message resolution, no I/O
    message = await _resolve_message(bot, payload)
    if message is None:
        return
    source_channel = getattr(message.channel, 'name', None)
    for config in matches:
        count = reaction_count(message, config)
        await post_or_edit(bot, config, message, count, source_channel)


async def handle_reaction_remove(bot, payload):
    if payload.guild_id is None:
        return
    matches = _matching_configs(payload)
    if not matches:
        return
    message = await _resolve_message(bot, payload)
    if message is None:
        return
    source_channel = getattr(message.channel, 'name', None)
    for config in matches:
        # Only ever refresh an existing post on un-react; never post, never delete.
        if starboard_helper.get_entry(config.id, message.id) is None:
            continue
        count = reaction_count(message, config)
        await post_or_edit(bot, config, message, count, source_channel)


async def post_or_edit(bot, config, message, count, source_channel):
    """Post the message to ``config``'s target the first time it crosses the
    threshold, then edit the live count on every later change."""
    target = await bot_utils.get_or_fetch_channel(bot, config.target_channel_id)
    if target is None:
        return
    embed = messages.starboard_embed(message, _emoji_display(config), count, source_channel)
    entry = starboard_helper.get_entry(config.id, message.id)

    if entry is None:
        if count < config.threshold:
            return  # not yet eligible
        try:
            posted = await target.send(embed=embed)
        except (nextcord.Forbidden, nextcord.NotFound):
            return  # target channel gone or unpostable — admin config issue
        starboard_helper.upsert_entry(
            config_id=config.id, guild_id=config.guild_id,
            original_message_id=message.id,
            original_channel_id=message.channel.id,
            author_id=message.author.id,
            posted_message_id=posted.id, star_count=count)
        return

    # Already posted: refresh the existing repost's count in place.
    try:
        posted = await target.fetch_message(entry.posted_message_id)
        await posted.edit(embed=embed)
    except (nextcord.Forbidden, nextcord.NotFound):
        return  # post deleted/unreachable — admin action, nothing to refresh
    starboard_helper.upsert_entry(
        config_id=config.id, guild_id=config.guild_id,
        original_message_id=message.id,
        original_channel_id=message.channel.id,
        author_id=message.author.id,
        star_count=count)


async def handle_message_delete(bot, payload):
    """Honour deletion of an original: drop every entry it fed and delete the
    corresponding starboard posts across all boards."""
    if payload.guild_id is None:
        return
    entries = starboard_helper.delete_entries_for_message(payload.guild_id, payload.message_id)
    for entry in entries:
        if entry.posted_message_id is None:
            continue
        config = starboard_helper.get_config(entry.starboard_config_id)
        if config is None:
            continue
        target = await bot_utils.get_or_fetch_channel(bot, config.target_channel_id)
        if target is None:
            continue
        try:
            posted = await target.fetch_message(entry.posted_message_id)
            await posted.delete()
        except (nextcord.NotFound, nextcord.Forbidden):
            # Post already gone or unreachable — the entry is cleared regardless.
            continue
