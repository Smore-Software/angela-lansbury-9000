from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class StarboardEntry(DB.Model):
    __tablename__ = 'starboard_entry'

    id: Mapped[int] = mapped_column(primary_key=True)
    starboard_config_id: Mapped[int] = mapped_column(ForeignKey('starboard_config.id'), index=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    original_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    original_channel_id: Mapped[int] = mapped_column(BigInteger)
    posted_message_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    star_count: Mapped[int] = mapped_column(default=0)

    # Each board posts a given message at most once; makes concurrent inserts collide
    # deterministically. NO uniqueness on (guild_id, emoji) — fan-out is intentional.
    __table_args__ = (UniqueConstraint('starboard_config_id', 'original_message_id'),)
