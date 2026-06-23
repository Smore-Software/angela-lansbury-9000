import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.events.handlers import starboard_handler


def register_event(bot: commands.Bot):
    # RawMessageDeleteEvent carries message_id/channel_id/guild_id and fires even
    # for uncached messages, so deletes are honoured regardless of cache state.
    @bot.event
    async def on_raw_message_delete(payload: nextcord.RawMessageDeleteEvent):
        try:
            await starboard_handler.handle_message_delete(bot, payload)
        except Exception as e:
            sentry_sdk.capture_exception(e)
