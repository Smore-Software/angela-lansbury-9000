"""Tests for the anniversary add/edit modal's field configuration.

The modal's ``__init__`` only assembles ``TextInput`` rows — no gateway needed —
so the field attributes are checkable directly. These lock the crit-driven polish:
title + count-suffix are required and autofilled with "Anniversary", a prefill
(Edit re-open) still wins over that literal default, and the year placeholder
reads "YYYY (optional)". The submit/preview round-trip lives in
``tests/test_anniversary_preview_view.py``.
"""
from types import SimpleNamespace

from bot.cogs.anniversary.views.anniversary_modal import AnniversaryModal


async def test_title_required_and_autofilled_on_fresh_add():
    modal = AnniversaryModal(channel_id=1)
    assert modal.title_input.required is True
    assert modal.title_input.default_value == 'Anniversary'


async def test_count_suffix_required_autofilled_and_relabeled():
    modal = AnniversaryModal(channel_id=1)
    assert modal.label_input.label == 'Count suffix (e.g. "Nth [Suffix]")'
    assert modal.label_input.required is True
    assert modal.label_input.default_value == 'Anniversary'
    # Round-2 correction: max_length stays at 100, not tightened.
    assert modal.label_input.max_length == 100


async def test_year_placeholder_reads_optional():
    modal = AnniversaryModal(channel_id=1)
    assert modal.year_input.placeholder == 'YYYY (optional)'
    assert modal.year_input.required is False
    assert modal.year_input.max_length == 4


async def test_prefill_wins_over_literal_defaults():
    source = SimpleNamespace(
        title='Wedding', count_label='Year', message='hi',
        month=6, day=25, year=2020)
    modal = AnniversaryModal(channel_id=1, prefill=source)
    assert modal.title_input.default_value == 'Wedding'
    assert modal.label_input.default_value == 'Year'
