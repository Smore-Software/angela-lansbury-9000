"""Tests for the anniversary channel picker view.

The view exists because a modal can't hold a select and must be the *direct*
response to an interaction. The regression these guard is crit r_6662a2: a sole
preselected ``StringSelect`` option never fires a change event, so the modal must
be reached via the **Continue** button instead — including the single-channel
case, which would otherwise dead-end. The live ``send_modal`` /
``edit_original_message`` calls hit the gateway, so we drive the callbacks against
fake interactions that just record what they were asked to do.
"""
from types import SimpleNamespace

import nextcord

from bot.cogs.anniversary.views.channel_choice_view import ChannelChoiceView
from bot.cogs.anniversary.views.anniversary_modal import AnniversaryModal


def _channel(channel_id):
    return SimpleNamespace(channel_id=channel_id)


def _guild(names=None):
    names = names or {}
    return SimpleNamespace(
        get_channel=lambda cid: SimpleNamespace(name=names[cid]) if cid in names else None)


class _Response:
    def __init__(self):
        self.modal = None
        self.deferred = False
        self.messages = []

    async def send_modal(self, modal):
        self.modal = modal

    async def defer(self):
        self.deferred = True

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _Interaction:
    def __init__(self):
        self.response = _Response()
        self.edited = None

    async def edit_original_message(self, **kwargs):
        self.edited = kwargs


# --- single-channel path (the dead-end this fixes) --------------------------


async def test_single_channel_continue_reaches_modal_without_touching_select():
    # One channel, preselected: the user never touches the select, so Continue must
    # still open the modal for that channel.
    view = ChannelChoiceView(
        [_channel(777)], guild=_guild({777: 'remembrances'}), preselected_channel_id=777)
    assert view.selected_channel_id == 777

    interaction = _Interaction()
    await view._on_continue(interaction)

    assert isinstance(interaction.response.modal, AnniversaryModal)
    assert interaction.response.modal.channel_id == 777
    assert interaction.response.modal.entry is None


async def test_continue_edits_original_message_into_confirmation():
    view = ChannelChoiceView(
        [_channel(777)], guild=_guild({777: 'remembrances'}), preselected_channel_id=777)
    interaction = _Interaction()
    await view._on_continue(interaction)

    # The picker is replaced by a plain confirmation; the now-stale selector is gone.
    assert interaction.edited['content'] == 'Posting to <#777>.'
    assert interaction.edited['view'] is None


# --- multi-channel path -----------------------------------------------------


async def test_multi_channel_routes_to_user_choice():
    view = ChannelChoiceView(
        [_channel(777), _channel(888)],
        guild=_guild({777: 'a', 888: 'b'}))
    # Nothing preselected for 2+ channels until the user picks.
    assert view.selected_channel_id is None

    # User picks the second channel: the select records it and defers (no modal yet).
    view.select._selected_values = ['888']
    select_interaction = _Interaction()
    await view._on_select(select_interaction)
    assert view.selected_channel_id == 888
    assert select_interaction.response.deferred is True
    assert select_interaction.response.modal is None

    # Continue then opens the modal for the chosen channel and confirms it.
    continue_interaction = _Interaction()
    await view._on_continue(continue_interaction)
    assert continue_interaction.response.modal.channel_id == 888
    assert continue_interaction.edited['content'] == 'Posting to <#888>.'


async def test_continue_without_a_choice_asks_for_one():
    view = ChannelChoiceView(
        [_channel(777), _channel(888)], guild=_guild({777: 'a', 888: 'b'}))
    interaction = _Interaction()
    await view._on_continue(interaction)

    # No modal, no destructive edit — just a nudge to pick a channel.
    assert interaction.response.modal is None
    assert interaction.edited is None
    assert interaction.response.messages[0]['content'] == 'Please choose a channel first.'


# --- option labelling -------------------------------------------------------


async def test_options_render_resolved_names_with_id_fallback():
    # 777 resolves to a live channel (#name); 888 was deleted (unmapped) -> the
    # never-empty ``Channel <id>`` fallback. Labels carry the meaning because a
    # select can't render a <#id> mention.
    view = ChannelChoiceView(
        [_channel(777), _channel(888)], guild=_guild({777: 'remembrances'}))
    labels = [option.label for option in view.select.options]
    assert labels == ['#remembrances', 'Channel 888']


# --- edit path --------------------------------------------------------------


async def test_edit_carries_entry_into_modal_single_channel():
    entry = SimpleNamespace(id=5, channel_id=777, title='Wedding', month=6, day=25,
                            year=2000, count_label=None, message=None)
    view = ChannelChoiceView(
        [_channel(777)], guild=_guild({777: 'remembrances'}),
        preselected_channel_id=777, entry=entry)
    # The current channel seeds the selection so Continue works untouched.
    assert view.selected_channel_id == 777

    interaction = _Interaction()
    await view._on_continue(interaction)
    assert interaction.response.modal.channel_id == 777
    assert interaction.response.modal.entry is entry
