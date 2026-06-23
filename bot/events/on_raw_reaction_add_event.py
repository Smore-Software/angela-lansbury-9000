import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.events.handlers import temp_discussion_handler, starboard_handler


def register_event(bot: commands.Bot):
    # NOTE: @bot.event keeps only ONE callback per event name, so both handlers
    # must be chained INSIDE this single closure. Each call is isolated in its own
    # try/except so an exception in one can never swallow the other.
    @bot.event
    async def on_raw_reaction_add(payload: nextcord.RawReactionActionEvent):
        try:
            await temp_discussion_handler.handle_closure_react(bot, payload)
        except Exception as e:
            sentry_sdk.capture_exception(e)
        try:
            await starboard_handler.handle_reaction_add(bot, payload)
        except Exception as e:
            sentry_sdk.capture_exception(e)
