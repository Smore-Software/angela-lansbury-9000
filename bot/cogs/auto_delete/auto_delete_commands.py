"""
AutoDeleteCommands
/auto-delete config
/auto-delete stop

This cog holds the task coroutine responsible for identifying and deleting stale messages as well as the slash
commands listed above that configure channels to get messages auto-deleted.
"""
from typing import List

import nextcord
import sentry_sdk
from nextcord import slash_command, Interaction, SlashOption, Permissions
from nextcord.ext import commands, tasks

from bot.cogs.auto_delete import auto_delete_helper
from bot.utils import messages
from bot.utils.bot_utils import get_or_fetch_guild, get_or_fetch_channel
from bot.utils.loop_recovery import recover_loop
from db import AutoDeleteType, DB, AutoDeleteChannelConfig


class AutoDeleteCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_for_stale_messages.start()

    @tasks.loop(seconds=60)
    async def check_for_stale_messages(self):
        """
        This is the loop handler that periodically (every 30s) checks if any configured channels have stale messages
        that need to be pruned.
        """
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        configs: List[AutoDeleteChannelConfig] = DB.s.all(AutoDeleteChannelConfig)
        for config in configs:
            guild = await get_or_fetch_guild(self.bot, config.guild_id)
            if guild is None:
                remove_config_and_report(config, f'Guild not found for config {config}')
                continue
            channel: nextcord.TextChannel = await get_or_fetch_channel(self.bot, config.channel_id)
            if channel is None:
                remove_config_and_report(config, f'Channel not found for config {config} and guild {guild}')
                continue
            try:
                anchor_message = await auto_delete_helper.fetch_anchor_message(channel, config)
            except nextcord.NotFound:
                await channel.send(embed=messages.error('Failed to find anchor message for auto delete. '
                                                        'Please reconfigure auto-delete in this channel.'))
                auto_delete_helper.remove_channel_config(config.guild_id, config.channel_id)
                continue
            except nextcord.Forbidden:
                print(f'Forbidden to read message history in channel {channel.name} ({guild.name})')
                remove_config_and_report(config, f'Forbidden to read message history in channel {channel.name}'
                                                 f'({guild.name})')
                await guild.owner.send(embed=messages.error('It looks like I don\'t have Read Message History permissions '
                                                            f'for this {channel.name}. Please reconfigure auto-delete.'))
                continue
            try:
                all_messages = await channel.history(after=anchor_message, limit=10).flatten()
                stale_messages = auto_delete_helper.get_stale_messages(all_messages, config)
                for m in stale_messages:
                    await m.delete()
            except nextcord.Forbidden:
                print(f'Forbidden to read message history in channel {channel.name} ({guild.name})')
                sentry_sdk.capture_message(f'Unable to delete messages in channel {channel.name} ({guild.name})')
                remove_config_and_report(config, f'Forbidden to read/delete messages in channel {channel.name}'
                                                 f'({guild.name})')
                await guild.owner.send(embed=messages.error('It looks like I don\'t have the correct permissions '
                                                            f'for {channel.name}. Please reconfigure auto-delete.'))

    @check_for_stale_messages.error
    async def check_for_stale_messages_error(self, e):
        await recover_loop(self.check_for_stale_messages, e)

    @slash_command(name='channel-auto-delete', default_member_permissions=Permissions(manage_guild=True))
    async def auto_delete(self, interaction: Interaction):
        """
        This is the command prefix for the following sub-commands enumerated below.
        """
        pass

    @auto_delete.subcommand(description='Configure auto-delete settings for the current channel')
    async def config(self,
                     interaction: Interaction,
                     delete: str = SlashOption(description='Types of messages to delete',
                                               choices=AutoDeleteType.list()),
                     delete_after: int = SlashOption(description='Delay before deleting messages, in MINUTES')):
        if not isinstance(interaction.channel, nextcord.TextChannel):
            return await interaction.send(embed=messages.error('This command can only be used in a server\'s text '
                                                               'channel.'))
        if not self._has_perms(interaction):
            return await interaction.send(embed=messages.error('I need Manage Messages and Read Message History '
                                                               'permissions for this channel to configure auto-delete.'), ephemeral=True)
        try:
            minutes_string = convert_minutes_to_human_readable(delete_after)
        except ValueError as e:
            return await interaction.send(embed=messages.error(str(e)))
        embed = messages.success(f'{delete.title()} messages in this channel '
                                 f'posted after this message will be deleted '
                                 f'after {minutes_string}.\n\n'
                                 f"(**Don't** delete this message. I use "
                                 f"it to anchor searches against this channel's history.)")
        current_config: AutoDeleteChannelConfig = DB.s.first(AutoDeleteChannelConfig,
                                                             guild_id=interaction.guild_id,
                                                             channel_id=interaction.channel_id)
        if current_config is None:
            await interaction.send(embed=embed)
            message = await interaction.original_message()
            current_config = AutoDeleteChannelConfig(guild_id=interaction.guild_id,
                                                     channel_id=interaction.channel_id,
                                                     auto_delete_type=delete,
                                                     delete_after_minutes=delete_after,
                                                     anchor_message=message.id,
                                                     original_anchor_message=message.id)
            DB.s.add(current_config)
        else:
            message = await interaction.channel.fetch_message(current_config.original_anchor_message)
            if message is None:
                await interaction.send(embed=embed)
                message = await interaction.original_message()
                current_config.original_anchor_message = message.id
            else:
                await message.edit(embed=embed)
                await interaction.send(embed=messages.success(f'Modified existing channel config. {message.jump_url}'),
                                       ephemeral=True)
            current_config.auto_delete_type = delete
            current_config.delete_after_minutes = delete_after
            current_config.anchor_message = message.id
        DB.s.commit()

    @auto_delete.subcommand(description='Stop auto-deletion in this channel.')
    async def stop(self, interaction: Interaction):
        try:
            auto_delete_helper.remove_channel_config(interaction.guild_id, interaction.channel_id)
        except auto_delete_helper.ConfigNotFound:
            return await interaction.send(
                embed=messages.error("This channel isn't configured to auto-delete messages."))
        await interaction.send(embed=messages.success('Stopped auto-deletions in this channel.'))

    @staticmethod
    def _has_perms(interaction: Interaction):
        perms = interaction.channel.permissions_for(interaction.guild.me)
        return perms.manage_messages and perms.read_message_history


def remove_config_and_report(config: AutoDeleteChannelConfig, message: str):
    DB.s.delete(config)
    DB.s.commit()
    sentry_sdk.capture_message(message)


def convert_minutes_to_human_readable(minutes):
    if not isinstance(minutes, int):
        raise ValueError("delete_after must be an integer.")

    if minutes < 0:
        raise ValueError("delete_after must be non-negative.")

    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} and {remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}"

    days = hours // 24
    remaining_hours = hours % 24

    return f"{days} day{'s' if days != 1 else ''}, {remaining_hours} hour{'s' if remaining_hours != 1 else ''}, and {remaining_minutes} minute{'s' if remaining_minutes != 1 else ''}"
