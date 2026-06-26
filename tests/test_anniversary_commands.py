"""Tests for the /anniversary command cog's testable units.

Full slash-command round-trips need a live gateway, so we test the extracted
static/module logic instead: authorization (`_can_manage`), the autocomplete
choice builders (`_entry_choices` / `_channel_choices` — shape, owner-vs-manager
scoping, the 25-cap, substring filtering), the id resolvers, and a registration
smoke check that the cog is exported from ``bot.cogs``. Entries/channels are
created through the real helpers against the temp-sqlite fixture; interactions are
``SimpleNamespace`` fakes exposing only the attributes the code reads.
"""
from types import SimpleNamespace

from bot.cogs.anniversary import anniversary_commands as ac
from db.helpers import anniversary_helper, anniversary_channel_helper


def _interaction(guild_id=1, user_id=100, manage_guild=False):
    return SimpleNamespace(
        guild_id=guild_id,
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild)))


def _entry(guild_id=1, user_id=100, id=1):
    return SimpleNamespace(id=id, guild_id=guild_id, user_id=user_id)


def _add_entry(guild_id=1, user_id=100, title='Wedding', month=6, day=25, year=None):
    return anniversary_helper.add(
        guild_id=guild_id, user_id=user_id, channel_id=10, title=title,
        count_label=None, message=None, month=month, day=day, year=year)


# --- _can_manage ------------------------------------------------------------


def test_can_manage_owner_yes():
    interaction = _interaction(guild_id=1, user_id=100)
    assert ac.AnniversaryCommands._can_manage(interaction, _entry(1, 100)) is True


def test_can_manage_manager_yes_for_another_users_entry():
    interaction = _interaction(guild_id=1, user_id=999, manage_guild=True)
    assert ac.AnniversaryCommands._can_manage(interaction, _entry(1, 100)) is True


def test_can_manage_unrelated_member_no():
    interaction = _interaction(guild_id=1, user_id=999, manage_guild=False)
    assert ac.AnniversaryCommands._can_manage(interaction, _entry(1, 100)) is False


def test_can_manage_wrong_guild_no_even_for_owner():
    # Same user id, but the entry belongs to another guild → not reachable here.
    interaction = _interaction(guild_id=2, user_id=100)
    assert ac.AnniversaryCommands._can_manage(interaction, _entry(1, 100)) is False


def test_can_manage_wrong_guild_no_even_for_manager():
    interaction = _interaction(guild_id=2, user_id=999, manage_guild=True)
    assert ac.AnniversaryCommands._can_manage(interaction, _entry(1, 100)) is False


def test_can_manage_none_entry_no():
    interaction = _interaction(guild_id=1, user_id=100)
    assert ac.AnniversaryCommands._can_manage(interaction, None) is False


# --- _resolve_entry ---------------------------------------------------------


def test_resolve_entry_returns_row():
    entry = _add_entry()
    assert ac.AnniversaryCommands._resolve_entry(str(entry.id)).id == entry.id


def test_resolve_entry_malformed_or_missing_returns_none():
    assert ac.AnniversaryCommands._resolve_entry('not-an-int') is None
    assert ac.AnniversaryCommands._resolve_entry('99999') is None
    assert ac.AnniversaryCommands._resolve_entry(None) is None


# --- _resolve_channel (cross-guild safety) ----------------------------------


def test_resolve_channel_returns_row_for_own_guild():
    anniversary_channel_helper.add_channel(1, 555, 'Remembrances')
    interaction = _interaction(guild_id=1)
    resolved = ac.AnniversaryCommands._resolve_channel(interaction, '555')
    assert resolved is not None and resolved.channel_id == 555


def test_resolve_channel_rejects_other_guild_and_malformed():
    anniversary_channel_helper.add_channel(1, 555, 'Remembrances')
    # Another guild can't resolve guild 1's registered channel.
    assert ac.AnniversaryCommands._resolve_channel(_interaction(guild_id=2), '555') is None
    assert ac.AnniversaryCommands._resolve_channel(_interaction(guild_id=1), 'nope') is None


