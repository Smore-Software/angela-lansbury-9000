"""
ImageMessageDeleteCommands

This cog doesn't have any commands (yet), but it does hold the looping task that checks for any messages users
have marked for eventual deletion.
"""

import asyncio
import datetime as dt
from typing import List

import nextcord
import sentry_sdk
import sqlalchemy as sa
from nextcord import slash_command, Permissions, Interaction, SlashOption, TextChannel
from nextcord.ext import commands, tasks

from bot.cogs.image_message_delete.views.configure_prompts_view import ConfigurePromptsView
from bot.utils import messages
from bot.utils.constants import BUMPERS_GUILD_ID, TESTING_GUILD_ID
from db import DB, ImageMessageToDelete
from db.helpers import image_message_helper, user_settings_helper, guild_config_helper


async def handle_thread_message(thread: nextcord.Thread,
                                message: nextcord.Message):
    if not thread.archived:
        await message.delete()
        return
    if not message.channel.permissions_for(message.guild.me).manage_threads:
        await message.author.send(f'👋 I was unable to delete the following message in a thread because the thread '
                                  f'is closed, and I lack the necessary permissions: '
                                  f'{message.jump_url}')
        return
    was_archived = thread.archived
    await thread.edit(archived=False)
    await message.delete()
    await thread.edit(archived=was_archived)


class ImageMessageDeleteCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_for_expired_messages.start()

    @tasks.loop(hours=1)
    async def check_for_expired_messages(self):
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        expired_messages: List[ImageMessageToDelete] = DB.s.execute(
            sa.select(ImageMessageToDelete)
                .where(ImageMessageToDelete.delete_after < now)
                .where(ImageMessageToDelete.has_failed == None)
        ).scalars().all()
        for db_message_to_delete in expired_messages:
            guild = self.bot.get_guild(db_message_to_delete.guild_id)
            try:
                channel = guild.get_channel(db_message_to_delete.channel_id)
                if not channel:
                    channel = await self.bot.fetch_channel(db_message_to_delete.channel_id)
                message = await channel.fetch_message(db_message_to_delete.message_id)
                if isinstance(channel, nextcord.Thread):
                    await handle_thread_message(channel, message)
                else:
                    await message.delete()
                db_message_to_delete.has_failed = False
            except nextcord.NotFound:
                # The message (or its channel) no longer exists, so the desired
                # end state — message gone — is already true. Treat as success.
                db_message_to_delete.has_failed = False
            except Exception as e:
                sentry_sdk.capture_exception(e)
                db_message_to_delete.has_failed = True
            finally:
                DB.s.commit()

    @check_for_expired_messages.error
    async def check_for_expired_messages_error(self, e):
        sentry_sdk.capture_exception(e)
        await asyncio.sleep(60)
        self.check_for_expired_messages.restart()

    @slash_command(name='image-prompts', force_global=True)
    async def image_deleter_settings(self, interaction: Interaction):
        pass

    @image_deleter_settings.subcommand(name='configure',
                                       description='Change your settings for image deletion in a channel.')
    async def channel_default(self, interaction: Interaction):
        if not await self.check_enabled(interaction):
            return
        try:
            view = ConfigurePromptsView(interaction.guild_id,
                                        interaction.user,
                                        interaction.guild.text_channels,
                                        interaction.guild.me,
                                        interaction.guild)
            await interaction.send(content=view.current_page_content,
                                   view=view,
                                   ephemeral=True)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await interaction.send(embed=messages.error('An error occurred while trying to configure your image '
                                                        'deletion settings.'),
                                   ephemeral=True)
            raise e

    @staticmethod
    async def check_enabled(interaction: Interaction):
        guild_config = guild_config_helper.get_guild_config(interaction.guild_id)
        if not guild_config.image_deletion_prompts_enabled:
            await interaction.send(embed=messages.info('Image deletion is not enabled in this server.'),
                                   ephemeral=True)
            return False
        return True
