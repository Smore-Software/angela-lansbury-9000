from datetime import datetime

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB

import datetime as dt


# noinspection PyTypeChecker
class UserActivity(DB.Model):
    __tablename__ = 'user_activity'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tracking_started_on: Mapped[datetime] = mapped_column()
    is_active: Mapped[bool] = mapped_column(default=True)
    sent_thirty_day_notice: Mapped[bool] = mapped_column(default=False)
    sent_sixty_day_notice: Mapped[bool] = mapped_column(default=False)

    def reset(self):
        self.tracking_started_on = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        self.is_active = True
        self.sent_thirty_day_notice = False
        self.sent_sixty_day_notice = False
