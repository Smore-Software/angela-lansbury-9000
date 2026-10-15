from datetime import datetime, timezone
from typing import List

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.model import DB


class ThreadMap(DB.Model):
    __tablename__ = 'thread_map'

    id: Mapped[int]                     = mapped_column(primary_key=True)
    guild_id: Mapped[int]               = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int]             = mapped_column(BigInteger)
    message_id: Mapped[int]             = mapped_column(BigInteger, unique=True, index=True)
    created_by: Mapped[int]             = mapped_column(BigInteger)
    current_page: Mapped[int]           = mapped_column(default=0)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Python-side default: a server_default of now() autogenerates fine on
    # Postgres but sqlite rejects it at insert time.
    created_at: Mapped[datetime]        = mapped_column(DateTime(timezone=True),
                                                        default=lambda: datetime.now(timezone.utc))

    roles: Mapped[List['ThreadMapRole']] = relationship(cascade='all, delete-orphan')
    excluded: Mapped[List['ThreadMapExcludedChannel']] = relationship(cascade='all, delete-orphan')
    pages: Mapped[List['ThreadMapPage']] = relationship(cascade='all, delete-orphan',
                                                        order_by='ThreadMapPage.page_index')


class ThreadMapRole(DB.Model):
    __tablename__ = 'thread_map_role'

    thread_map_id: Mapped[int] = mapped_column(ForeignKey('thread_map.id'), primary_key=True)
    role_id: Mapped[int]       = mapped_column(BigInteger, primary_key=True)


class ThreadMapExcludedChannel(DB.Model):
    __tablename__ = 'thread_map_excluded_channel'

    thread_map_id: Mapped[int] = mapped_column(ForeignKey('thread_map.id'), primary_key=True)
    channel_id: Mapped[int]    = mapped_column(BigInteger, primary_key=True)


class ThreadMapPage(DB.Model):
    __tablename__ = 'thread_map_page'

    thread_map_id: Mapped[int] = mapped_column(ForeignKey('thread_map.id'), primary_key=True)
    page_index: Mapped[int]    = mapped_column(primary_key=True)
    content: Mapped[str]       = mapped_column(Text)
