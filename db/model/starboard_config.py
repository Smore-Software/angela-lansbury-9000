from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class StarboardConfig(DB.Model):
    __tablename__ = 'starboard_config'

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_channel_id: Mapped[int] = mapped_column(BigInteger)
    emoji: Mapped[str] = mapped_column()                  # unicode char OR custom emoji name
    emoji_id: Mapped[int] = mapped_column(BigInteger, nullable=True)  # set only for custom emoji
    threshold: Mapped[int] = mapped_column(default=5)
    enabled: Mapped[bool] = mapped_column(default=True)
