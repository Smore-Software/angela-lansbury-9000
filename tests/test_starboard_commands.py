"""Tests for the /starboard command cog's testable units.

Full slash-command round-trips need a live gateway, so we test the extracted
logic instead: the pure emoji parser, custom-emoji validation against a guild, the
label/display/embed builders, and the create/update/remove paths through
``starboard_helper`` (asserting both DB state and that the per-guild cache is
invalidated on every mutation).

The ``add``/``edit`` callbacks that own the bypass-role branches are additionally
driven directly via ``cog.add.callback(...)`` with a fake interaction — the same
stand-in ``tests/test_starboard_list_view.py`` uses for ``list``. Every option has
to be passed explicitly there: an unpassed one keeps its ``SlashOption`` default
object rather than the ``None`` Discord would send.
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


# --- emoji_label (plain-text autocomplete rendering) ------------------------


def test_emoji_label_unicode_is_the_char():
    config = SimpleNamespace(emoji='⭐', emoji_id=None)
    assert sc.emoji_label(config) == '⭐'


def test_emoji_label_custom_uses_name_not_mention():
    # A `<:name:id>` mention renders as raw text in an autocomplete label, so the
    # readable `:name:` form is used there instead.
    config = SimpleNamespace(emoji='book', emoji_id=123)
    assert sc.emoji_label(config) == ':book:'


# --- board_summary / board_label --------------------------------------------


def _config(id=1, target_channel_id=10, emoji='⭐', emoji_id=None,
            threshold=5, enabled=True, bypass_role_id=None):
    return SimpleNamespace(id=id, target_channel_id=target_channel_id, emoji=emoji,
                           emoji_id=emoji_id, threshold=threshold, enabled=enabled,
                           bypass_role_id=bypass_role_id)


def _role(id=77, name='Mods', default=False):
    return SimpleNamespace(id=id, name=name, is_default=lambda: default)


def test_board_summary_standard_format():
    # Standardized format: `#channel | emoji | **≥ N**`, id omitted.
    summary = sc.board_summary(_config(threshold=7), '<#10>')
    assert summary == '<#10> | ⭐ | **≥ 7**'


def test_board_summary_plain_drops_markdown_bold():
    summary = sc.board_summary(_config(threshold=4), '#general', markdown=False)
    assert summary == '#general | ⭐ | ≥ 4'


def test_board_label_includes_channel_emoji_threshold():
    label = sc.board_label(_config(emoji='⭐', threshold=7), channel_name='general')
    assert label == '#general | ⭐ | ≥ 7'


def test_board_label_uses_channel_mention_when_name_unknown():
    label = sc.board_label(_config(target_channel_id=42, emoji='⭐', threshold=3))
    assert label == '<#42> | ⭐ | ≥ 3'


def test_board_label_renders_custom_emoji_as_name():
    # Autocomplete is plain text — a custom emoji shows as `:name:`, not `<:name:id>`.
    label = sc.board_label(_config(emoji='blob', emoji_id=999, threshold=2),
                           channel_name='general')
    assert label == '#general | :blob: | ≥ 2'


def test_board_summary_markdown_keeps_custom_emoji_mention():
    # In markdown contexts (list embeds, confirmations) the mention renders as the
    # actual emoji, so it is preserved.
    summary = sc.board_summary(_config(emoji='blob', emoji_id=999, threshold=2), '<#10>')
    assert summary == '<#10> | <:blob:999> | **≥ 2**'


def test_board_label_truncated_to_limit():
    label = sc.board_label(_config(), channel_name='x' * 200)
    assert len(label) <= sc._AUTOCOMPLETE_LABEL_MAX


# --- bypass_role_ref --------------------------------------------------------


def test_bypass_role_ref_none_when_board_has_no_bypass_role():
    assert sc.bypass_role_ref(_config(bypass_role_id=None)) is None


def test_bypass_role_ref_markdown_is_the_role_mention():
    # Markdown contexts render `<@&id>` as the role pill; an embed can't ping.
    assert sc.bypass_role_ref(_config(bypass_role_id=77)) == '<@&77>'


def test_bypass_role_ref_plain_resolves_name_from_guild():
    guild = _FakeGuild({}, {77: 'Mods'})
    assert sc.bypass_role_ref(_config(bypass_role_id=77), guild,
                              markdown=False) == '@Mods'


def test_bypass_role_ref_plain_falls_back_to_id_when_role_is_gone():
    # Deleting a role in Discord doesn't clear the column, so the label degrades
    # to the raw id rather than rendering `@None`.
    guild = _FakeGuild({}, {})
    assert sc.bypass_role_ref(_config(bypass_role_id=77), guild,
                              markdown=False) == '@77'


def test_bypass_role_ref_plain_falls_back_to_id_without_a_guild():
    assert sc.bypass_role_ref(_config(bypass_role_id=77), None,
                              markdown=False) == '@77'


# --- bypass_role_error ------------------------------------------------------


def test_bypass_role_error_none_when_no_role_supplied():
    assert sc.bypass_role_error(None) is None


def test_bypass_role_error_none_for_an_ordinary_role():
    assert sc.bypass_role_error(_role(default=False)) is None


def test_bypass_role_error_rejects_everyone():
    # `Member.roles` always contains the guild's default role, so @everyone as a
    # bypass role would make every reaction skip the threshold.
    error = sc.bypass_role_error(_role(name='@everyone', default=True))
    assert error is not None
    assert '@everyone' in error


# --- board_summary / board_label with a bypass role -------------------------


def test_board_summary_appends_bypass_segment_last():
    summary = sc.board_summary(_config(), '<#10>', role_ref='<@&77>')
    assert summary == '<#10> | ⭐ | **≥ 5** | bypass <@&77>'


def test_board_summary_plain_appends_bypass_segment():
    summary = sc.board_summary(_config(), '#general', markdown=False,
                               role_ref='@Mods')
    assert summary == '#general | ⭐ | ≥ 5 | bypass @Mods'


def test_board_label_carries_resolved_bypass_role_name():
    guild = _FakeGuild({}, {77: 'Mods'})
    label = sc.board_label(_config(bypass_role_id=77), channel_name='general',
                           guild=guild)
    assert label == '#general | ⭐ | ≥ 5 | bypass @Mods'


def test_board_label_with_bypass_role_still_respects_the_length_limit():
    # A long channel name eats the trailing bypass segment — the length bound
    # wins. Asserted on its own fixture: "contains @Mods" and "fits the limit"
    # cannot both hold here.
    guild = _FakeGuild({}, {77: 'Mods'})
    label = sc.board_label(_config(bypass_role_id=77), channel_name='x' * 200,
                           guild=guild)
    assert len(label) <= sc._AUTOCOMPLETE_LABEL_MAX


# --- build_list_embeds ------------------------------------------------------


def test_build_list_embeds_single_embed():
    configs = [_config(id=1), _config(id=2, emoji='🔥', enabled=False)]
    embeds = sc.build_list_embeds(configs)
    assert len(embeds) == 1
    lines = embeds[0].description.splitlines()
    assert len(lines) == 2
    # Numbered Markdown list using the standardized pipe format; no board id and
    # no legacy "·" bullet separators.
    assert lines[0] == '1. <#10> | ⭐ | **≥ 5**'
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


def test_build_list_embeds_shows_the_bypass_role():
    embeds = sc.build_list_embeds([_config(bypass_role_id=77)])
    assert embeds[0].description.splitlines()[0] == '1. <#10> | ⭐ | **≥ 5** | bypass <@&77>'


def test_build_list_embeds_keeps_disabled_after_the_bypass_segment():
    embeds = sc.build_list_embeds([_config(bypass_role_id=77, enabled=False)])
    line = embeds[0].description.splitlines()[0]
    assert line.endswith('| bypass <@&77> | disabled')


# --- _board_choices autocomplete filtering ----------------------------------


class _FakeGuild:
    def __init__(self, channels, roles=None):
        self._channels = channels  # {channel_id: name}
        self._roles = roles or {}  # {role_id: name}

    def get_channel(self, cid):
        name = self._channels.get(cid)
        return SimpleNamespace(name=name) if name else None

    def get_role(self, rid):
        name = self._roles.get(rid)
        return SimpleNamespace(id=rid, name=name) if name else None


class _FakeAcInteraction:
    def __init__(self, guild_id, channels, roles=None):
        self.guild_id = guild_id
        self.guild = _FakeGuild(channels, roles)


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


def test_board_choices_labels_resolve_the_bypass_role_through_the_guild():
    # `_board_choices` must hand the guild to `board_label`, or the label falls
    # back to the raw role id.
    starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                bypass_role_id=77)
    interaction = _FakeAcInteraction(1, {10: 'book-club'}, {77: 'Mods'})
    label = next(iter(sc.StarboardCommands._board_choices(interaction)))
    assert label == '#book-club | ⭐ | ≥ 5 | bypass @Mods'


# --- create/update/remove through the helper (DB + cache invalidation) ------


def test_add_config_persists_and_invalidates_cache():
    # Prime the cache for the guild so we can prove the mutator drops it.
    assert starboard_helper.get_enabled_configs(1) == []
    cfg = starboard_helper.add_config(
        guild_id=1, target_channel_id=10, emoji='book', emoji_id=123,
        threshold=4)
    # DB state reflects what /starboard add would persist (custom emoji → id set).
    stored = starboard_helper.get_config(cfg.id)
    assert stored.emoji == 'book'
    assert stored.emoji_id == 123
    assert stored.threshold == 4
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


# --- add / edit callbacks: the bypass-role branches --------------------------


class _FakeCmdInteraction:
    """Minimal stand-in for a slash interaction: records what would be sent and
    resolves the guild bits ``add``/``edit`` reach for."""

    def __init__(self, guild_id=1, roles=None):
        self.guild_id = guild_id
        self.guild = SimpleNamespace(
            emojis=[], me=object(),
            get_channel=lambda cid: None,
            get_role=lambda rid: SimpleNamespace(id=rid, name=(roles or {}).get(rid)))
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)

    @property
    def last_description(self):
        return self.sent[-1]['embed'].description


def _fake_channel(id=10):
    # `_perms_warning` needs a permissions object; grant both so the confirmation
    # carries no trailing warning.
    return SimpleNamespace(
        id=id, mention=f'<#{id}>',
        permissions_for=lambda _me: SimpleNamespace(send_messages=True,
                                                    embed_links=True))


async def _run_add(interaction, **kw):
    cog = sc.StarboardCommands(bot=None)
    kw.setdefault('channel', _fake_channel())
    kw.setdefault('emoji', '⭐')
    kw.setdefault('threshold', 5)
    kw.setdefault('role', None)
    await cog.add.callback(cog, interaction, **kw)


async def _run_edit(interaction, config, **kw):
    cog = sc.StarboardCommands(bot=None)
    kw.setdefault('threshold', None)
    kw.setdefault('enabled', None)
    kw.setdefault('channel', None)
    kw.setdefault('emoji', None)
    kw.setdefault('role', None)
    kw.setdefault('clear_role', None)
    await cog.edit.callback(cog, interaction, starboard=str(config.id), **kw)


@pytest.mark.asyncio
async def test_add_persists_the_bypass_role_and_echoes_it():
    interaction = _FakeCmdInteraction()
    await _run_add(interaction, role=_role(id=77, name='Mods'))
    stored = starboard_helper.get_configs(1)[0]
    assert stored.bypass_role_id == 77
    assert 'bypass <@&77>' in interaction.last_description


@pytest.mark.asyncio
async def test_add_without_a_role_leaves_the_board_unbypassed():
    interaction = _FakeCmdInteraction()
    await _run_add(interaction)
    assert starboard_helper.get_configs(1)[0].bypass_role_id is None
    assert 'bypass' not in interaction.last_description


@pytest.mark.asyncio
async def test_add_rejects_everyone_and_writes_nothing():
    interaction = _FakeCmdInteraction()
    await _run_add(interaction, role=_role(id=1, name='@everyone', default=True))
    assert starboard_helper.get_configs(1) == []
    assert interaction.sent[-1]['ephemeral'] is True
    assert '@everyone' in interaction.last_description


@pytest.mark.asyncio
async def test_edit_sets_the_bypass_role_and_confirms_it():
    config = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐')
    interaction = _FakeCmdInteraction()
    await _run_edit(interaction, config, role=_role(id=77, name='Mods'))
    assert starboard_helper.get_config(config.id).bypass_role_id == 77
    # Without the role_ref on the edit confirmation this reports success without
    # echoing the value it just set.
    assert 'bypass <@&77>' in interaction.last_description


@pytest.mark.asyncio
async def test_edit_clear_role_nulls_the_column():
    config = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                         bypass_role_id=77)
    interaction = _FakeCmdInteraction()
    await _run_edit(interaction, config, clear_role=True)
    assert starboard_helper.get_config(config.id).bypass_role_id is None
    assert 'bypass' not in interaction.last_description


@pytest.mark.asyncio
async def test_edit_omitting_clear_role_leaves_the_bypass_role_alone():
    # An unsupplied optional bool arrives as None, which must be a no-op rather
    # than a clear — otherwise `/starboard edit threshold:8` silently unbypasses.
    config = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                         bypass_role_id=77)
    interaction = _FakeCmdInteraction()
    await _run_edit(interaction, config, threshold=8)
    stored = starboard_helper.get_config(config.id)
    assert stored.bypass_role_id == 77
    assert stored.threshold == 8


@pytest.mark.asyncio
async def test_edit_rejects_role_and_clear_role_together_without_writing():
    config = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                         bypass_role_id=77, threshold=5)
    interaction = _FakeCmdInteraction()
    await _run_edit(interaction, config, role=_role(id=88, name='Helpers'),
                    clear_role=True, threshold=9)
    stored = starboard_helper.get_config(config.id)
    assert stored.bypass_role_id == 77  # untouched
    assert stored.threshold == 5        # the whole edit was refused
    assert interaction.sent[-1]['ephemeral'] is True
    assert 'not both' in interaction.last_description


@pytest.mark.asyncio
async def test_edit_rejects_everyone_and_writes_nothing():
    config = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                         threshold=5)
    interaction = _FakeCmdInteraction()
    await _run_edit(interaction, config,
                    role=_role(id=1, name='@everyone', default=True), threshold=9)
    stored = starboard_helper.get_config(config.id)
    assert stored.bypass_role_id is None
    assert stored.threshold == 5
    assert interaction.sent[-1]['ephemeral'] is True
    assert '@everyone' in interaction.last_description


@pytest.mark.asyncio
async def test_remove_confirmation_names_the_bypass_role():
    config = starboard_helper.add_config(guild_id=1, target_channel_id=10, emoji='⭐',
                                         bypass_role_id=77)
    cog = sc.StarboardCommands(bot=None)
    interaction = _FakeCmdInteraction()
    await cog.remove.callback(cog, interaction, starboard=str(config.id))
    assert 'bypass <@&77>' in interaction.last_description
    assert starboard_helper.get_config(config.id) is None
