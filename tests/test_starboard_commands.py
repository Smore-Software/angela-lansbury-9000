"""Tests for the /starboard command cog's testable units.

Full slash-command round-trips need a live interaction, so we test the extracted
logic instead: the pure emoji parser, custom-emoji validation against a guild, the
label/display/embed builders, and the create/update/remove paths through
``starboard_helper`` (asserting both DB state and that the per-guild cache is
invalidated on every mutation).
"""
from types import SimpleNamespace

import pytest

from bot.cogs.starboard import starboard_commands as sc
from bot.cogs.starboard.starboard_utils import parse_emoji_input
from db.helpers import starboard_helper


@pytest.fixture(autouse=True)
def _clear_cache():
    # The config cache is a module-level dict that outlives the per-test DB
    # rollback, so clear it around every test to stop leakage.
    starboard_helper.__CACHE.clear()
    yield
    starboard_helper.__CACHE.clear()


# --- parse_emoji_input ------------------------------------------------------


def test_parse_emoji_input_unicode():
    assert parse_emoji_input('📖') == ('📖', None)


def test_parse_emoji_input_unicode_strips_whitespace():
    assert parse_emoji_input('  ⭐  ') == ('⭐', None)


def test_parse_emoji_input_custom():
    assert parse_emoji_input('<:book:123>') == ('book', 123)


def test_parse_emoji_input_animated_custom():
    assert parse_emoji_input('<a:book:123>') == ('book', 123)


def test_parse_emoji_input_empty_raises():
    with pytest.raises(ValueError):
        parse_emoji_input('')


def test_parse_emoji_input_whitespace_only_raises():
    with pytest.raises(ValueError):
        parse_emoji_input('   ')


def test_parse_emoji_input_none_raises():
    with pytest.raises(ValueError):
        parse_emoji_input(None)


def test_parse_emoji_input_malformed_custom_tag_raises():
    # Opens like a custom-emoji mention but doesn't fully parse → garbage.
    with pytest.raises(ValueError):
        parse_emoji_input('<:broken>')
    with pytest.raises(ValueError):
        parse_emoji_input('<::>')


# --- custom_emoji_belongs_to_guild ------------------------------------------


def _guild_emojis(*ids):
    return [SimpleNamespace(id=i, name=f'e{i}') for i in ids]


def test_custom_emoji_belongs_to_guild_true():
    assert sc.custom_emoji_belongs_to_guild(_guild_emojis(1, 2, 123), 123) is True


def test_custom_emoji_belongs_to_guild_false_for_foreign_emoji():
    assert sc.custom_emoji_belongs_to_guild(_guild_emojis(1, 2), 999) is False


def test_custom_emoji_belongs_to_guild_empty_guild():
    assert sc.custom_emoji_belongs_to_guild([], 123) is False


# --- emoji_display ----------------------------------------------------------


def test_emoji_display_unicode():
    config = SimpleNamespace(emoji='⭐', emoji_id=None)
    assert sc.emoji_display(config) == '⭐'


def test_emoji_display_custom():
    config = SimpleNamespace(emoji='book', emoji_id=123)
    assert sc.emoji_display(config) == '<:book:123>'


# --- board_summary / board_label --------------------------------------------


def _config(id=1, target_channel_id=10, emoji='⭐', emoji_id=None,
            threshold=5, enabled=True, name=None):
    return SimpleNamespace(id=id, target_channel_id=target_channel_id, emoji=emoji,
                           emoji_id=emoji_id, threshold=threshold, enabled=enabled,
                           name=name)


def test_board_summary_standard_format_with_name():
    # Standardized format: `Name #channel | emoji | **≥ N**`, id omitted.
    summary = sc.board_summary(_config(name='Hall of Fame', threshold=7), '<#10>')
    assert summary == '(Hall of Fame) <#10> | ⭐ | **≥ 7**'


def test_board_summary_omits_name_when_absent():
    summary = sc.board_summary(_config(emoji='🔥', threshold=3), '#general')
    assert summary == '#general | 🔥 | **≥ 3**'


def test_board_summary_plain_drops_markdown_bold():
    summary = sc.board_summary(_config(threshold=4), '#general', markdown=False)
    assert summary == '#general | ⭐ | ≥ 4'


def test_board_label_includes_channel_emoji_threshold():
    label = sc.board_label(_config(emoji='⭐', threshold=7), channel_name='general')
    assert label == '#general | ⭐ | ≥ 7'


def test_board_label_includes_name_when_set():
    label = sc.board_label(_config(name='Stars', threshold=5), channel_name='general')
    assert label == '(Stars) #general | ⭐ | ≥ 5'


def test_board_label_uses_channel_mention_when_name_unknown():
    label = sc.board_label(_config(target_channel_id=42, emoji='⭐', threshold=3))
    assert label == '<#42> | ⭐ | ≥ 3'


def test_board_label_truncated_to_limit():
    label = sc.board_label(_config(name='x' * 200))
    assert len(label) <= sc._AUTOCOMPLETE_LABEL_MAX


# --- build_list_embeds ------------------------------------------------------


def test_build_list_embeds_single_embed():
    configs = [_config(id=1, name='Star'), _config(id=2, emoji='🔥', enabled=False)]
    embeds = sc.build_list_embeds(configs)
    assert len(embeds) == 1
    lines = embeds[0].description.splitlines()
    assert len(lines) == 2
    # Numbered Markdown list using the standardized pipe format; no board id and
    # no legacy "·" bullet separators.
    assert lines[0] == '1. (Star) <#10> | ⭐ | **≥ 5**'
    assert '·' not in embeds[0].description
    # Disabled boards are flagged with a trailing pipe segment.
    assert lines[1].endswith('| disabled')


