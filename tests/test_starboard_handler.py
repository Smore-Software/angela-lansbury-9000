"""Tests for bot/events/handlers/starboard_handler.py — reaction add/remove
post-or-edit logic and original-message-delete cleanup.

Everything is faked (bot / channels / messages / payloads); no network. The
module-level config cache survives the per-test DB rollback, so it is cleared
between tests.
"""
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import nextcord

from bot.events.handlers import starboard_handler
from db.helpers import starboard_helper
from tests.conftest import make_emoji, make_payload

GUILD = 1
SOURCE_CHANNEL_ID = 100
TARGET_CHANNEL_ID = 200
TARGET_CHANNEL_ID_2 = 201
MESSAGE_ID = 500
POSTED_ID = 9001
BYPASS_ROLE_ID = 77
BYPASS_ROLE_NAME = 'Moderator'
OTHER_ROLE_ID = 3

# Sentinel so a test can pass `guild=None` (the unresolvable-guild case) and still
# be distinguishable from "caller did not care, use the default guild".
_DEFAULT_GUILD = object()


@pytest.fixture(autouse=True)
def _clear_cache():
    starboard_helper.__CACHE.clear()
    yield
    starboard_helper.__CACHE.clear()


# --- fakes ------------------------------------------------------------------


def _not_found():
    return nextcord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'missing')


class FakeChannel:
    """A channel that records send/fetch_message/edit/delete calls."""

    def __init__(self, id, name='general'):
        self.id = id
        self.name = name
        self.send = AsyncMock(return_value=SimpleNamespace(id=POSTED_ID))
        self.fetch_message = AsyncMock()


def make_bot(channels):
    bot = SimpleNamespace()
    bot.cached_messages = []
    bot.get_channel = lambda cid: channels.get(cid)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels[cid])
    return bot


def make_guild(roles=None):
    """A guild whose ``get_role`` resolves the ids in ``roles`` and returns
    ``None`` for anything else — the shape ``_bypass_role_name`` reads."""
    resolvable = {BYPASS_ROLE_ID: BYPASS_ROLE_NAME} if roles is None else roles
    return SimpleNamespace(
        get_role=lambda rid: (SimpleNamespace(id=rid, name=resolvable[rid])
                              if rid in resolvable else None))


def make_member(*role_ids):
    """A reactor shaped like ``payload.member``: an id and a list of roles."""
    return SimpleNamespace(id=42, roles=[SimpleNamespace(id=rid) for rid in role_ids])


def make_message(channel, reactions, content='hi', message_id=MESSAGE_ID, author_id=42,
                 guild=_DEFAULT_GUILD):
    wrapped = [SimpleNamespace(emoji=emoji, count=count) for emoji, count in reactions]
    return SimpleNamespace(
        id=message_id,
        channel=channel,
        guild=make_guild() if guild is _DEFAULT_GUILD else guild,
        reactions=wrapped,
        content=content,
        embeds=[],
        attachments=[],
        jump_url='http://jump',
        created_at=datetime.datetime(2026, 6, 22, tzinfo=datetime.timezone.utc),
        author=SimpleNamespace(id=author_id, display_name='Bob',
                               display_avatar=SimpleNamespace(url='http://avatar')),
    )


def make_posted_message(id=POSTED_ID):
    return SimpleNamespace(id=id, edit=AsyncMock(), delete=AsyncMock())


def add_board(target_channel_id=TARGET_CHANNEL_ID, emoji='⭐', threshold=5,
              bypass_role_id=None):
    return starboard_helper.add_config(
        guild_id=GUILD, target_channel_id=target_channel_id, emoji=emoji,
        threshold=threshold, bypass_role_id=bypass_role_id)


# --- reaction add: threshold behaviour --------------------------------------


async def test_below_threshold_no_send_no_entry():
    config = add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 4)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None


