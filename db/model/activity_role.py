import nextcord
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class ActivityRole(DB.Model):
    __tablename__ = 'activity_role'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_name: Mapped[str] = mapped_column(nullable=True)
    min: Mapped[int] = mapped_column(nullable=True)
    max: Mapped[int] = mapped_column(nullable=True)
    should_notify: Mapped[bool] = mapped_column(default=False)
    rolling_month_window: Mapped[int] = mapped_column(default=1)
    grace_period_months: Mapped[int] = mapped_column(nullable=True)

    role = None

    @property
    def errors(self):
        errors = []
        if self.min is None and self.max is None:
            errors.append('You must specify a minimum or maximum message count or both.')
        if self.min is not None and self.max is not None and self.min > self.max:
            errors.append('Minimum message count must be less than maximum message count.')
        if self.rolling_month_window is not None and self.rolling_month_window < 1:
            errors.append('Rolling month window must be at least 1.')
        if self.grace_period_months is not None and self.grace_period_months < 0:
            errors.append('Grace period months must be at least 0.')
        return errors
