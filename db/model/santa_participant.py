from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class SantaParticipant(DB.Model):
    __tablename__ = 'santa_participant'

    santa_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recipient_id: Mapped[int] = mapped_column(BigInteger)
    has_shipped: Mapped[bool] = mapped_column(default=False)