async def test_crossing_threshold_sends_once_and_records_entry():
    config = add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 5)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    # The emoji, live count, and source link ride in the message content (where a
    # custom emoji and a link both render); the footer carries just the channel.
    assert target.send.call_args.kwargs['content'] == '⭐ **× 5** · [Source ↗](http://jump)'
    assert target.send.call_args.kwargs['embed'].footer.text == '#general'
    entry = starboard_helper.get_entry(config.id, MESSAGE_ID)
    assert entry is not None
    assert entry.posted_message_id == POSTED_ID
    assert entry.star_count == 5


async def test_already_posted_edits_and_does_not_resend():
    config = add_board(threshold=5)
    starboard_helper.upsert_entry(
        config_id=config.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID, star_count=5)

    target = FakeChannel(TARGET_CHANNEL_ID)
    posted = make_posted_message()
    target.fetch_message = AsyncMock(return_value=posted)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 6)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0           # no duplicate post
    assert posted.edit.call_count == 1           # count refreshed in place
    # Refreshed count rides in the message content, not the footer.
    assert posted.edit.call_args.kwargs['content'] == '⭐ **× 6** · [Source ↗](http://jump)'
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).star_count == 6


async def test_send_forbidden_writes_no_entry():
    # If the first post is rejected (missing perms), no entry is recorded, so the
    # next reaction retries the post rather than trying to edit a nonexistent one.
    config = add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    target.send = AsyncMock(side_effect=nextcord.Forbidden(
        SimpleNamespace(status=403, reason='Forbidden'), 'no perms'))
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 5)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)  # swallowed → Sentry

    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None


async def test_edit_not_found_leaves_star_count_unchanged():
    # The posted message is gone; the edit raises NotFound (swallowed). The entry
    # survives and its star_count is NOT advanced to the new value.
    config = add_board(threshold=5)
    starboard_helper.upsert_entry(
        config_id=config.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID, star_count=5)

    target = FakeChannel(TARGET_CHANNEL_ID)
    target.fetch_message = AsyncMock(side_effect=_not_found())
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 8)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)  # swallowed → Sentry

    assert target.send.call_count == 0
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).star_count == 5


async def test_reaction_in_target_channel_is_skipped():
    add_board(target_channel_id=TARGET_CHANNEL_ID, threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    message = make_message(target, [(make_emoji('⭐'), 9)])
    bot = make_bot({TARGET_CHANNEL_ID: target})
    bot.cached_messages = [message]

    # Reaction happens INSIDE the board's own target channel — must be ignored.
    payload = make_payload(emoji=make_emoji('⭐'), channel_id=TARGET_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    # Skipped at config-match, so the message is never even resolved/fetched.
    assert target.fetch_message.call_count == 0


async def test_fan_out_posts_to_both_boards():
    cfg1 = add_board(target_channel_id=TARGET_CHANNEL_ID, emoji='⭐', threshold=5)
    cfg2 = add_board(target_channel_id=TARGET_CHANNEL_ID_2, emoji='⭐', threshold=5)
    target1 = FakeChannel(TARGET_CHANNEL_ID)
    target2 = FakeChannel(TARGET_CHANNEL_ID_2)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 5)])
    bot = make_bot({TARGET_CHANNEL_ID: target1, TARGET_CHANNEL_ID_2: target2,
                    SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target1.send.call_count == 1
    assert target2.send.call_count == 1
    assert starboard_helper.get_entry(cfg1.id, MESSAGE_ID) is not None
    assert starboard_helper.get_entry(cfg2.id, MESSAGE_ID) is not None


# --- cache-first resolution -------------------------------------------------


async def test_cache_hit_does_not_fetch_message():
    add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 5)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]   # cache HIT

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    # The source channel's fetch_message must never be touched on a cache hit.
    assert source.fetch_message.call_count == 0
    assert target.send.call_count == 1


async def test_cache_miss_fetches_message():
    add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 5)])
    source.fetch_message = AsyncMock(return_value=message)
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = []          # cache MISS

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    source.fetch_message.assert_awaited_once_with(MESSAGE_ID)
    assert target.send.call_count == 1


