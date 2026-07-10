import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.utils.constants import BUMPERS_GUILD_ID
from db.helpers import user_activity_helper
from db.session_guard import recover_session


def register_event(bot: commands.Bot):
    @bot.event
    async def on_member_join(member: nextcord.Member):
        if member.bot:
            return
        if member.guild.id != BUMPERS_GUILD_ID:
            return

        try:
            user_activity_helper.setup_user(member.id, member.guild.id)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            recover_session()
