from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db import DB


class UserSettings(DB.Model):
    __tablename__ = 'user_settings'

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    image_deletion_prompts_enabled: Mapped[bool] = mapped_column(default=True)
