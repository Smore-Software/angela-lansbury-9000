from datetime import datetime, timedelta

import nextcord
import sentry_sdk
from nextcord.ext import commands

from bot.cogs.activity.views.activity_stats_view import ActivityStatsView
from bot.utils import messages
from bot.utils.logger import get_logger, LoggingLevel
from db.helpers import rolling_message_log_helper, activity_module_settings_helper, activity_role_helper, \
    user_activity_helper
from db.model.activity_role import ActivityRole


"""
Task Functions
"""


async def activity_roles_for_member(member: nextcord.Member, activity_roles: list[ActivityRole]):
    class Mutations:
        def __init__(self):
            self.add = []
            self.remove = []
            self.errored = False

    mutations = Mutations()
    logger = get_logger(LoggingLevel.ACTIVITY, member.guild.id)
    user_activity = user_activity_helper.get_or_create(member.id, member.guild.id)
    for ar in activity_roles:
        message_count = rolling_message_log_helper.message_count_for_author(member.id,
                                                                            member.guild.id,
                                                                            days=ar.rolling_month_window * 30,
                                                                            divisor=ar.rolling_month_window)

        if ar.grace_period_months and \
                datetime.utcnow() - user_activity.tracking_started_on < timedelta(days=30 * ar.grace_period_months):
            # grace period is still active
            continue
        min_messages = ar.min if ar.min else float('-inf')
        max_messages = ar.max if ar.max else float('inf')
        if min_messages <= message_count <= max_messages:
            if ar.role not in member.roles:
                if ar.should_notify:
                    await logger.log(f'{member.mention} has been given the `{ar.role_name}` role.')
                mutations.add.append(ar.role)
        else:
            if ar.role in member.roles:
                if ar.should_notify:
                    await logger.log(f'{member.mention} has had the `{ar.role_name}` role removed.')
                mutations.remove.append(ar.role)
    return mutations


async def process_roles_for_member(member: nextcord.Member, activity_roles: list[ActivityRole]):
    mutations = await activity_roles_for_member(member, activity_roles)
    try:
        if mutations.add:
            await member.add_roles(*mutations.add, reason='Activity role assignment')
        if mutations.remove:
            await member.remove_roles(*mutations.remove, reason='Activity role removal')
    except nextcord.Forbidden:
        mutations.errored = True
    except nextcord.HTTPException as e:
        sentry_sdk.capture_exception(e)
        mutations.errored = True
    return mutations


async def process_activity_roles_task(bot: commands.Bot):
    activity_role_dict = activity_role_helper.get_activity_roles_by_guild_id()
    for guild_id, activity_roles in activity_role_dict.items():
        activity_config = activity_module_settings_helper.get_settings(guild_id)
        on_break_role_id = activity_config.model.break_role_id
        logger = get_logger(LoggingLevel.ACTIVITY, guild_id)
        guild = bot.get_guild(guild_id)
        bot_member = guild.get_member(bot.user.id)
        if not bot_member.guild_permissions.manage_roles:
            await logger.log('Angela does not have permission to manage roles. Unable to manage activity roles.')
            continue
        if not guild:
            continue
        for activity_role in activity_roles:
            activity_role.role = guild.get_role(activity_role.role_id)
            if activity_role.role is None:
                await logger.log(f'Activity role `{activity_role.role_name}` not found.')
        failures = []
        for member in guild.members:
            if member.bot:
                continue
            if nextcord.utils.get(member.roles, id=on_break_role_id):
                print('hit on break role check')
                continue
            mutations = await process_roles_for_member(member, activity_roles)
            if mutations.errored:
                failures.append(member)
        if failures:
            failure_mentions = [f.mention for f in failures]
            await logger.log(f'Failed to assign activity roles to the following members: {", ".join(failure_mentions)}.')


"""
Activity Command Functions
"""


async def show_stats(interaction: nextcord.Interaction):
    messages_last_thirty = rolling_message_log_helper.message_count_for_author(interaction.user.id,
                                                                               interaction.guild_id,
                                                                               30)
    messages_last_ninety = rolling_message_log_helper.message_count_for_author(interaction.user.id,
                                                                               interaction.guild_id,
                                                                               days=90,
                                                                               divisor=3)
    embed = messages.info('Here are your activity stats.')
    embed.add_field(name='Rolling monthly average (last 90 days)', value=f'**{messages_last_ninety}**')
    embed.add_field(name='Messages sent in the last 30 days', value=messages_last_thirty, inline=False)
    view = ActivityStatsView(embed)
    await interaction.send(embed=embed, view=view, ephemeral=True)


