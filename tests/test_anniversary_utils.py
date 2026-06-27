"""Tests for the anniversary cog's pure presentation utils.

Everything under test takes plain values or a duck-typed ``entry`` (a
``SimpleNamespace`` satisfies the attribute access), so no live interaction, DB,
or gateway is needed. Covers parsing/validation, ordinals, count suppression, the
blank-default helpers, and the two embed builders (including the fresh-embed-per-
page guarantee the paginator relies on).
"""
from types import SimpleNamespace

import nextcord
import pytest

from bot.cogs.anniversary import anniversary_utils as au
from bot.utils import messages


def _entry(title=None, count_label=None, message=None, month=6, day=25,
           year=None, channel_id=42):
    """A duck-typed Anniversary entry covering the columns the utils read."""
    return SimpleNamespace(title=title, count_label=count_label, message=message,
                           month=month, day=day, year=year, channel_id=channel_id)


# --- parse_month_day --------------------------------------------------------


@pytest.mark.parametrize('raw,expected', [
    ('6/25', (6, 25)),
    ('06/25', (6, 25)),
    ('1/1', (1, 1)),
    ('12/31', (12, 31)),
    ('2/29', (2, 29)),        # leap day must be storable
    ('6-25', (6, 25)),        # '-' tolerated
    ('6.25', (6, 25)),        # '.' tolerated
    ('  6/25  ', (6, 25)),    # surrounding whitespace stripped
])
def test_parse_month_day_accepts_valid(raw, expected):
    assert au.parse_month_day(raw) == expected


@pytest.mark.parametrize('raw', [
    '13/1',      # month out of range
    '0/1',       # month out of range (low)
    '2/30',      # day out of range for February
    '6/31',      # June has 30 days
    '6/0',       # day out of range (low)
    'abc',       # junk, no separator
    '6/25/2020', # too many parts
    '6',         # missing day
    '',          # empty
    '/',         # no numbers
])
def test_parse_month_day_rejects_invalid(raw):
    with pytest.raises(ValueError):
        au.parse_month_day(raw)


def test_parse_month_day_none_raises():
    with pytest.raises(ValueError):
        au.parse_month_day(None)


# --- parse_year -------------------------------------------------------------


def test_parse_year_blank_and_none_are_none():
    assert au.parse_year('') is None
    assert au.parse_year('   ') is None
    assert au.parse_year(None) is None


def test_parse_year_valid_four_digit():
    assert au.parse_year('1990') == 1990
    assert au.parse_year('  2000  ') == 2000


def test_parse_year_two_digit_raises():
    with pytest.raises(ValueError):
        au.parse_year('19')


def test_parse_year_non_number_raises():
    with pytest.raises(ValueError):
        au.parse_year('nineteen')


def test_parse_year_future_raises():
    future = nextcord.utils.utcnow().year + 1
    with pytest.raises(ValueError):
        au.parse_year(str(future))


def test_parse_year_current_year_allowed():
    current = nextcord.utils.utcnow().year
    assert au.parse_year(str(current)) == current


# --- ordinal ----------------------------------------------------------------


@pytest.mark.parametrize('n,expected', [
    (1, '1st'), (2, '2nd'), (3, '3rd'), (4, '4th'),
    (11, '11th'), (12, '12th'), (13, '13th'),
    (21, '21st'), (22, '22nd'), (23, '23rd'),
    (101, '101st'), (111, '111th'),
])
def test_ordinal(n, expected):
    assert au.ordinal(n) == expected


# --- anniversary_count ------------------------------------------------------


@pytest.mark.parametrize('year,current,expected', [
    (None, 2026, None),   # no year recorded
    (2023, 2026, 3),      # normal positive count
    (2026, 2026, None),   # year-of suppresses 0th
    (2027, 2026, None),   # future year suppresses negative
])
def test_anniversary_count(year, current, expected):
    assert au.anniversary_count(year, current) == expected


