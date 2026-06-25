from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class ActivityModuleSettings(DB.Model):
    __tablename__ = 'activity_module_settings'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inactive_role_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    break_role_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    log_channel: Mapped[int] = mapped_column(BigInteger, nullable=True)