def test_build_list_embeds_paginates_past_page_size():
    configs = [_config(id=i) for i in range(sc._LIST_BOARDS_PER_PAGE + 3)]
    embeds = sc.build_list_embeds(configs)
    assert len(embeds) == 2
    assert len(embeds[0].description.splitlines()) == sc._LIST_BOARDS_PER_PAGE
    assert len(embeds[1].description.splitlines()) == 3
    # Numbering is continuous across pages.
    assert embeds[1].description.splitlines()[0].startswith(
        f'{sc._LIST_BOARDS_PER_PAGE + 1}. ')


def test_build_list_embeds_empty():
    assert sc.build_list_embeds([]) == []


# --- _board_choices autocomplete filtering ----------------------------------


class _FakeGuild:
    def __init__(self, channels):
        self._channels = channels  # {channel_id: name}

    def get_channel(self, cid):
        name = self._channels.get(cid)
        return SimpleNamespace(name=name) if name else None


class _FakeAcInteraction:
    def __init__(self, guild_id, channels):
        self.guild_id = guild_id
        self.guild = _FakeGuild(channels)


def test_board_choices_filters_by_channel_name():
    book = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    starboard_helper.add_config(guild_id=1, target_channel_id=11, emoji='🔥')
    interaction = _FakeAcInteraction(1, {10: 'book-club', 11: 'memes'})
    choices = sc.StarboardCommands._board_choices(interaction, 'book')
    assert list(choices.values()) == [str(book.id)]


def test_board_choices_no_query_returns_all():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    starboard_helper.add_config(guild_id=1, target_channel_id=11, emoji='🔥')
    interaction = _FakeAcInteraction(1, {10: 'book-club', 11: 'memes'})
    assert len(sc.StarboardCommands._board_choices(interaction)) == 2


def test_board_choices_filter_is_case_insensitive():
    book = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    interaction = _FakeAcInteraction(1, {10: 'Book-Club'})
    choices = sc.StarboardCommands._board_choices(interaction, 'BOOK')
    assert list(choices.values()) == [str(book.id)]


def test_board_choices_drops_unresolved_channels_when_filtering():
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    interaction = _FakeAcInteraction(1, {})  # channel can't be resolved
    assert sc.StarboardCommands._board_choices(interaction, 'book') == {}


# --- create/update/remove through the helper (DB + cache invalidation) ------


def test_add_config_persists_and_invalidates_cache():
    # Prime the cache for the guild so we can prove the mutator drops it.
    assert starboard_helper.get_enabled_configs(1) == []
    cfg = starboard_helper.add_config(
        guild_id=1, target_channel_id=10, emoji='book', emoji_id=123,
        threshold=4, name='Books')
    # DB state reflects what /starboard add would persist (custom emoji → id set).
    stored = starboard_helper.get_config(cfg.id)
    assert stored.emoji == 'book'
    assert stored.emoji_id == 123
    assert stored.threshold == 4
    assert stored.name == 'Books'
    # The primed cache was invalidated, so the next read sees the new board.
    assert len(starboard_helper.get_enabled_configs(1)) == 1


def test_update_config_applies_only_provided_fields_and_invalidates_cache():
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                      threshold=5)
    starboard_helper.get_enabled_configs(1)  # prime cache
    # Mirror the cog's edit: only threshold + enabled provided.
    starboard_helper.update_config(cfg.id, threshold=8, enabled=False)
    stored = starboard_helper.get_config(cfg.id)
    assert stored.threshold == 8
    assert stored.enabled is False
    assert stored.emoji == '⭐'  # untouched field preserved
    # Disabling dropped it from the enabled cache after invalidation.
    assert starboard_helper.get_enabled_configs(1) == []


def test_remove_config_deletes_and_invalidates_cache():
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    assert len(starboard_helper.get_enabled_configs(1)) == 1  # prime cache
    assert starboard_helper.remove_config(cfg.id) is True
    assert starboard_helper.get_config(cfg.id) is None
    assert starboard_helper.get_enabled_configs(1) == []


def test_update_config_moving_guild_invalidates_both_caches():
    # An edit that reassigns a board to another guild must drop BOTH guilds' caches
    # so neither is left stale (mirrors update_config's dual _invalidate).
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    starboard_helper.get_enabled_configs(1)  # prime guild 1's cache
    starboard_helper.get_enabled_configs(2)  # prime guild 2's (empty) cache
    starboard_helper.update_config(cfg.id, guild_id=2)
    assert starboard_helper.get_enabled_configs(1) == []  # left the old guild
    assert len(starboard_helper.get_enabled_configs(2)) == 1  # appears in the new one


# --- _resolve_board (cross-guild safety) ------------------------------------


def test_resolve_board_rejects_config_from_another_guild():
    cfg = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    # An admin in guild 2 must not be able to edit/remove guild 1's board.
    interaction = SimpleNamespace(guild_id=2)
    assert sc.StarboardCommands._resolve_board(interaction, str(cfg.id)) is None
    # The board's own guild resolves it fine.
    own = SimpleNamespace(guild_id=1)
    assert sc.StarboardCommands._resolve_board(own, str(cfg.id)).id == cfg.id


def test_resolve_board_rejects_malformed_and_missing_ids():
    interaction = SimpleNamespace(guild_id=1)
    assert sc.StarboardCommands._resolve_board(interaction, 'not-an-int') is None
    assert sc.StarboardCommands._resolve_board(interaction, '99999') is None