# --- title_or_default / label_or_default ------------------------------------


@pytest.mark.parametrize('value', [None, '', '   '])
def test_title_or_default_blank(value):
    assert au.title_or_default(value) == 'Anniversary'


@pytest.mark.parametrize('value', [None, '', '   '])
def test_label_or_default_blank(value):
    assert au.label_or_default(value) == 'Anniversary'


def test_title_or_default_preserves_value():
    assert au.title_or_default('  Our Wedding  ') == 'Our Wedding'


def test_label_or_default_preserves_value():
    assert au.label_or_default('  Year  ') == 'Year'


# --- channel_display_name ---------------------------------------------------


def _guild(channels):
    """A fake guild whose ``get_channel`` resolves ids per the ``channels`` map and
    returns ``None`` for anything unmapped (a deleted/uncached channel)."""
    def get_channel(channel_id):
        name = channels.get(channel_id)
        return SimpleNamespace(name=name) if name is not None else None
    return SimpleNamespace(get_channel=get_channel)


def test_channel_display_name_resolved():
    assert au.channel_display_name(_guild({7: 'remembrances'}), 7) == '#remembrances'


def test_channel_display_name_unresolved_falls_back_to_id():
    # Channel deleted/uncached -> non-empty fallback (empty labels are rejected).
    assert au.channel_display_name(_guild({}), 7) == 'Channel 7'


def test_channel_display_name_none_guild_falls_back_to_id():
    # The list/upcoming call site passes guild=None; must not blow up.
    assert au.channel_display_name(None, 7) == 'Channel 7'


def test_channel_display_name_truncates_to_discord_label_cap():
    # A 100-char channel name yields a 101-char '#name'; capped to 100 with an ellipsis.
    label = au.channel_display_name(_guild({7: 'x' * 100}), 7)
    assert len(label) == 100
    assert label.endswith('…')


# --- build_pending_entry ----------------------------------------------------


def test_build_pending_entry_preserves_and_carries_values():
    pending = au.build_pending_entry(
        title='Our Wedding', count_label='Year', message='We remember you',
        month=6, day=25, year=2020, channel_id=99)
    assert pending.title == 'Our Wedding'
    assert pending.count_label == 'Year'
    assert pending.message == 'We remember you'
    assert (pending.month, pending.day, pending.year) == (6, 25, 2020)
    assert pending.channel_id == 99


@pytest.mark.parametrize('blank', [None, '', '   '])
def test_build_pending_entry_normalizes_blank_text_to_none(blank):
    # Blank/whitespace free-text collapses to None, exactly as add/update store it,
    # so the confirmed preview matches what gets persisted.
    pending = au.build_pending_entry(
        title=blank, count_label=blank, message=blank,
        month=2, day=29, year=None, channel_id=7)
    assert pending.title is None
    assert pending.count_label is None
    assert pending.message is None


def test_build_pending_entry_strips_surrounding_whitespace():
    pending = au.build_pending_entry(
        title='  Wedding  ', count_label='  Year  ', message='  hi  ',
        month=1, day=1, year=2000, channel_id=1)
    assert (pending.title, pending.count_label, pending.message) == (
        'Wedding', 'Year', 'hi')


def test_build_pending_entry_renders_through_post_embed():
    # The whole point of the pending entry: post_embed renders it identically to a
    # persisted row, so the confirm preview is faithful. Year drives the count line.
    pending = au.build_pending_entry(
        title='Our Wedding', count_label='Year', message='We remember you',
        month=6, day=25, year=2021, channel_id=42)
    embed = au.post_embed(pending, 2026)
    assert embed.title == 'Our Wedding'
    assert embed.description == '*We remember you*'
    assert embed.footer.text == '5th Year · June 25'


