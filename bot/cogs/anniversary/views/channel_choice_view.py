"""The channel picker shown before the anniversary modal.

A modal cannot hold a select, and a modal must be the *direct* response to an
interaction — so the channel is chosen here first, and a **Continue** button's
component interaction is what opens ``AnniversaryModal`` via ``send_modal``. The
select on its own can't drive this: a ``StringSelect`` only fires its callback on
a *change* event, so a sole preselected option (the single-channel case) can never
fire and would dead-end before the modal (crit r_6662a2). The Continue button
gives a uniform flow for one or many channels.

The same view backs both add and edit: ``/anniversary add`` preselects the sole
registered channel, ``/anniversary edit`` preselects the entry's current channel
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
        # The channel Continue will open the modal for. Seeded from the preselected
        # option so Continue works without the user ever touching the select (the
        # single-channel case can't emit a change event); updated by ``_on_select``
        # whenever the user picks a different channel.
        self.selected_channel_id = preselected_channel_id
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

        self.continue_button = nextcord.ui.Button(
            label='Continue', style=nextcord.ButtonStyle.primary)
        self.continue_button.callback = self._on_continue
        self.add_item(self.continue_button)

    async def _on_select(self, interaction: nextcord.Interaction) -> None:
        # Just record the pick and acknowledge; the modal opens from Continue so the
        # flow is identical whether or not the select ever fires.
        self.selected_channel_id = int(self.select.values[0])
        await interaction.response.defer()

    async def _on_continue(self, interaction: nextcord.Interaction) -> None:
        if self.selected_channel_id is None:
            return await interaction.response.send_message(
                content='Please choose a channel first.', ephemeral=True)
        # send_modal must be the direct response to this component interaction; the
        # original ephemeral picker is then edited into a plain confirmation so the
        # now-irrelevant selector doesn't linger behind the modal.
        await interaction.response.send_modal(
            AnniversaryModal(self.selected_channel_id, entry=self.entry))
        await interaction.edit_original_message(
            content=f'Posting to <#{self.selected_channel_id}>.', view=None)
