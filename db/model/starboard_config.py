from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class StarboardConfig(DB.Model):
    __tablename__ = 'starboard_config'

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(index=True)
    target_channel_id: Mapped[int] = mapped_column()
    emoji: Mapped[str] = mapped_column()                  # unicode char OR custom emoji name
    emoji_id: Mapped[int] = mapped_column(nullable=True)  # set only for custom emoji
    threshold: Mapped[int] = mapped_column(default=5)
    enabled: Mapped[bool] = mapped_column(default=True)
    name: Mapped[str] = mapped_column(nullable=True)      # friendly label for list/autocomplete
