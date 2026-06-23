from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class StarboardEntry(DB.Model):
    __tablename__ = 'starboard_entry'

    id: Mapped[int] = mapped_column(primary_key=True)
    starboard_config_id: Mapped[int] = mapped_column(ForeignKey('starboard_config.id'), index=True)
    guild_id: Mapped[int] = mapped_column(index=True)
    original_message_id: Mapped[int] = mapped_column(index=True)
    original_channel_id: Mapped[int] = mapped_column()
    posted_message_id: Mapped[int] = mapped_column(nullable=True)
    author_id: Mapped[int] = mapped_column()
    star_count: Mapped[int] = mapped_column(default=0)

    # Each board posts a given message at most once; makes concurrent inserts collide
    # deterministically. NO uniqueness on (guild_id, emoji) — fan-out is intentional.
    __table_args__ = (UniqueConstraint('starboard_config_id', 'original_message_id'),)
