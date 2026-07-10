import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.events.handlers.activity_handler import activity_handler
from bot.events.handlers.image_message_handler import image_message_handler
from db.session_guard import recover_session


def register_event(bot: commands.Bot):
    @bot.event
    async def on_message(message: nextcord.Message):
        if message.author == bot.user:
            return

        # Each handler is isolated in its own try/except so a failure in one
        # (and its poisoned session) can never starve the other. See the
        # chained-handler pattern in on_raw_reaction_add_event.py:9-11.
        try:
            await image_message_handler(message)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            recover_session()
        try:
            await activity_handler(message)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            recover_session()