# --- _entry_choices ---------------------------------------------------------


def test_entry_choices_shape_is_label_to_str_id():
    entry = _add_entry(title='Wedding')
    interaction = _interaction(guild_id=1, user_id=100)
    choices = ac.AnniversaryCommands._entry_choices(interaction)
    assert choices == {ac.entry_label(entry): str(entry.id)}
    # Values are stringified ids.
    assert all(isinstance(v, str) for v in choices.values())


def test_entry_choices_owner_sees_only_their_own():
    mine = _add_entry(user_id=100, title='Mine')
    _add_entry(user_id=200, title='Theirs')
    interaction = _interaction(guild_id=1, user_id=100, manage_guild=False)
    choices = ac.AnniversaryCommands._entry_choices(interaction)
    assert list(choices.values()) == [str(mine.id)]


def test_entry_choices_manager_sees_all_guild_entries():
    a = _add_entry(user_id=100, title='Aaa')
    b = _add_entry(user_id=200, title='Bbb')
    interaction = _interaction(guild_id=1, user_id=999, manage_guild=True)
    choices = ac.AnniversaryCommands._entry_choices(interaction)
    assert set(choices.values()) == {str(a.id), str(b.id)}


def test_entry_choices_excludes_other_guilds():
    mine = _add_entry(guild_id=1, user_id=100, title='Here')
    _add_entry(guild_id=2, user_id=100, title='Elsewhere')
    interaction = _interaction(guild_id=1, user_id=100, manage_guild=True)
    choices = ac.AnniversaryCommands._entry_choices(interaction)
    assert list(choices.values()) == [str(mine.id)]


def test_entry_choices_substring_filter_case_insensitive():
    wedding = _add_entry(user_id=100, title='Wedding', month=6, day=25)
    _add_entry(user_id=100, title='Adoption Day', month=7, day=1)
    interaction = _interaction(guild_id=1, user_id=100)
    choices = ac.AnniversaryCommands._entry_choices(interaction, 'WED')
    assert list(choices.values()) == [str(wedding.id)]


def test_entry_choices_capped_at_25():
    for i in range(30):
        _add_entry(user_id=100, title=f'Day {i:02d}', month=1, day=(i % 28) + 1)
    interaction = _interaction(guild_id=1, user_id=100)
    assert len(ac.AnniversaryCommands._entry_choices(interaction)) == 25


# --- _channel_choices -------------------------------------------------------


def test_channel_choices_shape_is_label_to_str_channel_id():
    anniversary_channel_helper.add_channel(1, 777, 'Remembrances')
    interaction = _interaction(guild_id=1)
    choices = ac.AnniversaryCommands._channel_choices(interaction)
    assert choices == {'Remembrances': '777'}


def test_channel_choices_substring_filter_case_insensitive():
    anniversary_channel_helper.add_channel(1, 777, 'Remembrances')
    anniversary_channel_helper.add_channel(1, 888, 'Celebrations')
    interaction = _interaction(guild_id=1)
    choices = ac.AnniversaryCommands._channel_choices(interaction, 'celeb')
    assert list(choices.values()) == ['888']


def test_channel_choices_excludes_other_guilds():
    anniversary_channel_helper.add_channel(1, 777, 'Here')
    anniversary_channel_helper.add_channel(2, 888, 'Elsewhere')
    interaction = _interaction(guild_id=1)
    choices = ac.AnniversaryCommands._channel_choices(interaction)
    assert list(choices.values()) == ['777']


def test_channel_choices_capped_at_25():
    for i in range(30):
        anniversary_channel_helper.add_channel(1, 1000 + i, f'Channel {i:02d}')
    interaction = _interaction(guild_id=1)
    assert len(ac.AnniversaryCommands._channel_choices(interaction)) == 25


# --- registration smoke -----------------------------------------------------


def test_cog_is_exported_from_bot_cogs():
    from bot.cogs import AnniversaryCommands
    assert AnniversaryCommands is ac.AnniversaryCommands
    assert 'AnniversaryCommands' in __import__('bot.cogs', fromlist=['__all__']).__all__
