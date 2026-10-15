"""The ephemeral moderator menu opened by a thread map's Edit button.

The main menu swaps itself in place for a dropdown sub-view and back. Every
selection saves immediately, responds with the fresh summary inside Discord's
3-second window, then rebuilds and pushes the public map. Views only carry the
map id; each callback re-reads the config from the DB.
"""
from typing import Callable, Iterable, Literal, Optional

import nextcord

from bot.cogs.thread_map.thread_map_render import is_admin_role
from bot.cogs.thread_map.thread_map_service import Rebuilt, rebuild
from bot.cogs.thread_map.thread_map_view import ThreadMapView, forget_map, report_view_error
from bot.utils import messages
from db import DB
from db.helpers import thread_map_helper
from db.helpers.thread_map_helper import MapConfig

PushResult = Literal['ok', 'gone', 'forbidden']

SCOPED_CHANNEL_TYPES = [nextcord.ChannelType.text, nextcord.ChannelType.news, nextcord.ChannelType.forum]
MAP_GONE_TEXT = 'This map no longer exists.'


async def push_to_message(bot, cfg: MapConfig, rebuilt: Rebuilt) -> PushResult:
    # type= is required: PartialMessage rejects a PartialMessageable whose type is None.
    # ChannelType.text also covers announcement hosts, since the edit is a plain HTTP call.
    channel = bot.get_partial_messageable(cfg.channel_id, type=nextcord.ChannelType.text)
    try:
        await channel.get_partial_message(cfg.message_id).edit(embed=rebuilt.embed,
                                                               view=ThreadMapView(0, rebuilt.page_count))
    except nextcord.NotFound:
        return 'gone'
    except nextcord.Forbidden:
        return 'forbidden'
    return 'ok'


def _role_label(guild, role_id: int) -> str:
    if role_id == guild.id:
        return '@everyone'
    role = guild.get_role(role_id)
    return f'@{role.name}' if role else 'deleted role'


def _channel_label(guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id)
    return f'#{channel.name}' if channel else 'deleted channel'


def summary_embed(guild, cfg: MapConfig, counts: Optional[tuple[int, int]] = None,
                  notes: Iterable[str] = ()) -> nextcord.Embed:
    """The menu's view of the current config; selects can't show selections, so this is the only display."""
    roles = ', '.join(['@everyone'] + sorted(_role_label(guild, r) for r in cfg.role_ids))
    excluded = ', '.join(sorted(_channel_label(guild, c) for c in cfg.excluded_ids)) or 'none'
    lines = [f'**Showing channels visible to**: {roles}',
             '-# To add channels to the map, add roles whose permissions allow them to see those channels',
             '',
             f'Excluded channels: {excluded}']
    if counts is not None:
        lines += ['', f'Map is showing {counts[0]} channels with {counts[1]} threads']
    if notes:
        lines += [''] + list(notes)
    return nextcord.Embed(title='Edit thread map', description='\n'.join(lines), color=nextcord.Color.dark_blue())


def _load(map_id: int) -> Optional[MapConfig]:
    with DB.Session() as session:
        return thread_map_helper.get_config(session, map_id)


class _MenuBase(nextcord.ui.View):
    def __init__(self, map_id: int):
        super().__init__(timeout=180)
        self.map_id = map_id
        # The interaction whose message shows this view, so on_timeout can edit it.
        self._interaction: Optional[nextcord.Interaction] = None

    async def _show(self, interaction: nextcord.Interaction, view: '_MenuBase', embed: nextcord.Embed):
        """Swap this message to ``view``. Stops the outgoing view first so its
        timeout can't later disable the controls of the one replacing it."""
        self.stop()
        await interaction.response.edit_message(embed=embed, view=view)
        view._interaction = interaction

    async def _show_gone(self, interaction: nextcord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=messages.error(MAP_GONE_TEXT), view=None)

    async def _show_menu(self, interaction: nextcord.Interaction):
        cfg = _load(self.map_id)
        if cfg is None:
            await self._show_gone(interaction)
            return
        await self._show(interaction, EditMenuView(cfg), summary_embed(interaction.guild, cfg))

    async def _apply(self, interaction: nextcord.Interaction,
                     mutate: Callable[[object, MapConfig], list[str]]):
        """Write a change, answer with the fresh summary, then rebuild and push the public map."""
        guild = interaction.guild
        notes: list[str] = []
        with DB.Session() as session:
            cfg = thread_map_helper.get_config(session, self.map_id)
            if cfg is not None:
                notes = mutate(session, cfg)
                session.commit()
                cfg = thread_map_helper.get_config(session, self.map_id)
        if cfg is None:
            await self._show_gone(interaction)
            return

        await self._show(interaction, EditMenuView(cfg), summary_embed(guild, cfg, notes=notes))

        rebuilt = await rebuild(guild, self.map_id)
        if rebuilt is None:
            await interaction.followup.send(embed=messages.error(MAP_GONE_TEXT), ephemeral=True)
            return
        result = await push_to_message(interaction.client, cfg, rebuilt)
        if result == 'gone':
            with DB.Session() as session:
                thread_map_helper.delete_map(session, self.map_id)
                session.commit()
            forget_map(self.map_id)
            await interaction.followup.send(
                embed=messages.error('That map message was deleted, so I stopped tracking it.'), ephemeral=True)
        elif result == 'forbidden':
            await interaction.followup.send(
                embed=messages.error("I can't edit the map message anymore. Check my permissions in that channel."),
                ephemeral=True)
        else:
            try:
                await interaction.edit_original_message(
                    embed=summary_embed(guild, cfg, (rebuilt.channel_count, rebuilt.thread_count), notes))
            except nextcord.HTTPException:
                pass  # the ephemeral token may have lapsed during a slow rebuild

    async def on_timeout(self):
        self.stop()
        for child in self.children:
            child.disabled = True
        if self._interaction is not None:
            try:
                await self._interaction.edit_original_message(view=self)
            except nextcord.HTTPException:
                pass

    async def on_error(self, error: Exception, item: nextcord.ui.Item, interaction: nextcord.Interaction) -> None:
        await report_view_error(error, interaction)