async def test_cache_miss_message_not_found_is_noop():
    config = add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    source.fetch_message = AsyncMock(side_effect=_not_found())
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = []

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None


# --- self-star / bot reactions count ----------------------------------------


async def test_self_star_counts_toward_threshold():
    # The reaction count is taken as-is; the bot never filters reactors, so a
    # self-star (member == author) still crosses the threshold.
    config = add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 5)], author_id=42)
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD, user_id=42,
                           member=SimpleNamespace(id=42))
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is not None


# --- guards -----------------------------------------------------------------


async def test_no_guild_id_is_noop():
    add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    bot = make_bot({TARGET_CHANNEL_ID: target})
    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=None)
    await starboard_handler.handle_reaction_add(bot, payload)
    assert target.send.call_count == 0


async def test_non_matching_emoji_does_not_resolve_message():
    add_board(emoji='⭐', threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    source.fetch_message = AsyncMock()
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = []

    payload = make_payload(emoji=make_emoji('🔥'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD)
    await starboard_handler.handle_reaction_add(bot, payload)

    # No matching board → no message resolution at all (the early-out hot path).
    assert source.fetch_message.call_count == 0
    assert target.send.call_count == 0


# --- reaction remove: edits, never deletes ----------------------------------


async def test_remove_edits_existing_post_does_not_delete():
    config = add_board(threshold=5)
    starboard_helper.upsert_entry(
        config_id=config.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID, star_count=6)

    target = FakeChannel(TARGET_CHANNEL_ID)
    posted = make_posted_message()
    target.fetch_message = AsyncMock(return_value=posted)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    # Dropped below threshold after an un-react.
    message = make_message(source, [(make_emoji('⭐'), 4)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD,
                           event_type='REACTION_REMOVE')
    await starboard_handler.handle_reaction_remove(bot, payload)

    assert posted.edit.call_count == 1           # count refreshed
    assert posted.delete.call_count == 0         # NEVER deleted on un-react
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).star_count == 4


async def test_remove_with_no_entry_is_noop():
    add_board(threshold=5)
    target = FakeChannel(TARGET_CHANNEL_ID)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), 2)])
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]

    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD,
                           event_type='REACTION_REMOVE')
    await starboard_handler.handle_reaction_remove(bot, payload)

    assert target.send.call_count == 0
    assert target.fetch_message.call_count == 0


# --- bypass role: posting & the subtext line ---------------------------------
# The truth table this feature lives or dies by. The posting gate keys on "is
# this reactor privileged?"; the persisted `bypassed_role_id` keys on "did this
# reaction actually cause a below-threshold post?". Collapsing the two is the bug
# these tests exist to catch.

NOTE = '-# Someone with the **Moderator** role bypassed the 5-reaction threshold.'
VAGUE_NOTE = '-# Someone with a privileged role bypassed the 5-reaction threshold.'


def _seed_entry(config, star_count, bypassed_role_id=None):
    return starboard_helper.upsert_entry(
        config_id=config.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID, star_count=star_count,
        bypassed_role_id=bypassed_role_id)


def _react_setup(config_count, member=None, guild=_DEFAULT_GUILD, posted=None):
    """Wire a bot/target/source/message/payload for one reaction event."""
    target = FakeChannel(TARGET_CHANNEL_ID)
    if posted is not None:
        target.fetch_message = AsyncMock(return_value=posted)
    source = FakeChannel(SOURCE_CHANNEL_ID)
    message = make_message(source, [(make_emoji('⭐'), config_count)], guild=guild)
    bot = make_bot({TARGET_CHANNEL_ID: target, SOURCE_CHANNEL_ID: source})
    bot.cached_messages = [message]
    payload = make_payload(emoji=make_emoji('⭐'), channel_id=SOURCE_CHANNEL_ID,
                           message_id=MESSAGE_ID, guild_id=GUILD, member=member)
    return bot, target, payload


