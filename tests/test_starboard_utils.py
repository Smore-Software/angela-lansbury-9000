"""Tests for the pure starboard helpers: ``reaction_count`` (count extraction)
and ``messages.starboard_embed`` (the repost embed). No mocks, no I/O."""
import datetime
from types import SimpleNamespace

from bot.cogs.starboard.starboard_utils import reaction_count
from bot.utils import messages
from db.model.starboard_config import StarboardConfig


def _reactions(*pairs):
    return [SimpleNamespace(emoji=emoji, count=count) for emoji, count in pairs]


def _message(reactions=(), content='hello', attachments=(), jump_url='http://jump'):
    return SimpleNamespace(
        reactions=list(reactions),
        content=content,
        attachments=list(attachments),
        embeds=[],
        jump_url=jump_url,
        created_at=datetime.datetime(2026, 6, 22, tzinfo=datetime.timezone.utc),
        author=SimpleNamespace(display_name='Bob',
                               display_avatar=SimpleNamespace(url='http://avatar')),
    )


def _attachment(content_type, url, filename='file'):
    return SimpleNamespace(content_type=content_type, url=url, filename=filename)


# --- reaction_count ---------------------------------------------------------


def test_reaction_count_returns_matching_count(emoji_factory):
    cfg = StarboardConfig(emoji='⭐', emoji_id=None)
    msg = _message(reactions=_reactions((emoji_factory('⭐'), 7)))
    assert reaction_count(msg, cfg) == 7


def test_reaction_count_zero_when_emoji_absent(emoji_factory):
    cfg = StarboardConfig(emoji='⭐', emoji_id=None)
    msg = _message(reactions=_reactions((emoji_factory('🔥'), 9)))
    assert reaction_count(msg, cfg) == 0


def test_reaction_count_picks_correct_among_multiple(emoji_factory):
    cfg = StarboardConfig(emoji='⭐', emoji_id=None)
    msg = _message(reactions=_reactions(
        (emoji_factory('🔥'), 3),
        (emoji_factory('⭐'), 5),
        (emoji_factory('👍'), 8),
    ))
    assert reaction_count(msg, cfg) == 5


def test_reaction_count_matches_custom_emoji_by_id(emoji_factory):
    cfg = StarboardConfig(emoji='blob', emoji_id=12345)
    msg = _message(reactions=_reactions((emoji_factory('renamed', id=12345), 4)))
    assert reaction_count(msg, cfg) == 4


# --- starboard_embed --------------------------------------------------------


def test_starboard_embed_core_fields():
    msg = _message(content='something memorable')
    embed = messages.starboard_embed(msg, 'general')
    assert embed.author.name == 'Bob'
    assert embed.author.icon_url == 'http://avatar'
    # The description is now just the original content — emoji, count, and the
    # source back-link all moved to the message content.
    assert embed.description == 'something memorable'
    # The footer is just the source channel.
    assert embed.footer.text == '#general'


def test_starboard_content_renders_emoji_count_and_source():
    # Everything that must render as a real emoji or link lives in the message
    # content: `EMOJI **(COUNT)** | [Source ↗](url)`.
    assert messages.starboard_content('⭐', 6, 'http://jump') == \
        '⭐ **× 6** · [Source ↗](http://jump)'
    assert messages.starboard_content('<:blob:12345>', 5, 'http://j') == \
        '<:blob:12345> **× 5** · [Source ↗](http://j)'


def test_starboard_embed_sets_image_when_image_attachment_present():
    img = _attachment('image/png', 'http://img.png')
    msg = _message(attachments=[img])
    embed = messages.starboard_embed(msg, 'general')
    assert embed.image.url == 'http://img.png'


def test_starboard_embed_no_image_when_no_image_attachment():
    msg = _message(attachments=[])
    embed = messages.starboard_embed(msg, 'general')
    assert embed.image.url is None


def test_starboard_embed_lists_non_image_attachments_as_links():
    img = _attachment('image/png', 'http://img.png', filename='pic.png')
    doc = _attachment('application/pdf', 'http://doc.pdf', filename='spec.pdf')
    msg = _message(attachments=[img, doc])
    embed = messages.starboard_embed(msg, 'general')
    # Image inlined; the pdf surfaces as a link, not the inlined image.
    assert embed.image.url == 'http://img.png'
    attachment_fields = [f.value for f in embed.fields if f.name == 'Attachments']
    assert len(attachment_fields) == 1
    assert 'http://doc.pdf' in attachment_fields[0]
    assert 'http://img.png' not in attachment_fields[0]


def test_starboard_embed_handles_empty_content_media_only():
    img = _attachment('image/jpeg', 'http://img.jpg')
    msg = _message(content='', attachments=[img])
    embed = messages.starboard_embed(msg, 'memes')
    # No crash on empty content; image still inlined.
    assert embed.image.url == 'http://img.jpg'
    assert embed.footer.text == '#memes'
    # Empty content yields no description (the source link lives in the message
    # content now, not the embed).
    assert embed.description is None


def test_starboard_embed_tolerates_attachment_with_no_content_type():
    # Discord attachments can carry content_type=None; message_has_image must not
    # raise on them, and such an attachment is treated as non-image (linked, not inlined).
    untyped = _attachment(None, 'http://file.bin', filename='blob.bin')
    msg = _message(content='see attached', attachments=[untyped])
    embed = messages.starboard_embed(msg, 'general')
    assert embed.image.url is None
    attachment_fields = [f.value for f in embed.fields if f.name == 'Attachments']
    assert len(attachment_fields) == 1
    assert 'http://file.bin' in attachment_fields[0]
