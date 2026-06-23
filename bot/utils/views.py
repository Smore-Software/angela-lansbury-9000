"""Reusable nextcord view building blocks shared across cogs.

``EmbedPaginatorView`` is a generic take on the repo's button-driven pagination
pattern (◀️/▶️ that edit the message in place), the shape the per-cog views model
ad hoc (``image_message_delete``'s ``ConfigurePromptsView``). It lives here so new
surfaces page identically without re-rolling their own view; the existing bespoke
views can be migrated onto it in a follow-up.
"""
import nextcord


class EmbedPaginatorView(nextcord.ui.View):
    """Paginate a list of pre-rendered embeds, one per page, with ◀️/▶️ buttons.

    Navigation edits the message in place and wraps with modulo, so ▶️ on the
    last page lands on the first and ◀️ on the first lands on the last. A single
    page needs no navigation, so the buttons are dropped entirely in that case.
    Each page's footer is stamped ``{noun} N/M`` so the viewer always knows where
    they are. On timeout the buttons are disabled in place (see ``send``).

    The caller owns rendering: pass a non-empty list of embeds and, optionally,
    the noun to label pages with (``'Page'`` by default, e.g. ``'Board'``).
    """

    def __init__(self, embeds: list[nextcord.Embed], *, noun: str = 'Page',
                 timeout: float | None = 180):
        super().__init__(timeout=timeout)
        if not embeds:
            raise ValueError('EmbedPaginatorView requires at least one embed.')
        self.embeds = embeds
        self.noun = noun
        self.current_index = 0
        # Where the ephemeral response lives, so on_timeout can edit it; set by send().
        self._interaction: nextcord.Interaction | None = None
        # A lone page has nowhere to page to — strip the controls.
        if len(self.embeds) == 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)

    @property
    def current_embed(self) -> nextcord.Embed:
        """The embed for the current page, footer stamped with its position.

        Mutates the embed's footer on each access, so the caller must pass embeds
        that are not aliased elsewhere (the list command builds a fresh one per
        board, which satisfies this).
        """
        embed = self.embeds[self.current_index]
        embed.set_footer(text=f'{self.noun} {self.current_index + 1}/{len(self.embeds)}')
        return embed

    async def send(self, interaction: nextcord.Interaction, *, ephemeral: bool = True):
        """Send the first page and remember the interaction for timeout cleanup."""
        self._interaction = interaction
        await interaction.send(embed=self.current_embed, view=self, ephemeral=ephemeral)

    @nextcord.ui.button(emoji='◀️', style=nextcord.ButtonStyle.secondary)
    async def previous_page(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.current_index = (self.current_index - 1) % len(self.embeds)
        await interaction.response.edit_message(embed=self.current_embed, view=self)

    @nextcord.ui.button(emoji='▶️', style=nextcord.ButtonStyle.secondary)
    async def next_page(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.current_index = (self.current_index + 1) % len(self.embeds)
        await interaction.response.edit_message(embed=self.current_embed, view=self)

    async def on_timeout(self):
        """Disable the controls in place so a stale message can't be paged.

        The ephemeral token can lapse or the message be dismissed before the
        timeout fires, so the edit is best-effort — a failed cleanup edit is not
        worth surfacing as an unhandled error in the timeout task.
        """
        self.stop()
        for child in self.children:
            child.disabled = True
        if self._interaction is not None:
            try:
                await self._interaction.edit_original_message(view=self)
            except nextcord.HTTPException:
                pass
