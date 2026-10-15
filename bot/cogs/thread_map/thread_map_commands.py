from datetime import datetime, timezone

import nextcord
import sentry_sdk
from nextcord import Interaction, Permissions, SlashOption, slash_command
from nextcord.ext import commands

from bot.cogs.thread_map.thread_map_render import build_embed, is_admin_role
from bot.cogs.thread_map.thread_map_service import render_pages
from bot.cogs.thread_map.thread_map_view import ThreadMapView, forget_map
from bot.utils import messages
from db import DB
from db.helpers import thread_map_helper


class ThreadMapCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # Not in __init__: View.__init__ needs a running loop, and add_cog runs before bot.run.
        try:
            self.bot.add_view(ThreadMapView())
        except Exception as e:
            sentry_sdk.capture_message('Failed to register the thread map view! Check log immediately.')
            sentry_sdk.capture_exception(e)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: nextcord.RawMessageDeleteEvent):
        try:
            with DB.Session() as session:
                map_id = thread_map_helper.delete_by_message(session, payload.message_id)
                if map_id is not None:
                    session.commit()
            if map_id is not None:
                forget_map(map_id)
        except Exception as e:
            sentry_sdk.capture_exception(e)

    @slash_command(name='thread-map',
                   description='Post a map of active threads, grouped by channel.',
                   force_global=True,
                   default_member_permissions=Permissions(manage_guild=True))
    async def thread_map(self, interaction: Interaction,
                         role: nextcord.Role = SlashOption(name='role', required=False,
                                                           description='Also show channels this role can see.')):
        channel = interaction.channel
        if not isinstance(channel, nextcord.TextChannel):
            await interaction.send(embed=messages.error('A thread map can only be posted in a text or '
                                                        'announcement channel.'), ephemeral=True)
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.send(embed=messages.error('I need **Send Messages** and **Embed Links** in this '
                                                        'channel to post a thread map.'), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        # The role option resolves to None when the guild isn't cached; @everyone is the baseline already.
        role_ids = [role.id] if role is not None and not role.is_default() else []

        rendered = await render_pages(interaction.guild, role_ids, [])
        now = datetime.now(timezone.utc)
        page_count = len(rendered.pages)
        try:
            message = await channel.send(embed=build_embed(rendered.pages[0], 0, page_count, now),
                                         view=ThreadMapView(0, page_count))
        except nextcord.HTTPException as e:
            sentry_sdk.capture_exception(e)
            await interaction.send(embed=messages.error('I couldn\'t post the thread map here.'), ephemeral=True)
            return

        # Posted first, recorded second, so a failed send never leaves an orphan row.
        try:
            with DB.Session() as session:
                thread_map_helper.create_map(session, guild_id=interaction.guild.id, channel_id=channel.id,
                                             message_id=message.id, created_by=interaction.user.id,
                                             role_ids=role_ids, pages=rendered.pages, refreshed_at=now)
                session.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            try:
                await message.delete()
            except nextcord.HTTPException:
                pass
            await interaction.send(embed=messages.error('Something went wrong saving the thread map. '
                                                        'Please try again.'), ephemeral=True)
            return

        reply = 'Thread map posted.'
        if role_ids and is_admin_role(role):
            reply += (f'\n\n⚠️ {role.mention} has Administrator, so the map lists every channel I can see, '
                      'including private ones.')
        await interaction.send(embed=messages.success(reply), ephemeral=True)