# Row: no entry / ordinary reactor / below threshold → no post (regression guard).
async def test_bypass_board_ordinary_reactor_below_threshold_does_not_post():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(4, member=make_member(OTHER_ROLE_ID))

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None


# A board with no bypass role configured cannot be bypassed by anyone, however
# many roles the reactor carries — including the board's own threshold-th one.
async def test_board_without_bypass_role_never_bypasses():
    config = add_board(threshold=5, bypass_role_id=None)
    bot, target, payload = _react_setup(
        4, member=make_member(OTHER_ROLE_ID, BYPASS_ROLE_ID))

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None


# Row: no entry / ordinary reactor / at threshold → post, no subtext.
async def test_bypass_board_ordinary_reactor_at_threshold_posts_without_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(5, member=make_member(OTHER_ROLE_ID))

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    assert target.send.call_args.kwargs['content'] == '⭐ **× 5** · [Source ↗](http://jump)'
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id is None


# Row: no entry / bypass-role reactor / below threshold → post + subtext, stamped.
async def test_bypass_role_reactor_below_threshold_posts_with_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(1, member=make_member(BYPASS_ROLE_ID))

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    assert target.send.call_args.kwargs['content'] == (
        '⭐ **× 1** · [Source ↗](http://jump)\n'
        '-# Someone with the **Moderator** role bypassed the 5-reaction threshold.')
    entry = starboard_helper.get_entry(config.id, MESSAGE_ID)
    assert entry.posted_message_id == POSTED_ID
    assert entry.star_count == 1
    assert entry.bypassed_role_id == BYPASS_ROLE_ID


# Row: no entry / bypass-role reactor / AT threshold → post, no subtext, NOT
# stamped — the post earned its place, the moderator was merely the 5th reactor.
# Then the reactions fall away: still no subtext. (Flag-correctness test (a).)
async def test_moderator_as_threshold_th_reactor_is_not_stamped_and_never_shows_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(5, member=make_member(BYPASS_ROLE_ID))

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    assert target.send.call_args.kwargs['content'] == '⭐ **× 5** · [Source ↗](http://jump)'
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id is None

    # Now three reactors un-star it. `payload.member` is None on the remove path.
    posted = make_posted_message()
    bot, target, payload = _react_setup(2, posted=posted)
    payload.event_type = 'REACTION_REMOVE'
    await starboard_handler.handle_reaction_remove(bot, payload)

    assert posted.edit.call_count == 1
    assert posted.edit.call_args.kwargs['content'] == '⭐ **× 2** · [Source ↗](http://jump)'
    assert '\n-#' not in posted.edit.call_args.kwargs['content']


# Row: existing NORMAL entry / bypass-role reactor / above threshold → edit, no
# subtext, entry stays unflagged; a later drop below still shows none.
# (Flag-correctness test (b) — the retro-stamp this column exists to prevent.)
async def test_bypass_reactor_on_normally_earned_entry_never_stamps_it():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    _seed_entry(config, star_count=5)

    posted = make_posted_message()
    bot, target, payload = _react_setup(
        6, member=make_member(BYPASS_ROLE_ID), posted=posted)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    assert posted.edit.call_args.kwargs['content'] == '⭐ **× 6** · [Source ↗](http://jump)'
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id is None

    # Reactions fall back below the threshold: the post is ordinary, so no note.
    posted2 = make_posted_message()
    bot, target, payload = _react_setup(2, posted=posted2)
    payload.event_type = 'REACTION_REMOVE'
    await starboard_handler.handle_reaction_remove(bot, payload)

    assert posted2.edit.call_args.kwargs['content'] == '⭐ **× 2** · [Source ↗](http://jump)'
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id is None


