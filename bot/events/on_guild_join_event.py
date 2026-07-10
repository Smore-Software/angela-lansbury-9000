import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.utils.logger import get_logger, LoggingLevel
from db.session_guard import recover_session


def register_event(bot: commands.Bot):
    @bot.event
    async def on_guild_join(guild: nextcord.Guild):
        try:
            logger = get_logger(LoggingLevel.GENERAL)
            await logger.log(f'Joined guild `{guild.name}`')
            if guild.owner:
                await guild.owner.send('👋 Thanks for adding me! If you have questions or concerns, or just want to be '
                                       'updated on new developments or issues, feel free to join '
                                       'https://discord.gg/NQm54zS82w')
        except Exception as e:
            sentry_sdk.capture_exception(e)
            recover_session()
