"""Phase 2 — BigInteger correctness.

Every Discord snowflake column must be declared with SQLAlchemy ``BigInteger``.
On SQLite this is a runtime no-op (bare ints already store 64-bit values), so
these assertions are the only thing that catches a regression before the schema
is ever built on Postgres — where a bare ``Mapped[int]`` becomes 32-bit
``INTEGER`` and overflows on snowflakes (~1e18).

The snowflake inventory below is exhaustive and intentionally explicit: it is
the contract. Columns deliberately left as plain ``Integer`` (surrogate keys,
counts, date parts, durations) are NOT listed here.
"""
from datetime import datetime

from sqlalchemy import BigInteger

from db.model.activity_module_settings import ActivityModuleSettings
from db.model.activity_role import ActivityRole
from db.model.auto_delete_channel_config import AutoDeleteChannelConfig
from db.model.birthday import Birthday
from db.model.guild_config import GuildConfig
from db.model.image_message_to_delete import ImageMessageToDelete
from db.model.polls import PollQuestion, PollResponse
from db.model.rolling_message_log import RollingMessageLog
from db.model.santa_participant import SantaParticipant
from db.model.starboard_config import StarboardConfig
from db.model.starboard_entry import StarboardEntry
from db.model.user_activity import UserActivity
from db.model.user_channel_settings import UserChannelSettings
from db.model.user_settings import UserSettings

import pytest

# (Model, column_name) for every Discord-snowflake column across the schema.
SNOWFLAKE_COLUMNS = [
    (UserSettings, 'guild_id'),
    (UserSettings, 'user_id'),

    (ImageMessageToDelete, 'guild_id'),
    (ImageMessageToDelete, 'channel_id'),
    (ImageMessageToDelete, 'message_id'),
    (ImageMessageToDelete, 'author_id'),

    (ActivityRole, 'guild_id'),
    (ActivityRole, 'role_id'),

    (GuildConfig, 'guild_id'),
    (GuildConfig, 'birthday_channel_id'),
    (GuildConfig, 'baby_month_milestone_channel_id'),

    (StarboardEntry, 'guild_id'),
    (StarboardEntry, 'original_message_id'),
    (StarboardEntry, 'original_channel_id'),
    (StarboardEntry, 'posted_message_id'),
    (StarboardEntry, 'author_id'),

    (StarboardConfig, 'guild_id'),
    (StarboardConfig, 'target_channel_id'),
    (StarboardConfig, 'emoji_id'),

    (UserChannelSettings, 'guild_id'),
    (UserChannelSettings, 'channel_id'),
    (UserChannelSettings, 'user_id'),

    (AutoDeleteChannelConfig, 'guild_id'),
    (AutoDeleteChannelConfig, 'channel_id'),
    (AutoDeleteChannelConfig, 'anchor_message'),
    (AutoDeleteChannelConfig, 'original_anchor_message'),

    (ActivityModuleSettings, 'guild_id'),
    (ActivityModuleSettings, 'inactive_role_id'),
    (ActivityModuleSettings, 'break_role_id'),
    (ActivityModuleSettings, 'log_channel'),

    (SantaParticipant, 'santa_id'),
    (SantaParticipant, 'recipient_id'),

    (Birthday, 'guild_id'),
    (Birthday, 'user_id'),

    (PollQuestion, 'message_id'),
    (PollQuestion, 'guild_id'),
    (PollQuestion, 'channel_id'),
    (PollQuestion, 'author_id'),

    (PollResponse, 'respondent_id'),

    (UserActivity, 'guild_id'),
    (UserActivity, 'user_id'),

    (RollingMessageLog, 'message_id'),
    (RollingMessageLog, 'guild_id'),
    (RollingMessageLog, 'author_id'),
]


@pytest.mark.parametrize(
    'model, column',
    SNOWFLAKE_COLUMNS,
    ids=[f'{m.__name__}.{c}' for m, c in SNOWFLAKE_COLUMNS],
)
def test_snowflake_column_is_big_integer(model, column):
    col_type = model.__table__.c[column].type
    assert isinstance(col_type, BigInteger), (
        f'{model.__name__}.{column} is {col_type!r}, expected BigInteger — '
        f'a bare INTEGER overflows on Discord snowflakes when run on Postgres.'
    )


# A realistic Discord snowflake: larger than the 32-bit INTEGER ceiling
# (2_147_483_647) so a regression to plain Integer would fail on Postgres.
REALISTIC_SNOWFLAKE = 112233445566778899


def test_snowflake_round_trips_unchanged():
    from db import DB

    DB.s.add(RollingMessageLog(
        message_id=REALISTIC_SNOWFLAKE,
        guild_id=REALISTIC_SNOWFLAKE + 1,
        author_id=REALISTIC_SNOWFLAKE + 2,
        sent_at=datetime(2026, 6, 25, 12, 0, 0),
    ))
    DB.s.commit()

    row = DB.s.first(RollingMessageLog, message_id=REALISTIC_SNOWFLAKE)
    assert row is not None
    assert row.message_id == REALISTIC_SNOWFLAKE
    assert row.guild_id == REALISTIC_SNOWFLAKE + 1
    assert row.author_id == REALISTIC_SNOWFLAKE + 2
