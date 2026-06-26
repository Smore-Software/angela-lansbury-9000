"""The shared add/edit modal for an anniversary entry.

A Discord modal must be the *direct* response to an interaction and cannot hold a
select, so the channel is chosen first in ``ChannelChoiceView`` and handed in as
``channel_id``; the modal only collects the free-text fields. The same modal backs
both ``/anniversary add`` (``entry is None``) and ``/anniversary edit`` (``entry``
prefilled), mirroring ``bot/cogs/polls/views/create_poll_modal.py``.

Discord caps a modal at five ``TextInput`` rows; the five below sit exactly at
that limit. A modal also cannot be reopened to correct a validation error, so a
bad date is surfaced as an ephemeral message asking the submitter to re-run rather
than re-rendering the modal — ``anniversary_utils`` parsing stays forgiving.
"""
import nextcord
from nextcord import Interaction

from bot.cogs.anniversary import anniversary_utils
from bot.utils import messages
from db.helpers import anniversary_helper


class AnniversaryModal(nextcord.ui.Modal):
    def __init__(self, channel_id: int, *, entry=None):
        super().__init__(title='Add an anniversary' if entry is None else 'Edit anniversary')
        # The channel chosen (add) or re-confirmed (edit) in the picker; the modal
        # itself never picks a channel because it cannot hold a select.
        self.channel_id = channel_id
        self.entry = entry

        self.title_input = nextcord.ui.TextInput(
            label='Title', placeholder='Anniversary', required=False, max_length=200,
            default_value=(entry.title if entry else None))
        self.add_item(self.title_input)

        self.date_input = nextcord.ui.TextInput(
            label='Date', placeholder='MM/DD', required=True, max_length=10,
            default_value=(f'{entry.month:02d}/{entry.day:02d}' if entry else None))
        self.add_item(self.date_input)

        self.year_input = nextcord.ui.TextInput(
            label='Year', placeholder='YYYY (for the count)', required=False, max_length=4,
            default_value=(str(entry.year) if entry and entry.year else None))
        self.add_item(self.year_input)

        self.label_input = nextcord.ui.TextInput(
            label='Count word', placeholder='Anniversary', required=False, max_length=100,
            default_value=(entry.count_label if entry else None))
        self.add_item(self.label_input)

        self.message_input = nextcord.ui.TextInput(
            label='Message', placeholder='Your own words (optional).', required=False,
            style=nextcord.TextInputStyle.paragraph, max_length=1500,
            default_value=(entry.message if entry else None))
        self.add_item(self.message_input)

    async def callback(self, interaction: Interaction) -> None:
        try:
            month, day = anniversary_utils.parse_month_day(self.date_input.value)
            year = anniversary_utils.parse_year(self.year_input.value)
        except ValueError as e:
            # A modal can't be reopened to fix the field, so report and ask to re-run.
            return await interaction.response.send_message(
                embed=messages.error(f'{e} Please run the command again.'), ephemeral=True)

        title = (self.title_input.value or '').strip() or None
        count_label = (self.label_input.value or '').strip() or None
        message = (self.message_input.value or '').strip() or None

        if self.entry is None:
            saved = anniversary_helper.add(
                guild_id=interaction.guild_id, user_id=interaction.user.id,
                channel_id=self.channel_id, title=title, count_label=count_label,
                message=message, month=month, day=day, year=year)
            if saved is None:
                return await interaction.response.send_message(
                    embed=messages.error(
                        'You already have an anniversary with that title on that date.'),
                    ephemeral=True)
            verb = 'Saved'
        else:
            saved = anniversary_helper.update(
                self.entry.id, title=title, count_label=count_label, message=message,
                month=month, day=day, year=year, channel_id=self.channel_id)
            if saved is None:
                return await interaction.response.send_message(
                    embed=messages.error('That anniversary no longer exists.'), ephemeral=True)
            verb = 'Updated'

        preview = anniversary_utils.post_embed(saved, nextcord.utils.utcnow().year)
        await interaction.response.send_message(
            content=f'{verb}! Here\'s how it will appear in <#{saved.channel_id}>:',
            embed=preview, ephemeral=True)
