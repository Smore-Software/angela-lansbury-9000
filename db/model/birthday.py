from sqlalchemy import BigInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class Birthday(DB.Model):
    __tablename__ = 'birthdays'
    __table_args__ = (UniqueConstraint('guild_id', 'user_id', 'name'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column()
    month: Mapped[int] = mapped_column()
    day: Mapped[int] = mapped_column()
    year: Mapped[int] = mapped_column()
