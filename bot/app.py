import cooldowns
import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.cogs import AutoDeleteCommands, ImageMessageDeleteCommands, StarboardCommands
from bot.cogs.admin.admin_commands import AdminCommands
from bot.cogs.birthday.birthday_commands import BirthdayCommands
from bot.cogs.polls.poll_commands import PollCommands
from bot.cogs.server_admin.server_admin_commands import ServerAdminCommands
from bot.cogs.threads.threads_commands import ThreadsCommands
from bot.config import Config
from bot.events import on_member_join_event, on_guild_join_event, on_raw_reaction_add_event, \
    on_raw_reaction_remove_event, on_raw_message_delete_event
from bot.events.on_message_event import register_event
from bot.utils import logger, messages

intents = nextcord.Intents(messages=True, message_content=True, guild_reactions=True, guilds=True)
config = Config()
bot = commands.Bot(intents=intents)

if config.is_prod:
    sentry_sdk.init(
        dsn="https://00565c13fed21a6b54e808aad5acaea1@o250406.ingest.sentry.io/4505959527284736",
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        traces_sample_rate=1.0,
        # Set profiles_sample_rate to 1.0 to profile 100%
        # of sampled transactions.
        # We recommend adjusting this value in production.
        profiles_sample_rate=1.0,
    )
else:
    sentry_sdk.init(dsn='')


@bot.event
async def on_ready():
    await bot.change_presence(activity=nextcord.Game(name='Mrs. Potts', type=nextcord.ActivityType.playing))
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')


@bot.event
async def on_application_command_error(inter: nextcord.Interaction, error):
    error = getattr(error, "original", error)

    if isinstance(error, cooldowns.CallableOnCooldown):
        await inter.send(
            embed=messages.error(f"You are being rate-limited! Retry in `{error.retry_after}` seconds."),
            ephemeral=True
        )

    else:
        raise error


def run():
    bot.add_cog(AutoDeleteCommands(bot))
    bot.add_cog(ImageMessageDeleteCommands(bot))
    # bot.add_cog(ActivityCommands(bot))
    bot.add_cog(BirthdayCommands(bot))
    bot.add_cog(AdminCommands(bot))
    bot.add_cog(ThreadsCommands(bot))
    bot.add_cog(ServerAdminCommands())
    bot.add_cog(PollCommands(bot))
    bot.add_cog(StarboardCommands(bot))
    logger.register_bot(bot)
    on_member_join_event.register_event(bot)
    on_guild_join_event.register_event(bot)
    on_raw_reaction_add_event.register_event(bot)
    on_raw_reaction_remove_event.register_event(bot)
    on_raw_message_delete_event.register_event(bot)
    register_event(bot)
    bot.run(config.BOT_TOKEN)

