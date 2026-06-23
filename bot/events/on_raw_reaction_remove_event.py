import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.events.handlers import temp_discussion_handler, starboard_handler


def register_event(bot: commands.Bot):
    # See on_raw_reaction_add_event: one callback per event name, so chain both
    # handlers here, each isolated so neither can swallow the other.
    @bot.event
    async def on_raw_reaction_remove(payload: nextcord.RawReactionActionEvent):
        try:
            await temp_discussion_handler.handle_closure_react(bot, payload)
        except Exception as e:
            sentry_sdk.capture_exception(e)
        try:
            await starboard_handler.handle_reaction_remove(bot, payload)
        except Exception as e:
            sentry_sdk.capture_exception(e)
