"""The admin-facing ``/starboard add|list|edit|remove`` command surface.

The root group is gated behind ``manage_guild`` and shipped globally
(``force_global``), mirroring ``bot/cogs/server_admin/server_admin_commands.py``.
The genuinely testable logic — emoji parsing/validation and the label/embed
builders — is factored into module-level functions so it can be exercised without
a live interaction (see ``tests/test_starboard_commands.py``). Every mutation goes
through ``starboard_helper``, which invalidates the per-guild config cache the
reaction hot path reads.
"""
import nextcord
from nextcord import slash_command, Permissions, Interaction, SlashOption, TextChannel
from nextcord.ext import commands

from bot.cogs.starboard.starboard_utils import parse_emoji_input
from bot.utils import messages
from bot.utils.views import EmbedPaginatorView
from db.helpers import starboard_helper

# Discord caps autocomplete labels at 100 chars and 25 choices per response.
_AUTOCOMPLETE_LABEL_MAX = 100
_AUTOCOMPLETE_CHOICE_MAX = 25
# Boards per page in `/starboard list` (one Markdown list item per board). The
# paginator adds ◀️/▶️ buttons only when there is more than one page.
_LIST_BOARDS_PER_PAGE = 10


def emoji_display(config) -> str:
    """Render a config's stored emoji back to a displayable form: ``<:name:id>``
    for a custom emoji, the raw unicode char otherwise. Renders as the actual emoji
    only in markdown contexts (embed descriptions, message content) — never in the
    plain-text of an autocomplete label, where ``emoji_label`` is used instead."""
    if config.emoji_id is not None:
        return f'<:{config.emoji}:{config.emoji_id}>'
    return config.emoji


def emoji_label(config) -> str:
    """Plain-text rendering of a config's emoji for non-markdown contexts
    (autocomplete labels). A custom-emoji mention renders as raw ``<:name:id>``
    there, so we fall back to the readable ``:name:`` form; unicode emoji render
    fine as-is."""
    if config.emoji_id is not None:
        return f':{config.emoji}:'
    return config.emoji


def board_summary(config, channel_ref: str, *, markdown: bool = True) -> str:
    """Canonical one-line board description, standardized across the feature set
    (list, autocomplete, confirmations): ``channel | emoji | ≥ N`` with the
    threshold bolded in markdown contexts. The board id is omitted — it is
    meaningless to users. ``channel_ref`` is the already-rendered channel: a
    ``<#id>`` mention in embeds/messages, a plain ``#name`` in autocomplete labels.
    The ``markdown`` flag also picks the emoji rendering: a custom-emoji mention
    only shows as the emoji in markdown contexts, so plain-text labels use the
    ``:name:`` form instead."""
    threshold = f'**≥ {config.threshold}**' if markdown else f'≥ {config.threshold}'
    emoji = emoji_display(config) if markdown else emoji_label(config)
    return f'{channel_ref} | {emoji} | {threshold}'


def board_label(config, channel_name: str | None = None) -> str:
    """Plain-text autocomplete label in the standardized board format. Uses the
    resolved ``#channel-name`` when known, falling back to a channel mention.
    Truncated to Discord's autocomplete label limit."""
    channel_ref = f'#{channel_name}' if channel_name else f'<#{config.target_channel_id}>'
    label = board_summary(config, channel_ref, markdown=False)
    if len(label) > _AUTOCOMPLETE_LABEL_MAX:
        label = label[:_AUTOCOMPLETE_LABEL_MAX - 1] + '…'
    return label


def build_list_embeds(configs) -> list:
    """Build one embed per page of a guild's boards as a numbered Markdown list,
    chunked at ``_LIST_BOARDS_PER_PAGE`` boards each (``list`` paginates only when
    needed). Each item uses the standardized board format; disabled boards carry a
    trailing ``| disabled`` so they stay distinguishable."""
    embeds = []
    for start in range(0, len(configs), _LIST_BOARDS_PER_PAGE):
        chunk = configs[start:start + _LIST_BOARDS_PER_PAGE]
        lines = []
        for offset, config in enumerate(chunk):
            summary = board_summary(config, f'<#{config.target_channel_id}>')
            if not config.enabled:
                summary += ' | disabled'
            lines.append(f'{start + offset + 1}. {summary}')
        embed = nextcord.Embed(color=messages.INFO_COLOR, title='Starboards',
                               description='\n'.join(lines))
        embeds.append(embed)
    return embeds


