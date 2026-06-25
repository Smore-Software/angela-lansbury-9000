import enum

from sqlalchemy import BigInteger, Enum, Integer
from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class AutoDeleteType(enum.Enum):
    @classmethod
    def list(cls):
        return list(map(lambda c: str(c.value), cls))

    all = 'all'
    photo = 'photo'
    text = 'text'


class AutoDeleteChannelConfig(DB.Model):
    __tablename__ = 'auto_delete_channel_config'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    auto_delete_type: Mapped[Enum] = mapped_column(Enum(AutoDeleteType))
    delete_after_minutes: Mapped[int] = mapped_column(Integer())
    anchor_message: Mapped[int] = mapped_column(BigInteger)
    original_anchor_message: Mapped[int] = mapped_column(BigInteger, nullable=True)
