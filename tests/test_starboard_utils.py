"""Tests for the pure starboard helpers: ``reaction_count`` (count extraction),
``member_has_bypass_role`` (the bypass predicate) and the ``messages`` builders
for the repost embed and content line. No mocks, no I/O."""
import datetime
from types import SimpleNamespace

from bot.cogs.starboard.starboard_utils import member_has_bypass_role, reaction_count
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


# --- member_has_bypass_role -------------------------------------------------


def _member(*role_ids):
    return SimpleNamespace(id=42, roles=[SimpleNamespace(id=rid) for rid in role_ids])


def test_member_has_bypass_role_false_when_board_has_no_bypass_role():
    # The default board: no bypass configured, so no reactor can ever bypass it.
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=None)
    assert member_has_bypass_role(_member(77), cfg) is False


def test_member_has_bypass_role_false_when_no_bypass_role_and_role_id_unreadable():
    # Causally covers the `bypass_role_id is None` guard. Without it the comparison
    # becomes `getattr(role, 'id', None) == None`, which a role we cannot read an id
    # off would satisfy — turning an unconfigured board into one everybody bypasses.
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=None)
    assert member_has_bypass_role(
        SimpleNamespace(id=42, roles=[SimpleNamespace()]), cfg) is False


def test_member_has_bypass_role_false_when_member_missing():
    # The reaction-remove path always passes ``payload.member is None``.
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=77)
    assert member_has_bypass_role(None, cfg) is False


def test_member_has_bypass_role_false_when_member_holds_other_roles():
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=77)
    assert member_has_bypass_role(_member(1, 2, 3), cfg) is False


def test_member_has_bypass_role_true_when_role_among_several():
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=77)
    assert member_has_bypass_role(_member(1, 77, 3), cfg) is True


def test_member_has_bypass_role_false_when_member_has_no_roles():
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=77)
    assert member_has_bypass_role(_member(), cfg) is False


def test_member_has_bypass_role_false_when_member_lacks_roles_attribute():
    # Existing handler-test fakes are bare ``SimpleNamespace(id=...)`` with no
    # ``roles`` at all; the predicate must tolerate that rather than raise.
    cfg = StarboardConfig(emoji='⭐', bypass_role_id=77)
    assert member_has_bypass_role(SimpleNamespace(id=42), cfg) is False


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


def test_starboard_content_appends_bypass_note_as_subtext():
    # `-#` is Discord subtext markdown; it renders small and muted directly under
    # the star line, and only works in message content (never in an embed).
    assert messages.starboard_content('⭐', 1, 'http://jump', bypass_note='why it is here') == \
        '⭐ **× 1** · [Source ↗](http://jump)\n-# why it is here'


def test_starboard_content_without_note_is_byte_identical_to_single_line():
    # Backward compatibility is a hard requirement: an ordinary post's content must
    # not gain so much as a trailing newline from the new parameter.
    plain = messages.starboard_content('⭐', 6, 'http://jump')
    assert plain == messages.starboard_content('⭐', 6, 'http://jump', bypass_note=None)
    assert plain == messages.starboard_content('⭐', 6, 'http://jump', bypass_note='')
    assert '\n' not in plain


def test_starboard_bypass_note_names_the_role():
    assert messages.starboard_bypass_note('Moderator', 5) == \
        'Someone with the **Moderator** role bypassed the 5-reaction threshold.'


def test_starboard_bypass_note_falls_back_when_role_unresolvable():
    # A deleted role (or a cold guild cache) must not render the word `None`.
    note = messages.starboard_bypass_note(None, 5)
    assert note == 'Someone with a privileged role bypassed the 5-reaction threshold.'
    assert 'None' not in note


def test_starboard_bypass_note_uses_the_boards_own_threshold():
    assert messages.starboard_bypass_note('Mod', 12) == \
        'Someone with the **Mod** role bypassed the 12-reaction threshold.'


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