def test_build_pending_entry_blank_title_renders_default_in_preview():
    pending = au.build_pending_entry(
        title='   ', count_label=None, message=None,
        month=3, day=1, year=None, channel_id=5)
    embed = au.post_embed(pending, 2026)
    assert embed.title == 'Anniversary'
    assert embed.description is None
    assert embed.footer.text == 'March 1'


# --- post_embed -------------------------------------------------------------


def test_post_embed_uses_info_color():
    embed = au.post_embed(_entry(), 2026)
    assert embed.color.value == messages.INFO_COLOR


def test_post_embed_title_falls_back_to_default():
    assert au.post_embed(_entry(title='   '), 2026).title == 'Anniversary'


def test_post_embed_title_preserved():
    assert au.post_embed(_entry(title='Our Wedding'), 2026).title == 'Our Wedding'


def test_post_embed_message_rendered_italic():
    embed = au.post_embed(_entry(message='We remember you'), 2026)
    assert embed.description == '*We remember you*'


def test_post_embed_blank_message_omits_description():
    assert au.post_embed(_entry(message='   '), 2026).description is None
    assert au.post_embed(_entry(message=None), 2026).description is None


def test_post_embed_footer_without_year_is_month_day_only():
    embed = au.post_embed(_entry(month=6, day=25, year=None), 2026)
    assert embed.footer.text == 'June 25'


def test_post_embed_footer_with_year_shows_count_line():
    embed = au.post_embed(
        _entry(month=6, day=25, year=2023, count_label=None), 2026)
    assert embed.footer.text == '3rd Anniversary · June 25'


def test_post_embed_footer_uses_custom_count_label():
    embed = au.post_embed(
        _entry(month=6, day=25, year=2021, count_label='Year'), 2026)
    assert embed.footer.text == '5th Year · June 25'


def test_post_embed_year_of_suppresses_count():
    # current_year == year -> 0th suppressed, footer is just the date.
    embed = au.post_embed(_entry(month=6, day=25, year=2026), 2026)
    assert embed.footer.text == 'June 25'


def test_post_embed_has_no_gif_and_no_happy():
    # Tone guard: no image attached and the word "happy" appears nowhere.
    embed = au.post_embed(
        _entry(title='Anniversary', message='Thinking of you', year=2020), 2026)
    assert embed.image.url is None
    haystack = ' '.join(
        filter(None, [embed.title, embed.description,
                      embed.footer.text if embed.footer else None])).lower()
    assert 'happy' not in haystack


def test_post_embed_accepts_leap_day_entry():
    # Feb 29 must render even in a non-leap current year (footer is name + day).
    embed = au.post_embed(_entry(month=2, day=29, year=None), 2025)
    assert embed.footer.text == 'February 29'


# --- build_list_embeds ------------------------------------------------------


def test_build_list_embeds_single_page_line_format():
    entries = [_entry(title='Wedding', month=6, day=25, channel_id=10, year=2020)]
    embeds = au.build_list_embeds(entries, guild=None, title='Your anniversaries')
    assert len(embeds) == 1
    assert embeds[0].title == 'Your anniversaries'
    assert embeds[0].color.value == messages.INFO_COLOR
    assert embeds[0].description == 'Wedding · 06/25 · <#10> · 2020'


def test_build_list_embeds_omits_year_when_unset_and_defaults_title():
    entries = [_entry(title=None, month=3, day=1, channel_id=7, year=None)]
    line = au.build_list_embeds(entries, guild=None, title='X')[0].description
    assert line == 'Anniversary · 03/01 · <#7>'


def test_build_list_embeds_chunks_at_ten_per_page():
    entries = [_entry(channel_id=i) for i in range(au._LIST_ENTRIES_PER_PAGE + 3)]
    embeds = au.build_list_embeds(entries, guild=None, title='X')
    assert len(embeds) == 2
    assert len(embeds[0].description.splitlines()) == au._LIST_ENTRIES_PER_PAGE
    assert len(embeds[1].description.splitlines()) == 3


