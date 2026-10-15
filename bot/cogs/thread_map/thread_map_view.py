"""The public, persistent view on a thread map message.

Registered once with static custom ids; every callback resolves its map from
``interaction.message.id`` and the DB. Dispatch may hand a callback any
instance of this view, so instance state beyond the buttons is never trusted.
"""
import time
from typing import Optional

import nextcord
import sentry_sdk

from bot.cogs.thread_map.thread_map_render import build_embed
from bot.cogs.thread_map.thread_map_service import rebuild
from bot.utils import messages
from db import DB
from db.helpers import thread_map_helper

REFRESH_COOLDOWN = 15.0
_last_refresh: dict[int, float] = {}  # map_id -> time.monotonic() of last successful refresh

UNTRACKED_TEXT = 'This map is no longer tracked. Run /thread-map to post a new one.'


def forget_map(map_id: int) -> None:
    _last_refresh.pop(map_id, None)


class ThreadMapView(nextcord.ui.View):
    def __init__(self, page_index: int = 0, page_count: Optional[int] = None):
        super().__init__(timeout=None)
        # page_count None is the registration form: every button present and enabled.
        if page_count is None:
            return
        if page_count <= 1:
            self.remove_item(self.prev_page)
            self.remove_item(self.next_page)
            return
        self.prev_page.disabled = page_index <= 0
        self.next_page.disabled = page_index >= page_count - 1

    @nextcord.ui.button(emoji='◀️', custom_id='thread_map:prev', row=0)
    async def prev_page(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._turn_page(interaction, -1)

    @nextcord.ui.button(emoji='▶️', custom_id='thread_map:next', row=0)
    async def next_page(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._turn_page(interaction, 1)

    @nextcord.ui.button(label='Refresh', style=nextcord.ButtonStyle.blurple, custom_id='thread_map:refresh', row=0)
    async def refresh_map(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        with DB.Session() as session:
            config = thread_map_helper.get_config_by_message(session, interaction.message.id)
        if config is None:
            await interaction.send(embed=messages.error(UNTRACKED_TEXT), ephemeral=True)
            return
        if time.monotonic() - _last_refresh.get(config.id, float('-inf')) < REFRESH_COOLDOWN:
            await interaction.send(embed=messages.info('Refreshed moments ago.'), ephemeral=True)
            return

        await interaction.response.defer()
        rebuilt = await rebuild(interaction.guild, config.id)
        if rebuilt is None:
            await interaction.send(embed=messages.error(UNTRACKED_TEXT), ephemeral=True)
            return
        # Only after success, so a failed rebuild doesn't lock the button.
        _last_refresh[config.id] = time.monotonic()
        try:
            await interaction.message.edit(embed=rebuilt.embed, view=ThreadMapView(0, rebuilt.page_count))
        except nextcord.HTTPException as e:  # e.g. rate-limited edits on a busy map
            sentry_sdk.capture_exception(e)

    async def _turn_page(self, interaction: nextcord.Interaction, step: int):
        with DB.Session() as session:
            config = thread_map_helper.get_config_by_message(session, interaction.message.id)
            if config is not None:
                index = min(max(config.current_page + step, 0), config.page_count - 1)
                thread_map_helper.set_current_page(session, config.id, index)
                page = thread_map_helper.get_page(session, config.id, index)
                session.commit()
        if config is None:
            await interaction.send(embed=messages.error(UNTRACKED_TEXT), ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=build_embed(page or '', index, config.page_count, config.last_refreshed_at),
            view=ThreadMapView(index, config.page_count),
        )

    async def on_error(self, error: Exception, item: nextcord.ui.Item, interaction: nextcord.Interaction) -> None:
        # Callbacks only use DB.Session(), never DB.s, so there is no session to recover.
        sentry_sdk.capture_exception(error)
        try:
            await interaction.send(embed=messages.error('Something went wrong with this thread map. Please try again.'),
                                   ephemeral=True)
        except nextcord.HTTPException:
            pass