class EditMenuView(_MenuBase):
    def __init__(self, cfg: MapConfig):
        super().__init__(cfg.id)
        self.remove_role.disabled = not cfg.role_ids

    @nextcord.ui.button(label='Exclude channel', style=nextcord.ButtonStyle.grey, row=0)
    async def exclude_channel(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._show(interaction, ExcludeChannelsView(self.map_id), nextcord.Embed(
            title='Exclude channels',
            description='Pick channels to hide from the map. Use Remove channel exclusion to bring one back.'))

    @nextcord.ui.button(label='Remove channel exclusion', style=nextcord.ButtonStyle.grey, row=0)
    async def include_channel(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._show(interaction, IncludeChannelsView(self.map_id), nextcord.Embed(
            title='Remove channel exclusions',
            description='Pick excluded channels to show on the map again. The menu lists what is excluded now.'))

    @nextcord.ui.button(label='Add role visibility', style=nextcord.ButtonStyle.grey, row=1)
    async def add_role(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._show(interaction, AddRolesView(self.map_id), nextcord.Embed(
            title='Add role visibility',
            description='Pick roles whose channels should also appear on the map.'))

    @nextcord.ui.button(label='Remove role visibility', style=nextcord.ButtonStyle.grey, row=1)
    async def remove_role(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        cfg = _load(self.map_id)
        if cfg is None:
            await self._show_gone(interaction)
            return
        if not cfg.role_ids:  # removed elsewhere since this menu was drawn
            await self._show(interaction, EditMenuView(cfg), summary_embed(interaction.guild, cfg))
            return
        await self._show(interaction, RemoveRolesView(interaction.guild, cfg), nextcord.Embed(
            title='Remove role visibility',
            description='Pick roles to stop showing channels for.'))


class _SubView(_MenuBase):
    @nextcord.ui.button(label='Back', style=nextcord.ButtonStyle.grey, row=1)
    async def back(self, _: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._show_menu(interaction)


def _selected_ids(interaction: nextcord.Interaction) -> list[int]:
    # Raw ids, not .values.channels: thread and uncached picks don't resolve.
    return [int(v) for v in interaction.data.get('values', [])]


class ExcludeChannelsView(_SubView):
    @nextcord.ui.channel_select(placeholder='Choose channels to exclude', channel_types=SCOPED_CHANNEL_TYPES,
                                min_values=1, max_values=25, row=0)
    async def channels(self, _: nextcord.ui.ChannelSelect, interaction: nextcord.Interaction):
        ids = _selected_ids(interaction)

        def mutate(session, cfg):
            thread_map_helper.add_exclusions(session, cfg.id, ids)
            return []

        await self._apply(interaction, mutate)


class IncludeChannelsView(_SubView):
    @nextcord.ui.channel_select(placeholder='Choose channels to include', channel_types=SCOPED_CHANNEL_TYPES,
                                min_values=1, max_values=25, row=0)
    async def channels(self, _: nextcord.ui.ChannelSelect, interaction: nextcord.Interaction):
        ids = _selected_ids(interaction)

        def mutate(session, cfg):
            thread_map_helper.remove_exclusions(session, cfg.id, ids)  # not-excluded ids are a no-op
            return []

        await self._apply(interaction, mutate)


class AddRolesView(_SubView):
    @nextcord.ui.role_select(placeholder='Choose roles to add', min_values=1, max_values=25, row=0)
    async def roles(self, _: nextcord.ui.RoleSelect, interaction: nextcord.Interaction):
        guild = interaction.guild
        ids = [i for i in _selected_ids(interaction) if i != guild.id]  # @everyone is always included

        def mutate(session, cfg):
            added, rejected = thread_map_helper.add_roles(session, cfg.id, ids)
            notes = []
            if rejected:
                notes.append(f'⚠️ {len(rejected)} role(s) not added: a map can show at most '
                             f'{thread_map_helper.ROLE_CAP} roles.')
            for role in filter(None, map(guild.get_role, added)):
                if is_admin_role(role):
                    notes.append(f'⚠️ @{role.name} has Administrator, so the map lists every channel I can see, '
                                 'including private ones.')
            return notes

        await self._apply(interaction, mutate)


class RemoveRolesView(_SubView):
    def __init__(self, guild, cfg: MapConfig):
        super().__init__(cfg.id)
        # Never preselect: a StringSelect whose only option is preselected never fires.
        options = [nextcord.SelectOption(label=_role_label(guild, r)[:100], value=str(r))
                   for r in sorted(cfg.role_ids)]
        select = nextcord.ui.StringSelect(placeholder='Choose roles to remove', options=options,
                                          min_values=1, max_values=len(options), row=0)
        select.callback = self._on_select
        self.add_item(select)
        self.roles = select

    async def _on_select(self, interaction: nextcord.Interaction):
        ids = _selected_ids(interaction)

        def mutate(session, cfg):
            thread_map_helper.remove_roles(session, cfg.id, ids)
            return []

        await self._apply(interaction, mutate)
