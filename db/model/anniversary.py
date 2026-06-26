from datetime import datetime

from sqlalchemy import BigInteger, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.model import DB


class Anniversary(DB.Model):
    __tablename__ = 'anniversary'
    __table_args__ = (UniqueConstraint('guild_id', 'user_id', 'title', 'month', 'day'),)

    id: Mapped[int]          = mapped_column(primary_key=True)
    guild_id: Mapped[int]    = mapped_column(BigInteger, index=True)
    user_id: Mapped[int]     = mapped_column(BigInteger, index=True)   # submitter = owner = mentioned
    channel_id: Mapped[int]  = mapped_column(BigInteger)              # chosen registered channel
    title: Mapped[str]       = mapped_column(nullable=True)   # heading; render default "Anniversary"
    count_label: Mapped[str] = mapped_column(nullable=True)   # word after ordinal; default "Anniversary"
    message: Mapped[str]     = mapped_column(nullable=True)   # optional body
    month: Mapped[int]       = mapped_column()
    day: Mapped[int]         = mapped_column()
    year: Mapped[int]        = mapped_column(nullable=True)   # optional; drives Nth count
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
