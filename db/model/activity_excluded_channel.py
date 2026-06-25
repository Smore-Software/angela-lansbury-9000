from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class ActivityExcludedChannel(DB.Model):
    __tablename__ = 'activity_excluded_channel'

    # A row exists iff that channel is excluded from activity tracking for that guild.
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