"""
Activity Admin Command Functions
"""


async def add_activity_role(interaction: nextcord.Interaction,
                            role: nextcord.Role,
                            min_messages: int = None,
                            max_messages: int = None,
                            should_notify: bool = False,
                            rolling_month_window: int = 1,
                            grace_period_months: int = 0):
    activity_role = activity_role_helper.get_activity_role(interaction.guild_id, role.id)
    did_exist = activity_role is not None
    if not activity_role:
        activity_role = ActivityRole(guild_id=interaction.guild_id,
                                     role_id=role.id,
                                     role_name=role.name,
                                     min=min_messages,
                                     max=max_messages,
                                     should_notify=should_notify,
                                     rolling_month_window=rolling_month_window,
                                     grace_period_months=grace_period_months)
    else:
        activity_role.min = min_messages
        activity_role.max = max_messages
        activity_role.should_notify = should_notify
        activity_role.rolling_month_window = rolling_month_window
        activity_role.grace_period_months = grace_period_months
    if activity_role.errors:
        await interaction.response.send_message('\n'.join(activity_role.errors), ephemeral=True)
        return
    activity_role_helper.save(activity_role)
    await interaction.response.send_message(f'Activity role `{role.name}` {"updated" if did_exist else "added"}.',
                                            ephemeral=True)


async def remove_activity_role(interaction: nextcord.Interaction, role: nextcord.Role):
    activity_role = activity_role_helper.get_activity_role(interaction.guild_id, role.id)
    if activity_role:
        activity_role_helper.delete(activity_role)
        await interaction.response.send_message(f'Activity role `{role.name}` removed.', ephemeral=True)
    else:
        await interaction.response.send_message(f'Activity role `{role.name}` not found.', ephemeral=True)


async def show_server_stats(interaction: nextcord.Interaction):
    settings = activity_module_settings_helper.get_settings(interaction.guild_id)
    all_stats = rolling_message_log_helper.get_all_ninety_day_stats(interaction.guild_id)
    embed = nextcord.Embed(title='Server Activity Stats', color=nextcord.Color.blurple())
    description = 'Each member\'s rolling monthly average message count:\n\n'
    member_count = 0
    inactive_count = 0
    for user_id, count in all_stats:
        member = interaction.guild.get_member(user_id)
        if member:
            member_count += 1
            member_name = f'**{member.mention}**'
            if nextcord.utils.get(member.roles, id=settings.model.inactive_role_id):
                description += f'{member_name}: {count} (**Inactive**)\n'
                inactive_count += 1
            else:
                description += f'{member_name}: {count}\n'
    description += f'\nTotal active members: {member_count - inactive_count}\nInactive members: {inactive_count}'
    embed.description = description
    await interaction.send(embed=embed, ephemeral=True)


async def show_settings(interaction: nextcord.Interaction, bot: commands.Bot):
    settings = activity_module_settings_helper.get_settings(interaction.guild_id)
    inactive_role = None
    excluded_channels = None
    break_role = None
    if settings.model.inactive_role_id:
        inactive_role = interaction.guild.get_role(settings.model.inactive_role_id)
    if settings.excluded_channels:
        excluded_channels = [_get_channel_if_exists(bot, c_id) for c_id in settings.excluded_channels]
    if settings.model.break_role_id:
        break_role = interaction.guild.get_role(settings.model.break_role_id)
    message = messages.info('Here are your current activity settings:')
    message.add_field(name='Inactive Role', value=inactive_role.mention if inactive_role else 'Not Set')
    message.add_field(name='Break Role', value=break_role.mention if break_role else 'Not Set')
    message.add_field(name='Excluded Channels',
                      value='\n'.join([c for c in excluded_channels]) if excluded_channels else 'None')
    await interaction.send(embed=message, ephemeral=True)


def _get_channel_if_exists(bot, channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        return 'Channel Not Found'
    return channel.mention