# The same trap from the other side: an ordinary entry that has ALREADY fallen
# below the threshold, reacted to by a privileged member. The entry exists, so
# nothing is being posted — and an existing entry is the sole authority on its own
# bypass state, so the current reactor must not supply a note it never earned.
async def test_bypass_reactor_on_below_threshold_normal_entry_shows_no_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    _seed_entry(config, star_count=5)  # posted normally, then un-reacted down to 2

    posted = make_posted_message()
    bot, target, payload = _react_setup(
        3, member=make_member(BYPASS_ROLE_ID), posted=posted)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 0
    assert posted.edit.call_args.kwargs['content'] == '⭐ **× 3** · [Source ↗](http://jump)'
    assert '\n-#' not in posted.edit.call_args.kwargs['content']
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id is None


# Row: existing BYPASSED entry / ordinary reactor / still below → edit, note kept.
async def test_bypassed_entry_still_below_threshold_keeps_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    _seed_entry(config, star_count=1, bypassed_role_id=BYPASS_ROLE_ID)

    posted = make_posted_message()
    bot, target, payload = _react_setup(
        3, member=make_member(OTHER_ROLE_ID), posted=posted)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert posted.edit.call_args.kwargs['content'] == (
        f'⭐ **× 3** · [Source ↗](http://jump)\n{NOTE}')


# Row: existing BYPASSED entry / count reaches the threshold → note dropped. The
# post has now earned its place on its own.
async def test_bypassed_entry_reaching_threshold_drops_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    _seed_entry(config, star_count=1, bypassed_role_id=BYPASS_ROLE_ID)

    posted = make_posted_message()
    bot, target, payload = _react_setup(
        5, member=make_member(OTHER_ROLE_ID), posted=posted)
    await starboard_handler.handle_reaction_add(bot, payload)

    assert posted.edit.call_args.kwargs['content'] == '⭐ **× 5** · [Source ↗](http://jump)'
    # The stamp itself survives — only its *rendering* is conditional.
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id == BYPASS_ROLE_ID


# Row: BYPASSED entry rises above the threshold (note gone), then is un-reacted
# back below → the note RETURNS. Rendering is per-render, not a one-way latch.
async def test_bypassed_entry_rise_then_fall_restores_subtext():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    _seed_entry(config, star_count=1, bypassed_role_id=BYPASS_ROLE_ID)

    risen = make_posted_message()
    bot, target, payload = _react_setup(
        5, member=make_member(OTHER_ROLE_ID), posted=risen)
    await starboard_handler.handle_reaction_add(bot, payload)
    assert risen.edit.call_args.kwargs['content'] == '⭐ **× 5** · [Source ↗](http://jump)'

    fallen = make_posted_message()
    bot, target, payload = _react_setup(3, posted=fallen)
    payload.event_type = 'REACTION_REMOVE'
    await starboard_handler.handle_reaction_remove(bot, payload)

    assert fallen.edit.call_args.kwargs['content'] == (
        f'⭐ **× 3** · [Source ↗](http://jump)\n{NOTE}')


# A board with no bypass role at all: a normal post un-reacted below the
# threshold shows nothing new.
async def test_normal_post_dropped_below_threshold_shows_no_subtext():
    config = add_board(threshold=5)
    _seed_entry(config, star_count=5)

    posted = make_posted_message()
    bot, target, payload = _react_setup(2, posted=posted)
    payload.event_type = 'REACTION_REMOVE'
    await starboard_handler.handle_reaction_remove(bot, payload)

    assert posted.edit.call_args.kwargs['content'] == '⭐ **× 2** · [Source ↗](http://jump)'
    assert '\n-#' not in posted.edit.call_args.kwargs['content']


# The role was deleted (or is not in the guild cache): the post still happens and
# the note degrades to an unnamed phrasing rather than rendering `None`.
async def test_unresolvable_role_degrades_to_privileged_role_note():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(
        1, member=make_member(BYPASS_ROLE_ID), guild=make_guild(roles={}))

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    assert target.send.call_args.kwargs['content'] == (
        f'⭐ **× 1** · [Source ↗](http://jump)\n{VAGUE_NOTE}')
    assert starboard_helper.get_entry(config.id, MESSAGE_ID).bypassed_role_id == BYPASS_ROLE_ID


