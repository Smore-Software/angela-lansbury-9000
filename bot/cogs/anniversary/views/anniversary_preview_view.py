"""The confirm-to-save preview shown after the anniversary modal is submitted.

The modal no longer persists on submit; instead it builds a not-yet-saved pending
entry, renders ``post_embed`` for it, and attaches this view. **Confirm** is what
actually writes (``anniversary_helper.add`` for a new entry, ``update`` for an
edit), then swaps the preview for a success state with both buttons disabled.
**Edit** reopens ``AnniversaryModal`` prefilled from the pending values so the
submitter can correct a field before saving — nothing is stored until Confirm.

Two Discord constraints shape this:
- A modal must be the *direct* response to a fresh component interaction, so Edit
  opens the modal straight off its own button press (it cannot be chained off the
  earlier modal-submit interaction). Re-submitting that modal returns here.
- The preview holds sensitive data (names of lost children), so it — like every
  management surface in this cog — stays ephemeral.

``entry`` carries the add-vs-edit mode through the round-trip: ``None`` means add
(Confirm → ``add``); a real ``Anniversary`` means edit (Confirm → ``update`` on
that row). The pending values and the channel ride along so a Confirm or an Edit
re-open uses exactly what the submitter last saw.
"""
import nextcord

from bot.cogs.anniversary import anniversary_utils
from bot.cogs.anniversary.views.anniversary_modal import AnniversaryModal
from bot.utils import messages
from db.helpers import anniversary_helper


class AnniversaryPreviewView(nextcord.ui.View):
    def __init__(self, *, pending, entry=None, guild_id: int, user_id: int,
                 timeout: float = 180):
        super().__init__(timeout=timeout)
        # The not-yet-persisted entry (a SimpleNamespace from
        # ``anniversary_utils.build_pending_entry``) the user is confirming.
        self.pending = pending
        # None => add mode; a real Anniversary row => edit that row. Drives which
        # helper Confirm calls and is re-handed to the modal on an Edit re-open.
        self.entry = entry
        # Needed by ``add`` (a fresh row), which has no existing entry to read them
        # from; ignored in edit mode where the row already owns them.
        self.guild_id = guild_id
        self.user_id = user_id

    @nextcord.ui.button(label='Confirm', style=nextcord.ButtonStyle.green)
    async def confirm(self, _: nextcord.ui.Button,
                      interaction: nextcord.Interaction) -> None:
        pending = self.pending
        if self.entry is None:
            saved = anniversary_helper.add(
                guild_id=self.guild_id, user_id=self.user_id,
                channel_id=pending.channel_id, title=pending.title,
                count_label=pending.count_label, message=pending.message,
                month=pending.month, day=pending.day, year=pending.year)
            collision = 'You already have an anniversary with that title on that date.'
            verb = 'Saved'
        else:
            saved = anniversary_helper.update(
                self.entry.id, title=pending.title, count_label=pending.count_label,
                message=pending.message, month=pending.month, day=pending.day,
                year=pending.year, channel_id=pending.channel_id)
            collision = ('That anniversary no longer exists, or another with that '
                         'title and date already does.')
            verb = 'Updated'

        if saved is None:
            # Leave the preview and its buttons intact so the user can adjust via
            # Edit and try again; just report what blocked the save.
            return await interaction.response.send_message(
                embed=messages.error(collision), ephemeral=True)

        # Persisted: disable both buttons and replace the preview with the saved
        # entry's embed so the message reads as a finished, immutable confirmation.
        for child in self.children:
            child.disabled = True
        embed = anniversary_utils.post_embed(saved, nextcord.utils.utcnow().year)
        await interaction.response.edit_message(
            content=f'{verb}! It will appear in <#{saved.channel_id}>.',
            embed=embed, view=self)
        self.stop()

    @nextcord.ui.button(label='Edit', style=nextcord.ButtonStyle.gray)
    async def edit(self, _: nextcord.ui.Button,
                   interaction: nextcord.Interaction) -> None:
        # The modal must be the direct response to this fresh button interaction;
        # it reopens prefilled from the pending values, carrying the edit target
        # (or None for add) so re-submission routes back to the same Confirm path.
        await interaction.response.send_modal(
            AnniversaryModal(self.pending.channel_id, entry=self.entry,
                             prefill=self.pending))
        # Retire this preview's now-stale buttons; the re-submitted modal sends a
        # fresh preview, so leaving these live would orphan a second control set.
        await interaction.edit_original_message(
            content='Make your changes and submit again.', embed=None, view=None)
        self.stop()
