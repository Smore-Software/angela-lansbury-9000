"""The member-facing ``/anniversary`` command surface plus the manager-only
``/anniversary-channels`` registry.

Modeled on ``bot/cogs/starboard/starboard_commands.py``: two globally-shipped
slash groups, autocomplete via ``@subcmd.on_autocomplete``, ephemeral replies
through ``messages.error/success/info``, and the genuinely testable logic
(authorization + autocomplete choices) factored into static/module-level helpers
so it can be exercised without a live interaction (see
``tests/test_anniversary_commands.py``). Every mutation goes through the two
helpers; no persistence lives here.

The add/edit flows are split across views because of two Discord constraints: a
modal must be the *direct* response to an interaction and cannot hold a select, so
the channel is chosen in ``ChannelChoiceView`` first, whose component interaction
then opens ``AnniversaryModal``. The daily posting loop (``post_anniversaries``)
runs once at 12:00 UTC, posting one neutral embed per today's entry to its own
channel and skipping entries whose channel was deregistered or whose submitter has
left the guild.
"""
import asyncio
from datetime import datetime, time, timezone

import nextcord
import sentry_sdk
from nextcord import slash_command, Permissions, Interaction, SlashOption, TextChannel
from nextcord.ext import commands, tasks

from bot.cogs.anniversary import anniversary_utils
from bot.cogs.anniversary.views.channel_choice_view import ChannelChoiceView
from bot.utils import messages, bot_utils
from bot.utils.views import EmbedPaginatorView
from db.helpers import anniversary_helper, anniversary_channel_helper

# Discord caps autocomplete labels at 100 chars and 25 choices per response.
_AUTOCOMPLETE_LABEL_MAX = 100
_AUTOCOMPLETE_CHOICE_MAX = 25


def entry_label(entry) -> str:
    """Plain-text autocomplete label for an entry: ``Title · MM/DD`` with the year
    appended when set. Truncated to Discord's autocomplete label limit."""
    parts = [anniversary_utils.title_or_default(entry.title),
             f'{entry.month:02d}/{entry.day:02d}']
    if entry.year:
        parts.append(str(entry.year))
    label = ' · '.join(parts)
    if len(label) > _AUTOCOMPLETE_LABEL_MAX:
        label = label[:_AUTOCOMPLETE_LABEL_MAX - 1] + '…'
    return label


def channel_label(channel) -> str:
    """Plain-text autocomplete label for a registered channel — its admin-set
    registry label, truncated to Discord's limit (channel mentions don't render in
    autocomplete labels, so the label carries the meaning)."""
    label = channel.label
    if len(label) > _AUTOCOMPLETE_LABEL_MAX:
        label = label[:_AUTOCOMPLETE_LABEL_MAX - 1] + '…'
    return label


async def _fetch_member_or_none(guild, user_id):
    """Resolve ``user_id`` to a member via a REST ``fetch_member`` (the ``members``
    intent is OFF per ``bot/app.py``, so the cache is empty and ``get_member`` can't
    be trusted), returning ``None`` when the submitter has left the guild."""
    try:
        return await guild.fetch_member(user_id)
    except nextcord.NotFound:
        return None


class AnniversaryCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.post_anniversaries.start()

    # --- daily posting loop -------------------------------------------------

    # Decision 4: one post a day at noon UTC. This MUST stay a `time=` schedule —
    # never a `seconds=` interval (the birthday cog shipped a 15s test loop by
    # accident; do not repeat it).
    @tasks.loop(time=time(hour=12, minute=0, second=0, tzinfo=timezone.utc))
    async def post_anniversaries(self):
        if not self.bot.is_ready():
            return
        today = datetime.now(timezone.utc)
        for guild_id, entries in anniversary_helper.get_todays(today).items():
            try:
                guild = self.bot.get_guild(guild_id)
                if guild is None:
                    continue
                registered = {c.channel_id
                              for c in anniversary_channel_helper.get_channels(guild_id)}
                # Resolve membership once per submitter via REST, but only for
                # entries whose channel is still registered (a deregistered channel
                # skips before we'd ever need the membership check).
                membership = {}
                for entry in entries:
                    if entry.channel_id in registered and entry.user_id not in membership:
                        member = await _fetch_member_or_none(guild, entry.user_id)
                        membership[entry.user_id] = member is not None
                postable, _skipped = anniversary_utils.partition_postable(
                    entries, registered, lambda user_id: membership.get(user_id, False))
                for entry in postable:
                    channel = await bot_utils.get_or_fetch_channel(guild, entry.channel_id)
                    if channel is None:
                        continue
                    embed = anniversary_utils.post_embed(entry, today.year)
                    await channel.send(
                        content=f'<@{entry.user_id}>', embed=embed,
                        allowed_mentions=nextcord.AllowedMentions(users=True))
            except nextcord.Forbidden as e:
                sentry_sdk.capture_exception(e)
            except Exception as e:
                sentry_sdk.capture_exception(e)

    @post_anniversaries.error
    async def post_anniversaries_error(self, e):
        sentry_sdk.capture_exception(e)
        await asyncio.sleep(60)
        self.post_anniversaries.restart()

    # --- /anniversary (open to everyone) ------------------------------------

    @slash_command(name='anniversary',
                   description='Record and manage anniversaries and remembrances.',
                   force_global=True)
    async def anniversary(self, interaction: Interaction):
        pass

    @anniversary.subcommand(name='add', description='Record a new anniversary.')
    async def add(self, interaction: Interaction):
        channels = anniversary_channel_helper.get_channels(interaction.guild_id)
        if not channels:
            return await interaction.send(
                embed=messages.error(
                    'No anniversary channels are registered yet. Ask a server manager '
                    'to add one with `/anniversary-channels add`.'),
                ephemeral=True)
        # Decision 5: the picker always shows; the sole channel is preselected.
        preselected = channels[0].channel_id if len(channels) == 1 else None
        await interaction.send(
            view=ChannelChoiceView(channels, preselected_channel_id=preselected),
            ephemeral=True)

    @anniversary.subcommand(name='list', description='List your own anniversaries.')
    async def list(self, interaction: Interaction):
        entries = anniversary_helper.list_for_user(interaction.guild_id, interaction.user.id)
        if not entries:
            return await interaction.send(
                embed=messages.info('You have no anniversaries recorded yet.'), ephemeral=True)
        embeds = anniversary_utils.build_list_embeds(
            entries, guild=interaction.guild, title='Your anniversaries')
        await EmbedPaginatorView(embeds, noun='Page').send(interaction)

    @anniversary.subcommand(name='upcoming',
                            description='Anniversaries coming up in the next month.')
    async def upcoming(self, interaction: Interaction):
        entries = anniversary_helper.get_upcoming(interaction.guild_id)
        if not entries:
            return await interaction.send(
                embed=messages.info('No anniversaries are coming up in the next month.'),
                ephemeral=True)
        embeds = anniversary_utils.build_list_embeds(
            entries, guild=interaction.guild, title='Upcoming anniversaries')
        await EmbedPaginatorView(embeds, noun='Page').send(interaction)

    @anniversary.subcommand(name='edit', description='Edit one of your anniversaries.')
    async def edit(self, interaction: Interaction,
                   entry: str = SlashOption(
                       name='entry', description='The anniversary to edit.')):
        resolved = self._resolve_entry(entry)
        if not self._can_manage(interaction, resolved):
            return await interaction.send(
                embed=messages.error('No such anniversary, or it isn\'t yours to edit.'),
                ephemeral=True)
        channels = anniversary_channel_helper.get_channels(interaction.guild_id)
        if not channels:
            return await interaction.send(
                embed=messages.error(
                    'No anniversary channels are registered yet. Ask a server manager '
                    'to add one with `/anniversary-channels add`.'),
                ephemeral=True)
        # Reuse the add flow: same picker preselected to the current channel (so
        # keeping it is one tap, re-routing is just a different pick), same modal
        # prefilled from the entry.
        await interaction.send(
            view=ChannelChoiceView(
                channels, preselected_channel_id=resolved.channel_id, entry=resolved),
            ephemeral=True)

    @anniversary.subcommand(name='remove', description='Remove one of your anniversaries.')
    async def remove(self, interaction: Interaction,
                     entry: str = SlashOption(
                         name='entry', description='The anniversary to remove.')):
        resolved = self._resolve_entry(entry)
        if not self._can_manage(interaction, resolved):
            return await interaction.send(
                embed=messages.error('No such anniversary, or it isn\'t yours to remove.'),
                ephemeral=True)
        anniversary_helper.delete(resolved.id)
        await interaction.send(
            embed=messages.success(f'Removed: {entry_label(resolved)}.'), ephemeral=True)

    @edit.on_autocomplete('entry')
    async def _edit_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(self._entry_choices(interaction, focused))

    @remove.on_autocomplete('entry')
    async def _remove_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(self._entry_choices(interaction, focused))

    # --- /anniversary-channels (manage_guild) -------------------------------

    @slash_command(name='anniversary-channels',
                   description='Manage which channels can receive anniversary posts.',
                   force_global=True,
                   default_member_permissions=Permissions(manage_guild=True))
    async def anniversary_channels(self, interaction: Interaction):
        pass

    @anniversary_channels.subcommand(
        name='add', description='Register a channel for anniversary posts.')
    async def add_channel(self, interaction: Interaction,
                          channel: TextChannel = SlashOption(
                              name='channel', description='The channel to register.'),
                          label: str = SlashOption(
                              name='label',
                              description='A short name shown in the picker, e.g. "Remembrances".')):
        registered = anniversary_channel_helper.add_channel(
            interaction.guild_id, channel.id, label)
        if registered is None:
            return await interaction.send(
                embed=messages.error(f'{channel.mention} is already registered.'),
                ephemeral=True)
        confirmation = f'Registered {channel.mention} for anniversaries as "{label}".'
        await interaction.send(
            embed=messages.success(confirmation + self._perms_warning(interaction, channel)),
            ephemeral=True)

    @anniversary_channels.subcommand(
        name='remove', description='Deregister an anniversary channel.')
    async def remove_channel(self, interaction: Interaction,
                             channel: str = SlashOption(
                                 name='channel', description='The channel to deregister.')):
        resolved = self._resolve_channel(interaction, channel)
        if resolved is None:
            return await interaction.send(
                embed=messages.error('That channel is not registered on this server.'),
                ephemeral=True)
        anniversary_channel_helper.remove_channel(resolved.id)
        await interaction.send(
            embed=messages.success(
                f'Deregistered <#{resolved.channel_id}>. Existing entries are left alone '
                'and simply won\'t post until re-routed.'),
            ephemeral=True)

    @anniversary_channels.subcommand(
        name='list', description='List this server\'s anniversary channels.')
    async def list_channels(self, interaction: Interaction):
        channels = anniversary_channel_helper.get_channels(interaction.guild_id)
        if not channels:
            return await interaction.send(
                embed=messages.info('No anniversary channels are registered yet.'),
                ephemeral=True)
        lines = [f'<#{c.channel_id}> | {c.label}' for c in channels]
        embed = nextcord.Embed(color=messages.INFO_COLOR, title='Anniversary channels',
                               description='\n'.join(lines))
        await interaction.send(embed=embed, ephemeral=True)

    @remove_channel.on_autocomplete('channel')
    async def _remove_channel_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(self._channel_choices(interaction, focused))

    # --- internal helpers ---------------------------------------------------

    @staticmethod
    def _can_manage(interaction, entry) -> bool:
        """True when the caller may edit/remove ``entry``: it must exist, belong to
        this guild, and be either the caller's own or reachable via ``manage_guild``.
        Owners reach only their own entries; managers reach any in the guild."""
        return entry is not None and entry.guild_id == interaction.guild_id and (
            entry.user_id == interaction.user.id
            or interaction.user.guild_permissions.manage_guild)

    @staticmethod
    def _resolve_entry(entry):
        """Resolve the ``entry`` option (an id as a string) to an ``Anniversary`` or
        ``None`` when malformed/missing. Guild + ownership are enforced by
        ``_can_manage``, not here."""
        try:
            entry_id = int(entry)
        except (TypeError, ValueError):
            return None
        return anniversary_helper.get(entry_id)

    @staticmethod
    def _resolve_channel(interaction, channel):
        """Resolve the ``channel`` option (a channel id as a string) to one of THIS
        guild's registered channels, or ``None`` when malformed or absent."""
        try:
            channel_id = int(channel)
        except (TypeError, ValueError):
            return None
        return anniversary_channel_helper.find_by_channel_id(interaction.guild_id, channel_id)

    @staticmethod
    def _perms_warning(interaction, channel) -> str:
        """Trailing warning string when the bot can't post to ``channel`` — empty
        when permissions look fine (mirrors starboard's preflight)."""
        perms = channel.permissions_for(interaction.guild.me)
        if perms.send_messages and perms.embed_links:
            return ''
        return ('\n\n⚠️ I may be missing **Send Messages** / **Embed Links** in '
                f'{channel.mention}; posts there will fail until that\'s fixed.')

    @staticmethod
    def _entry_choices(interaction, focused: str = '') -> dict:
        """``{label: str(id)}`` choices for the entry autocomplete. Scoped to the
        caller's own entries, or ALL guild entries when the caller has
        ``manage_guild``; case-insensitive substring filter on the label; capped at
        Discord's 25-choice limit."""
        if interaction.user.guild_permissions.manage_guild:
            entries = anniversary_helper.list_for_guild(interaction.guild_id)
        else:
            entries = anniversary_helper.list_for_user(interaction.guild_id, interaction.user.id)
        query = (focused or '').strip().lower()
        choices = {}
        for entry in entries:
            label = entry_label(entry)
            if query and query not in label.lower():
                continue
            choices[label] = str(entry.id)
            if len(choices) >= _AUTOCOMPLETE_CHOICE_MAX:
                break
        return choices

    @staticmethod
    def _channel_choices(interaction, focused: str = '') -> dict:
        """``{label: str(channel_id)}`` choices for the registry autocomplete,
        case-insensitive substring filtered on the label and capped at 25."""
        channels = anniversary_channel_helper.get_channels(interaction.guild_id)
        query = (focused or '').strip().lower()
        choices = {}
        for channel in channels:
            label = channel_label(channel)
            if query and query not in label.lower():
                continue
            choices[label] = str(channel.channel_id)
            if len(choices) >= _AUTOCOMPLETE_CHOICE_MAX:
                break
        return choices