async def test_message_without_guild_degrades_to_privileged_role_note():
    add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(
        1, member=make_member(BYPASS_ROLE_ID), guild=None)

    await starboard_handler.handle_reaction_add(bot, payload)

    assert target.send.call_count == 1
    assert target.send.call_args.kwargs['content'] == (
        f'⭐ **× 1** · [Source ↗](http://jump)\n{VAGUE_NOTE}')


# The un-react path never posts, bypass role or not: `payload.member` is always
# None there, so nothing can be privileged.
async def test_remove_with_no_entry_on_bypass_board_still_posts_nothing():
    config = add_board(threshold=5, bypass_role_id=BYPASS_ROLE_ID)
    bot, target, payload = _react_setup(2)
    payload.event_type = 'REACTION_REMOVE'

    await starboard_handler.handle_reaction_remove(bot, payload)

    assert target.send.call_count == 0
    assert target.fetch_message.call_count == 0
    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None


# --- delete propagation -----------------------------------------------------


async def test_delete_removes_posts_and_clears_entries_across_boards():
    cfg1 = add_board(target_channel_id=TARGET_CHANNEL_ID, emoji='⭐')
    cfg2 = add_board(target_channel_id=TARGET_CHANNEL_ID_2, emoji='⭐')
    starboard_helper.upsert_entry(
        config_id=cfg1.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID, star_count=5)
    starboard_helper.upsert_entry(
        config_id=cfg2.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID + 1, star_count=5)

    target1 = FakeChannel(TARGET_CHANNEL_ID)
    target2 = FakeChannel(TARGET_CHANNEL_ID_2)
    posted1 = make_posted_message(POSTED_ID)
    posted2 = make_posted_message(POSTED_ID + 1)
    target1.fetch_message = AsyncMock(return_value=posted1)
    target2.fetch_message = AsyncMock(return_value=posted2)
    bot = make_bot({TARGET_CHANNEL_ID: target1, TARGET_CHANNEL_ID_2: target2})

    payload = make_payload(message_id=MESSAGE_ID, channel_id=SOURCE_CHANNEL_ID, guild_id=GUILD)
    await starboard_handler.handle_message_delete(bot, payload)

    assert posted1.delete.call_count == 1
    assert posted2.delete.call_count == 1
    assert starboard_helper.get_entry(cfg1.id, MESSAGE_ID) is None
    assert starboard_helper.get_entry(cfg2.id, MESSAGE_ID) is None


async def test_delete_with_no_entry_is_noop():
    bot = make_bot({})
    payload = make_payload(message_id=99999, channel_id=SOURCE_CHANNEL_ID, guild_id=GUILD)
    # No entries → nothing to resolve, no error.
    await starboard_handler.handle_message_delete(bot, payload)


async def test_delete_swallows_not_found_and_still_clears_entry():
    config = add_board(target_channel_id=TARGET_CHANNEL_ID, emoji='⭐')
    starboard_helper.upsert_entry(
        config_id=config.id, guild_id=GUILD, original_message_id=MESSAGE_ID,
        original_channel_id=SOURCE_CHANNEL_ID, author_id=42,
        posted_message_id=POSTED_ID, star_count=5)

    target = FakeChannel(TARGET_CHANNEL_ID)
    target.fetch_message = AsyncMock(side_effect=_not_found())
    bot = make_bot({TARGET_CHANNEL_ID: target})

    payload = make_payload(message_id=MESSAGE_ID, channel_id=SOURCE_CHANNEL_ID, guild_id=GUILD)
    # The NotFound while deleting the post is swallowed; the entry is still cleared.
    await starboard_handler.handle_message_delete(bot, payload)

    assert starboard_helper.get_entry(config.id, MESSAGE_ID) is None
