from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class Birthday(DB.Model):
    __tablename__ = 'birthdays'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(primary_key=True)
    month: Mapped[int] = mapped_column()
    day: Mapped[int] = mapped_column()
    year: Mapped[int] = mapped_column()
