from datetime import datetime

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class ImageMessageToDelete(DB.Model):
    __tablename__ = 'image_message_to_delete'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    delete_after: Mapped[datetime] = mapped_column()
    author_id: Mapped[int] = mapped_column(BigInteger)
    has_failed: Mapped[bool] = mapped_column(nullable=True)

    def __repr__(self):
        return f'G: {self.guild_id} | C: {self.channel_id} | M: {self.message_id}'