def test_build_list_embeds_empty():
    assert au.build_list_embeds([], guild=None, title='X') == []


def test_build_list_embeds_returns_fresh_embeds():
    # The paginator stamps each page's footer on access, so pages must not alias
    # one another — distinct object identities across all pages.
    entries = [_entry(channel_id=i) for i in range(au._LIST_ENTRIES_PER_PAGE * 2)]
    embeds = au.build_list_embeds(entries, guild=None, title='X')
    assert len(embeds) == 2
    assert embeds[0] is not embeds[1]
    assert len({id(e) for e in embeds}) == len(embeds)


# --- partition_postable -----------------------------------------------------


def _loop_entry(user_id, channel_id):
    """A duck-typed entry exposing just the two columns the partition reads."""
    return SimpleNamespace(user_id=user_id, channel_id=channel_id)


def test_partition_postable_registered_and_present_is_postable():
    entry = _loop_entry(user_id=1, channel_id=10)
    postable, skipped = au.partition_postable(
        [entry], registered_ids={10}, is_member=lambda uid: True)
    assert postable == [entry]
    assert skipped == []


def test_partition_postable_deregistered_channel_is_skipped():
    # Decision 9: the channel is no longer registered -> skip, even for a member.
    entry = _loop_entry(user_id=1, channel_id=99)
    postable, skipped = au.partition_postable(
        [entry], registered_ids={10}, is_member=lambda uid: True)
    assert postable == []
    assert skipped == [entry]


def test_partition_postable_absent_member_is_skipped():
    # Decision 10: the submitter has left -> skip, even on a registered channel.
    entry = _loop_entry(user_id=1, channel_id=10)
    postable, skipped = au.partition_postable(
        [entry], registered_ids={10}, is_member=lambda uid: False)
    assert postable == []
    assert skipped == [entry]


def test_partition_postable_is_member_keyed_by_user_id():
    present = _loop_entry(user_id=1, channel_id=10)
    departed = _loop_entry(user_id=2, channel_id=10)
    here = {1}
    postable, skipped = au.partition_postable(
        [present, departed], registered_ids={10}, is_member=lambda uid: uid in here)
    assert postable == [present]
    assert skipped == [departed]


def test_partition_postable_short_circuits_membership_for_deregistered():
    # A deregistered channel must skip WITHOUT consulting `is_member` — the loop
    # relies on this to avoid a REST member fetch for entries it won't post.
    calls = []

    def is_member(uid):
        calls.append(uid)
        return True

    entry = _loop_entry(user_id=1, channel_id=99)
    postable, skipped = au.partition_postable(
        [entry], registered_ids={10}, is_member=is_member)
    assert postable == []
    assert calls == []


def test_partition_postable_mixed_batch_preserves_order():
    a = _loop_entry(user_id=1, channel_id=10)   # postable
    b = _loop_entry(user_id=2, channel_id=99)   # deregistered -> skip
    c = _loop_entry(user_id=3, channel_id=10)   # absent member -> skip
    d = _loop_entry(user_id=4, channel_id=10)   # postable
    here = {1, 4}
    postable, skipped = au.partition_postable(
        [a, b, c, d], registered_ids={10}, is_member=lambda uid: uid in here)
    assert postable == [a, d]
    assert skipped == [b, c]


# --- post_embed (daily-post integration shape) ------------------------------


def test_post_embed_today_entry_with_year_shows_count_footer():
    # A today entry WITH a year: footer reads "{ordinal} {label} · {Month Day}".
    embed = au.post_embed(_entry(month=6, day=25, year=2023, count_label='Year'), 2026)
    assert embed.footer.text == '3rd Year · June 25'


def test_post_embed_today_entry_without_year_shows_date_only():
    # A today entry WITHOUT a year: footer is just the date, no count line.
    embed = au.post_embed(_entry(month=6, day=25, year=None), 2026)
    assert embed.footer.text == 'June 25'
