from sqlalchemy import BigInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class AnniversaryChannel(DB.Model):
    __tablename__ = 'anniversary_channel'
    __table_args__ = (UniqueConstraint('guild_id', 'channel_id'),)

    id: Mapped[int]         = mapped_column(primary_key=True)
    guild_id: Mapped[int]   = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
