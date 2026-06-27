"""The channel picker shown before the anniversary modal.

A modal cannot hold a select, and a modal must be the *direct* response to an
interaction — so the channel is chosen here first, and the select's component
interaction is what opens ``AnniversaryModal`` via ``send_modal``. The same view
backs both add and edit: ``/anniversary add`` preselects the sole registered
channel (Decision 5), ``/anniversary edit`` preselects the entry's current channel
and carries the ``entry`` through so the modal opens prefilled.
"""
import nextcord

from bot.cogs.anniversary import anniversary_utils
from bot.cogs.anniversary.views.anniversary_modal import AnniversaryModal


class ChannelChoiceView(nextcord.ui.View):
    def __init__(self, channels, *, guild=None, preselected_channel_id: int = None,
                 entry=None, timeout: float = 180):
        super().__init__(timeout=timeout)
        # Carried through to the modal so edit reopens prefilled against the entry.
        self.entry = entry
        # Each registry row only stores a channel_id, so the option is labelled by
        # the channel's own resolved name (``#name``, or a ``Channel <id>`` fallback
        # for a deleted channel) — never an empty label, which nextcord rejects.
        options = [
            nextcord.SelectOption(
                label=anniversary_utils.channel_display_name(guild, channel.channel_id),
                value=str(channel.channel_id),
                default=(channel.channel_id == preselected_channel_id))
            for channel in channels
        ]
        self.select = nextcord.ui.StringSelect(
            placeholder='Choose a channel for this anniversary',
            min_values=1, max_values=1, options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: nextcord.Interaction) -> None:
        channel_id = int(self.select.values[0])
        # send_modal must be the direct response to this component interaction.
        await interaction.response.send_modal(AnniversaryModal(channel_id, entry=self.entry))
