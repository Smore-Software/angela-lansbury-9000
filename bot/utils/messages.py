import nextcord
import random

from db.model.birthday import Birthday

INFO_COLOR = 3700200
ERROR_COLOR = 16725552
SUCCESS_COLOR = 32768


def message_has_image(message: nextcord.Message):
    attachments_has_image = next(filter(lambda a: a.content_type and 'image' in a.content_type, message.attachments), False)
    attachments_has_video = next(filter(lambda a: a.content_type and 'video' in a.content_type, message.attachments), False)
    embeds_has_image = next(filter(lambda e: e.type == 'image', message.embeds), False)
    embeds_has_video = next(filter(lambda e: e.type == 'video', message.embeds), False)
    return attachments_has_image or attachments_has_video or embeds_has_image or embeds_has_video


def starboard_content(emoji_display: str, count: int, jump_url: str) -> str:
    """The ``{emoji} **× {count}** · [Source ↗](url)`` line that leads the starboard
    repost *message* (not the embed). Everything that needs to render as a real
    link or a real emoji lives here: a custom-emoji mention and a markdown
    hyperlink both render in message content but not in an embed footer (mention →
    raw ``<:name:id>`` text) or footer (no links at all)."""
    return f'{emoji_display} **× {count}** · [Source ↗]({jump_url})'


def starboard_embed(message: nextcord.Message, source_channel) -> nextcord.Embed:
    """Build the embed reposted to a starboard's target channel.

    Attribution (author + avatar), the original content, an inlined image (only
    when the message actually carries an image attachment — an embed can inline at
    most one), links to any remaining attachments, and a ``#channel`` source
    footer. The live ``{emoji} (count)`` tally and the back-link to the original
    both live in the message *content* (see ``starboard_content``), not the embed,
    because an embed footer renders a custom-emoji mention as raw ``<:name:id>``
    text and cannot hold a hyperlink. ``timestamp`` mirrors the original message so
    the repost sorts by when it was written. Handles media-only messages (empty
    content) without issue.
    """
    embed = nextcord.Embed(
        color=INFO_COLOR,
        description=message.content or None,
        timestamp=message.created_at,
    )
    embed.set_author(name=message.author.display_name,
                     icon_url=message.author.display_avatar.url)

    image_attachment = None
    if message_has_image(message):
        image_attachment = next(
            (a for a in message.attachments if a.content_type and 'image' in a.content_type),
            None,
        )
        if image_attachment is not None:
            embed.set_image(url=image_attachment.url)

    # Anything we did not inline (other images, video, files) is listed as a link.
    extra_attachments = [a for a in message.attachments if a is not image_attachment]
    if extra_attachments:
        links = '\n'.join(f'[{a.filename}]({a.url})' for a in extra_attachments)
        embed.add_field(name='Attachments', value=links, inline=False)

    embed.set_footer(text=f'#{source_channel}')
    return embed


def info(message: str):
    return nextcord.Embed(color=INFO_COLOR, description=message)


def error(message: str):
    return nextcord.Embed(color=ERROR_COLOR, description=message)


def success(message: str):
    return nextcord.Embed(color=SUCCESS_COLOR, description=message)


def santa_message(message: str, sender: nextcord.User, show_name=False):
    embed = nextcord.Embed(description=message)
    name = sender.name if show_name else 'Message from your Santa!'
    icon_url = sender.avatar.url if sender.avatar and show_name else None
    embed.set_author(name=name, icon_url=icon_url)
    return embed


def get_random_angela_gif():
    angela_gifs = [
        "https://media.giphy.com/media/MF41YrnoSgZEY/giphy.gif",
        "https://media.giphy.com/media/3ohs4kPMizP0sn727e/giphy.gif",
        "https://media.giphy.com/media/26gspGYxmtaUUTq7u/giphy.gif",
        "https://media.giphy.com/media/l0ExghDSRxU2g55sc/giphy.gif",
        "https://media.giphy.com/media/l0ExiVMKsnIy0tzBC/giphy.gif",
        "https://media.giphy.com/media/36fSgmwvt8aEpD9w7w/giphy.gif",
    ]
    return random.choice(angela_gifs)


def birthday_message():
    embed = nextcord.Embed(color=SUCCESS_COLOR, title='Happiest birthday from Angela Lansbury 9000!')
    embed.set_image(url=get_random_angela_gif())
    return embed


def get_birthday_number(year: int):
    current_year = nextcord.utils.utcnow().year
    birthday_number = current_year - year
    if birthday_number <= 0:
        return "0th"
    SUFFIXES = {1: 'st', 2: 'nd', 3: 'rd'}
    if 10 <= birthday_number % 100 <= 20:
        suffix = 'th'
    else:
        suffix = SUFFIXES.get(birthday_number % 10, 'th')
    return str(birthday_number) + suffix


def birthday_entry(embed: nextcord.Embed, birthday: Birthday, member: nextcord.Member):
    embed.add_field(name='\u200b',
                    value=f'{member.mention} a very happy {get_birthday_number(birthday.year)} birthday to {birthday.name.title()}!',
                    inline=False)
    return embed


def get_special_birthday_fields(embed: nextcord.Embed):
    # if today is October 16th
    if nextcord.utils.utcnow().strftime('%m-%d') == '10-16':
        embed.add_field(name='\u200b',
                        value=f'And a very happy {get_birthday_number(1925)} birthday to dearest Dame Angela Brigid Lansbury, may she rest in peace!',
                        inline=False)
    # if today is March 1st and it's not a leap year
    if nextcord.utils.utcnow().strftime('%m-%d') == '03-01' and not nextcord.utils.utcnow().year % 4 == 0:
        embed.add_field(name='\u200b', value=f'And a very happy birthday to those celebrating a leap year birthday.',
                        inline=False)
    return embed

def get_months_old(month: int, year: int):
    current_month = nextcord.utils.utcnow().month
    current_year = nextcord.utils.utcnow().year
    months_old = (current_year - year) * 12 + (current_month - month)
    return months_old

def baby_month_milestone_message(birthday: Birthday, member: nextcord.Member):
    embed = nextcord.Embed(color=SUCCESS_COLOR, title='Someone is celebrating a milestone!')
    months = str(get_months_old(birthday.month, birthday.year)) + " month" + {True: "s", False: ""}[get_months_old(birthday.month, birthday.year) > 1]
    embed.add_field(name='\u200b', value=f'{member.mention} happy {months} to {birthday.name.title()}!', inline=False)
    return embed
