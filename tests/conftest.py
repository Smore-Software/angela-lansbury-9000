"""Shared pytest fixtures for the Angela Lansbury 9000 test suite.

IMPORTANT — import order: ``DATABASE_URL`` MUST be set before ``db`` is imported
anywhere, otherwise the DB singleton in ``db/model/__init__.py`` binds to the
real ``bumper-db.sqlite``. pytest imports conftest before collecting any test
module, so setting the env var at module level here (above ``import db``)
guarantees every test runs against a throwaway sqlite file.
"""
import os
import tempfile

# Set the temp DB URL at the VERY TOP, before importing db. ``setdefault`` lets a
# caller override it (e.g. CI) without us clobbering their choice. Close the fd
# mkstemp opens — we only want the path; sqlalchemy opens its own connection.
_db_fd, _DB_PATH = tempfile.mkstemp(suffix='.sqlite')
os.close(_db_fd)
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_DB_PATH}')

from types import SimpleNamespace

import pytest

import db  # noqa: F401,E402  imports DB + every registered model via db/__init__.py
from db import DB  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def _schema():
    """Build the whole schema once against the temp engine, drop it at the end,
    then unlink the temp DB file so the run leaves nothing behind."""
    DB.create_all()
    yield
    DB.drop_all()
    if os.path.exists(_DB_PATH):
        os.unlink(_DB_PATH)


@pytest.fixture(autouse=True)
def db_tx():
    """Wrap every test in a savepoint transaction that rolls back on exit, so
    tests never leak rows into one another (or the temp file beyond the run)."""
    tx = DB.test_transaction(savepoint=True)
    yield
    tx.close()


# --- Discord fake factories -------------------------------------------------
# Minimal stand-ins shaped like the nextcord objects the production code reads.
# Kept here because P1/P2/P3 tests reuse them. Each exposes only the attributes
# the bot actually touches.


class _FakeEmoji:
    """Shaped like ``nextcord.PartialEmoji``.

    ``str()`` renders the unicode char when ``id is None``, else ``<:name:id>``
    (or ``<a:name:id>`` for animated custom emoji), matching nextcord's
    ``PartialEmoji.__str__``. ``is_custom_emoji()`` is ``id is not None``.
    """

    def __init__(self, name, id=None, animated=False):
        self.name = name
        self.id = id
        self.animated = animated

    def is_custom_emoji(self):
        return self.id is not None

    def is_unicode_emoji(self):
        return self.id is None

    def __str__(self):
        if self.id is None:
            return self.name
        prefix = 'a' if self.animated else ''
        return f'<{prefix}:{self.name}:{self.id}>'

    def __repr__(self):
        return f'_FakeEmoji({self!s})'


def make_emoji(name, id=None, animated=False):
    """Build a fake ``PartialEmoji``. Pass ``id`` for a custom emoji."""
    return _FakeEmoji(name, id=id, animated=animated)


def make_payload(emoji=None, channel_id=None, message_id=None, guild_id=None,
                 user_id=None, member=None, event_type='REACTION_ADD'):
    """Build a fake ``nextcord.RawReactionActionEvent``."""
    return SimpleNamespace(
        emoji=emoji,
        channel_id=channel_id,
        message_id=message_id,
        guild_id=guild_id,
        user_id=user_id,
        member=member,
        event_type=event_type,
    )


def make_message(reactions=None, content='', attachments=None, author=None,
                 id=None, channel=None, guild=None, jump_url=None):
    """Build a fake ``nextcord.Message``.

    ``reactions`` is a list of objects with ``.emoji`` and ``.count``; pass
    ``(emoji, count)`` tuples for convenience and they get wrapped.
    """
    wrapped = []
    for r in (reactions or []):
        if isinstance(r, tuple):
            emoji, count = r
            wrapped.append(SimpleNamespace(emoji=emoji, count=count))
        else:
            wrapped.append(r)
    return SimpleNamespace(
        reactions=wrapped,
        content=content,
        attachments=attachments or [],
        author=author,
        id=id,
        channel=channel,
        guild=guild,
        jump_url=jump_url,
    )


@pytest.fixture
def emoji_factory():
    return make_emoji


@pytest.fixture
def payload_factory():
    return make_payload


@pytest.fixture
def message_factory():
    return make_message
