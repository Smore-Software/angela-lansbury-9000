"""Tests for the anniversary confirm-to-save preview view.

The view is the post-modal-submit step: the modal builds a not-yet-persisted
pending entry and hands it here, so **Confirm** is the only thing that writes and
**Edit** reopens the prefilled modal. The live ``add``/``update`` calls and the
``edit_message`` / ``send_modal`` / ``edit_original_message`` responses hit the DB
and gateway, so — as in ``tests/test_channel_choice_view.py`` — we drive the
button callbacks against fake interactions and a patched helper that just records
what it was asked to do. The button-round-trip itself is covered by the manual
verification note in the completion report; these lock the routing/state logic.
"""
from types import SimpleNamespace

from bot.cogs.anniversary import anniversary_utils as au
from bot.cogs.anniversary.views import anniversary_preview_view
from bot.cogs.anniversary.views.anniversary_modal import AnniversaryModal
from bot.cogs.anniversary.views.anniversary_preview_view import AnniversaryPreviewView
from bot.utils import messages


class _Response:
    def __init__(self):
        self.edited = None
        self.modal = None
        self.messages = []

    async def edit_message(self, **kwargs):
        self.edited = kwargs

    async def send_modal(self, modal):
        self.modal = modal

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _Interaction:
    def __init__(self):
        self.response = _Response()
        self.edited_original = None

    async def edit_original_message(self, **kwargs):
        self.edited_original = kwargs


def _pending(channel_id=42, title='Wedding', count_label='Year', message='hi',
            month=6, day=25, year=2020):
    return au.build_pending_entry(
        title=title, count_label=count_label, message=message,
        month=month, day=day, year=year, channel_id=channel_id)


def _button(view, label):
    return next(child for child in view.children if child.label == label)


# --- Confirm: add mode ------------------------------------------------------


async def test_confirm_add_calls_add_and_disables_buttons(monkeypatch):
    captured = {}

    def fake_add(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(channel_id=kwargs['channel_id'], title=kwargs['title'],
                               count_label=kwargs['count_label'], message=kwargs['message'],
                               month=kwargs['month'], day=kwargs['day'], year=kwargs['year'])

    monkeypatch.setattr(anniversary_preview_view.anniversary_helper, 'add', fake_add)

    pending = _pending(channel_id=42)
    view = AnniversaryPreviewView(pending=pending, entry=None, guild_id=1, user_id=2)
    interaction = _Interaction()
    await _button(view, 'Confirm').callback(interaction)

    # add() got the pending values plus the fresh-row guild/user ids.
    assert captured['guild_id'] == 1 and captured['user_id'] == 2
    assert captured['channel_id'] == 42
    assert (captured['title'], captured['count_label'], captured['message']) == (
        'Wedding', 'Year', 'hi')
    assert (captured['month'], captured['day'], captured['year']) == (6, 25, 2020)

    # The message is replaced by the saved embed and both buttons are disabled.
    assert interaction.response.edited['content'] == 'Saved! It will appear in <#42>.'
    assert interaction.response.edited['embed'].title == 'Wedding'
    assert all(child.disabled for child in view.children)


async def test_confirm_add_collision_reports_error_and_keeps_buttons(monkeypatch):
    monkeypatch.setattr(anniversary_preview_view.anniversary_helper, 'add',
                        lambda **kwargs: None)

    view = AnniversaryPreviewView(pending=_pending(), entry=None, guild_id=1, user_id=2)
    interaction = _Interaction()
    await _button(view, 'Confirm').callback(interaction)

    # Nothing committed: ephemeral error, the preview message is left untouched, and
    # the buttons stay live so the user can adjust via Edit and retry.
    assert interaction.response.edited is None
    error = interaction.response.messages[0]
    assert error['ephemeral'] is True
    assert error['embed'].color.value == messages.error('x').color.value
    assert not any(child.disabled for child in view.children)


# --- Confirm: edit mode -----------------------------------------------------


async def test_confirm_edit_calls_update_on_target_entry(monkeypatch):
    captured = {}

    def fake_update(entry_id, **fields):
        captured['id'] = entry_id
        captured.update(fields)
        return SimpleNamespace(channel_id=fields['channel_id'], title=fields['title'],
                               count_label=fields['count_label'], message=fields['message'],
                               month=fields['month'], day=fields['day'], year=fields['year'])

    monkeypatch.setattr(anniversary_preview_view.anniversary_helper, 'update', fake_update)

    entry = SimpleNamespace(id=99)
    pending = _pending(channel_id=7, title='Renamed')
    view = AnniversaryPreviewView(pending=pending, entry=entry, guild_id=1, user_id=2)
    interaction = _Interaction()
    await _button(view, 'Confirm').callback(interaction)

    # Routed to update on the target row with the pending fields (incl. channel).
    assert captured['id'] == 99
    assert captured['title'] == 'Renamed'
    assert captured['channel_id'] == 7
    assert interaction.response.edited['content'] == 'Updated! It will appear in <#7>.'
    assert all(child.disabled for child in view.children)


async def test_confirm_edit_collision_reports_error(monkeypatch):
    monkeypatch.setattr(anniversary_preview_view.anniversary_helper, 'update',
                        lambda entry_id, **fields: None)

    view = AnniversaryPreviewView(
        pending=_pending(), entry=SimpleNamespace(id=99), guild_id=1, user_id=2)
    interaction = _Interaction()
    await _button(view, 'Confirm').callback(interaction)

    assert interaction.response.edited is None
    assert interaction.response.messages[0]['ephemeral'] is True
    assert not any(child.disabled for child in view.children)


# --- Edit -------------------------------------------------------------------


async def test_edit_reopens_modal_prefilled_and_clears_view():
    entry = SimpleNamespace(id=99)
    pending = _pending(channel_id=7)
    view = AnniversaryPreviewView(pending=pending, entry=entry, guild_id=1, user_id=2)
    interaction = _Interaction()
    await _button(view, 'Edit').callback(interaction)

    # The modal is the direct response, prefilled from the pending values and
    # carrying the edit target + channel through unchanged.
    modal = interaction.response.modal
    assert isinstance(modal, AnniversaryModal)
    assert modal.channel_id == 7
    assert modal.entry is entry
    # prefill drives the fields: the just-entered title shows as the default.
    assert modal.title_input.default_value == 'Wedding'

    # The stale preview buttons are retired so the re-submitted modal's fresh
    # preview is the only live control set.
    assert interaction.edited_original['view'] is None


async def test_edit_in_add_mode_carries_none_entry():
    view = AnniversaryPreviewView(
        pending=_pending(channel_id=5), entry=None, guild_id=1, user_id=2)
    interaction = _Interaction()
    await _button(view, 'Edit').callback(interaction)

    modal = interaction.response.modal
    assert modal.entry is None
    assert modal.channel_id == 5