def custom_emoji_belongs_to_guild(guild_emojis, emoji_id: int) -> bool:
    """True when ``emoji_id`` names one of the guild's own emoji. Rejecting foreign
    custom emoji keeps a board from referencing an emoji the bot can never see used
    here."""
    return nextcord.utils.get(guild_emojis, id=emoji_id) is not None


class StarboardCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @slash_command(name='starboard',
                   description='Configure this server\'s starboards.',
                   force_global=True,
                   default_member_permissions=Permissions(manage_guild=True))
    async def starboard(self, interaction: Interaction):
        pass

    @starboard.subcommand(name='add', description='Add a starboard to this server.')
    async def add(self, interaction: Interaction,
                  channel: TextChannel = SlashOption(
                      name='channel', description='Where matching messages are reposted.'),
                  emoji: str = SlashOption(
                      name='emoji', description='The trigger emoji (unicode or custom).'),
                  threshold: int = SlashOption(
                      name='threshold', description='Reactions needed to repost.', min_value=1)):
        parsed = self._parse_emoji(emoji)
        if parsed is None:
            return await interaction.send(
                embed=messages.error(f'`{emoji}` is not a valid emoji.'), ephemeral=True)
        emoji_name, emoji_id = parsed
        if emoji_id is not None and not custom_emoji_belongs_to_guild(
                interaction.guild.emojis, emoji_id):
            return await interaction.send(
                embed=messages.error('That custom emoji is not from this server. '
                                     'Pick one of this server\'s own emoji.'),
                ephemeral=True)

        if starboard_helper.find_duplicate_config(
                interaction.guild_id, channel.id, emoji_name, emoji_id) is not None:
            return await interaction.send(
                embed=messages.error(
                    f'A starboard for {channel.mention} with {emoji} already exists.'),
                ephemeral=True)

        config = starboard_helper.add_config(
            guild_id=interaction.guild_id, target_channel_id=channel.id,
            emoji=emoji_name, emoji_id=emoji_id, threshold=threshold)

        confirmation = f'Starboard added: {board_summary(config, channel.mention)}'
        await interaction.send(
            embed=messages.success(confirmation + self._perms_warning(interaction, channel)),
            ephemeral=True)

    @starboard.subcommand(name='list', description='List this server\'s starboards.')
    async def list(self, interaction: Interaction):
        configs = starboard_helper.get_configs(interaction.guild_id)
        if not configs:
            return await interaction.send(
                embed=messages.info('No starboards configured for this server yet.'),
                ephemeral=True)
        # Up to _LIST_BOARDS_PER_PAGE boards per page, paged with ◀️/▶️ buttons.
        # The paginator drops the buttons when there is only one page.
        embeds = build_list_embeds(configs)
        await EmbedPaginatorView(embeds, noun='Page').send(interaction)

    @starboard.subcommand(name='edit', description='Edit an existing starboard.')
    async def edit(self, interaction: Interaction,
                   starboard: str = SlashOption(
                       name='starboard', description='The starboard to edit.'),
                   threshold: int = SlashOption(
                       name='threshold', description='New reaction threshold.',
                       required=False, min_value=1),
                   enabled: str = SlashOption(
                       name='enabled', description='Enable or disable the board.',
                       required=False, choices=['✅', '❌']),
                   channel: TextChannel = SlashOption(
                       name='channel', description='New destination channel.', required=False),
                   emoji: str = SlashOption(
                       name='emoji', description='New trigger emoji.', required=False)):
        config = self._resolve_board(interaction, starboard)
        if config is None:
            return await interaction.send(
                embed=messages.error('No such starboard on this server.'), ephemeral=True)

        updates = {}
        if threshold is not None:
            updates['threshold'] = threshold
        if enabled is not None:
            updates['enabled'] = enabled == '✅'
        if channel is not None:
            updates['target_channel_id'] = channel.id
        if emoji is not None:
            parsed = self._parse_emoji(emoji)
            if parsed is None:
                return await interaction.send(
                    embed=messages.error(f'`{emoji}` is not a valid emoji.'), ephemeral=True)
            emoji_name, emoji_id = parsed
            if emoji_id is not None and not custom_emoji_belongs_to_guild(
                    interaction.guild.emojis, emoji_id):
                return await interaction.send(
                    embed=messages.error('That custom emoji is not from this server.'),
                    ephemeral=True)
            updates['emoji'] = emoji_name
            updates['emoji_id'] = emoji_id

        if not updates:
            return await interaction.send(
                embed=messages.error('Nothing to update — provide at least one field.'),
                ephemeral=True)

        # Changing the channel or emoji must not collide with another board that
        # already targets that channel+emoji on this guild.
        if 'target_channel_id' in updates or 'emoji' in updates:
            new_channel = updates.get('target_channel_id', config.target_channel_id)
            new_emoji = updates.get('emoji', config.emoji)
            new_emoji_id = updates.get('emoji_id', config.emoji_id)
            if starboard_helper.find_duplicate_config(
                    interaction.guild_id, new_channel, new_emoji, new_emoji_id,
                    exclude_id=config.id) is not None:
                return await interaction.send(
                    embed=messages.error(
                        'Another starboard already targets that channel with that emoji.'),
                    ephemeral=True)

        updated = starboard_helper.update_config(config.id, **updates)
        summary = board_summary(updated, f'<#{updated.target_channel_id}>')
        await interaction.send(
            embed=messages.success(f'Starboard updated: {summary}'), ephemeral=True)

    @starboard.subcommand(name='remove', description='Remove a starboard.')
    async def remove(self, interaction: Interaction,
                     starboard: str = SlashOption(
                         name='starboard', description='The starboard to remove.')):
        config = self._resolve_board(interaction, starboard)
        if config is None:
            return await interaction.send(
                embed=messages.error('No such starboard on this server.'), ephemeral=True)
        summary = board_summary(config, f'<#{config.target_channel_id}>')
        starboard_helper.remove_config(config.id)
        await interaction.send(
            embed=messages.success(f'Starboard removed: {summary}'), ephemeral=True)

    @edit.on_autocomplete('starboard')
    async def _edit_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(
            self._board_choices(interaction, focused))

    @remove.on_autocomplete('starboard')
    async def _remove_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(
            self._board_choices(interaction, focused))

    # --- internal helpers ---------------------------------------------------

    @staticmethod
    def _parse_emoji(raw):
        """Parse ``raw`` into ``(name, id)`` or ``None`` on invalid input."""
        try:
            return parse_emoji_input(raw)
        except ValueError:
            return None

    @staticmethod
    def _perms_warning(interaction, channel) -> str:
        """Trailing warning string when the bot can't post to ``channel`` — empty
        when permissions look fine (mirrors auto_delete's perms preflight)."""
        perms = channel.permissions_for(interaction.guild.me)
        if perms.send_messages and perms.embed_links:
            return ''
        return ('\n\n⚠️ I may be missing **Send Messages** / **Embed Links** in '
                f'{channel.mention}; reposts there will fail until that\'s fixed.')

    @staticmethod
    def _resolve_board(interaction, starboard):
        """Resolve the ``starboard`` option (an id as a string) to one of THIS
        guild's configs, or ``None`` if it is malformed or belongs elsewhere."""
        try:
            config_id = int(starboard)
        except (TypeError, ValueError):
            return None
        config = starboard_helper.get_config(config_id)
        if config is None or config.guild_id != interaction.guild_id:
            return None
        return config

    @staticmethod
    def _board_choices(interaction, focused: str = '') -> dict:
        """``{label: str(id)}`` choices for autocomplete over the guild's boards,
        filtered by channel name when the user has typed something (case-insensitive
        substring). Boards whose channel can't be resolved are dropped from a
        filtered search since there is no name to match against."""
        configs = starboard_helper.get_configs(interaction.guild_id)
        query = (focused or '').strip().lower()
        choices = {}
        for config in configs:
            channel = interaction.guild.get_channel(config.target_channel_id) \
                if interaction.guild else None
            channel_name = channel.name if channel else None
            if query and (channel_name is None or query not in channel_name.lower()):
                continue
            choices[board_label(config, channel_name)] = str(config.id)
            if len(choices) >= _AUTOCOMPLETE_CHOICE_MAX:
                break
        return choices
