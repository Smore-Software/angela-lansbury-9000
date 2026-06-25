from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class ActivityModuleSettings(DB.Model):
    __tablename__ = 'activity_module_settings'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    excluded_channels: Mapped[str] = mapped_column(nullable=True)
    inactive_role_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    break_role_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    log_channel: Mapped[int] = mapped_column(BigInteger, nullable=True)

    @property
    def excluded_channels_set(self):
        excluded_channels = self.excluded_channels.split(',')
        excluded_channels = [int(c) for c in excluded_channels]
        return set(excluded_channels) if self.excluded_channels else set()
