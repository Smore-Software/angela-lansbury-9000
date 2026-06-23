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
from db.helpers import starboard_helper

# Discord caps autocomplete labels at 100 chars and 25 choices per response.
_AUTOCOMPLETE_LABEL_MAX = 100
_AUTOCOMPLETE_CHOICE_MAX = 25
# Discord caps an embed at 25 fields; we render one field per board.
_LIST_FIELDS_PER_EMBED = 25


def emoji_display(config) -> str:
    """Render a config's stored emoji back to a displayable form: ``<:name:id>``
    for a custom emoji, the raw unicode char otherwise."""
    if config.emoji_id is not None:
        return f'<:{config.emoji}:{config.emoji_id}>'
    return config.emoji


def board_label(config, channel_name: str | None = None) -> str:
    """Short, plain-text label for autocomplete: the friendly name when set, else
    ``#channel · emoji · ≥N``. Falls back to a channel mention when the channel
    name could not be resolved. Truncated to Discord's autocomplete label limit."""
    if config.name:
        label = config.name
    else:
        channel = f'#{channel_name}' if channel_name else f'<#{config.target_channel_id}>'
        label = f'{channel} · {emoji_display(config)} · ≥{config.threshold}'
    if len(label) > _AUTOCOMPLETE_LABEL_MAX:
        label = label[:_AUTOCOMPLETE_LABEL_MAX - 1] + '…'
    return label


def build_list_embeds(configs) -> list:
    """Build one or more embeds describing a guild's boards, chunked to stay under
    Discord's 25-fields-per-embed limit (``list`` paginates only when needed)."""
    embeds = []
    for start in range(0, len(configs), _LIST_FIELDS_PER_EMBED):
        chunk = configs[start:start + _LIST_FIELDS_PER_EMBED]
        embed = nextcord.Embed(color=messages.INFO_COLOR, title='Starboards')
        for config in chunk:
            status = 'enabled' if config.enabled else 'disabled'
            name = config.name or '(unnamed)'
            embed.add_field(
                name=f'#{config.id} · {name}',
                value=(f'<#{config.target_channel_id}> · {emoji_display(config)} · '
                       f'≥{config.threshold} · {status}'),
                inline=False,
            )
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
                      name='threshold', description='Reactions needed to repost.', min_value=1),
                  name: str = SlashOption(
                      name='name', description='Optional friendly label.', required=False)):
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

        starboard_helper.add_config(
            guild_id=interaction.guild_id, target_channel_id=channel.id,
            emoji=emoji_name, emoji_id=emoji_id, threshold=threshold, name=name)

        confirmation = f'Starboard added: {channel.mention} · {emoji} · ≥{threshold}.'
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
        # interaction.send accepts at most 10 embeds; that bound is far beyond any
        # realistic board count but keeps a pathological guild from erroring.
        await interaction.send(embeds=build_list_embeds(configs)[:10], ephemeral=True)

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
                       name='emoji', description='New trigger emoji.', required=False),
                   name: str = SlashOption(
                       name='name', description='New friendly label.', required=False)):
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
        if name is not None:
            updates['name'] = name
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

        starboard_helper.update_config(config.id, **updates)
        await interaction.send(embed=messages.success('Starboard updated.'), ephemeral=True)

    @starboard.subcommand(name='remove', description='Remove a starboard.')
    async def remove(self, interaction: Interaction,
                     starboard: str = SlashOption(
                         name='starboard', description='The starboard to remove.')):
        config = self._resolve_board(interaction, starboard)
        if config is None:
            return await interaction.send(
                embed=messages.error('No such starboard on this server.'), ephemeral=True)
        starboard_helper.remove_config(config.id)
        await interaction.send(
            embed=messages.success('Starboard removed.'), ephemeral=True)

    @edit.on_autocomplete('starboard')
    async def _edit_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(self._board_choices(interaction))

    @remove.on_autocomplete('starboard')
    async def _remove_autocomplete(self, interaction: Interaction, focused: str):
        await interaction.response.send_autocomplete(self._board_choices(interaction))

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
    def _board_choices(interaction) -> dict:
        """``{label: str(id)}`` choices for autocomplete over the guild's boards."""
        configs = starboard_helper.get_configs(interaction.guild_id)
        choices = {}
        for config in configs[:_AUTOCOMPLETE_CHOICE_MAX]:
            channel = interaction.guild.get_channel(config.target_channel_id) \
                if interaction.guild else None
            channel_name = channel.name if channel else None
            choices[board_label(config, channel_name)] = str(config.id)
        return choices
