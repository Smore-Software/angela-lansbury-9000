"""The shared add/edit modal for an anniversary entry.

A Discord modal must be the *direct* response to an interaction and cannot hold a
select, so the channel is chosen first in ``ChannelChoiceView`` and handed in as
``channel_id``; the modal only collects the free-text fields. The same modal backs
both ``/anniversary add`` (``entry is None``) and ``/anniversary edit`` (``entry``
prefilled), mirroring ``bot/cogs/polls/views/create_poll_modal.py``.

Submitting does NOT persist: the callback validates, builds a not-yet-saved
pending entry, and hands it to ``AnniversaryPreviewView`` as an ephemeral
confirm-to-save preview. Confirm there is what writes; Edit there reopens this
modal prefilled. The optional ``prefill`` source is what populates the fields —
defaulting to ``entry`` on the first open, but the *pending* values on an Edit
re-open, while ``entry`` keeps tracking the real edit target (or ``None`` for add).

Discord caps a modal at five ``TextInput`` rows; the five below sit exactly at
that limit. A modal also cannot be reopened to correct a validation error, so a
bad date is surfaced as an ephemeral message asking the submitter to re-run rather
than re-rendering the modal — ``anniversary_utils`` parsing stays forgiving.
"""
import nextcord
from nextcord import Interaction

from bot.cogs.anniversary import anniversary_utils
from bot.utils import messages


class AnniversaryModal(nextcord.ui.Modal):
    def __init__(self, channel_id: int, *, entry=None, prefill=None):
        super().__init__(title='Add an anniversary' if entry is None else 'Edit anniversary')
        # The channel chosen (add) or re-confirmed (edit) in the picker; the modal
        # itself never picks a channel because it cannot hold a select.
        self.channel_id = channel_id
        # The real edit target (None => add); drives Confirm routing downstream and
        # rides through an Edit re-open unchanged.
        self.entry = entry
        # What the fields default to: the just-entered pending values on an Edit
        # re-open, else the edit target on first open, else nothing (a fresh add).
        source = prefill if prefill is not None else entry

        self.title_input = nextcord.ui.TextInput(
            label='Title', placeholder='Anniversary', required=False, max_length=200,
            default_value=(source.title if source else None))
        self.add_item(self.title_input)

        self.date_input = nextcord.ui.TextInput(
            label='Date', placeholder='MM/DD', required=True, max_length=10,
            default_value=(f'{source.month:02d}/{source.day:02d}' if source else None))
        self.add_item(self.date_input)

        self.year_input = nextcord.ui.TextInput(
            label='Year', placeholder='YYYY (for the count)', required=False, max_length=4,
            default_value=(str(source.year) if source and source.year else None))
        self.add_item(self.year_input)

        self.label_input = nextcord.ui.TextInput(
            label='Count word', placeholder='Anniversary', required=False, max_length=100,
            default_value=(source.count_label if source else None))
        self.add_item(self.label_input)

        self.message_input = nextcord.ui.TextInput(
            label='Message', placeholder='Your own words (optional).', required=False,
            style=nextcord.TextInputStyle.paragraph, max_length=1500,
            default_value=(source.message if source else None))
        self.add_item(self.message_input)

    async def callback(self, interaction: Interaction) -> None:
        # Imported here, not at module scope, to break the modal ↔ preview-view
        # import cycle (the preview view imports this modal for its Edit button).
        from bot.cogs.anniversary.views.anniversary_preview_view import \
            AnniversaryPreviewView

        try:
            month, day = anniversary_utils.parse_month_day(self.date_input.value)
            year = anniversary_utils.parse_year(self.year_input.value)
        except ValueError as e:
            # A modal can't be reopened to fix the field, so report and ask to re-run.
            return await interaction.response.send_message(
                embed=messages.error(f'{e} Please run the command again.'), ephemeral=True)

        # Build — but do NOT persist — the entry; Confirm in the preview saves it.
        pending = anniversary_utils.build_pending_entry(
            title=self.title_input.value, count_label=self.label_input.value,
            message=self.message_input.value, month=month, day=day, year=year,
            channel_id=self.channel_id)
        preview = anniversary_utils.post_embed(pending, nextcord.utils.utcnow().year)
        view = AnniversaryPreviewView(
            pending=pending, entry=self.entry,
            guild_id=interaction.guild_id, user_id=interaction.user.id)
        await interaction.response.send_message(
            content=(f'Here\'s how it will appear in <#{pending.channel_id}>. '
                     'Confirm to save, or Edit to make changes.'),
            embed=preview, view=view, ephemeral=True)
