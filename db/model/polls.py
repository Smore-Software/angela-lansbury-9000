from datetime import datetime
from typing import List

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import DB


class PollQuestion(DB.Model):
    __tablename__ = 'poll_question'

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column()
    is_anonymous: Mapped[bool] = mapped_column(default=False)
    is_multi_choice: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=True)
    is_open: Mapped[bool] = mapped_column(default=True)

    choices: Mapped[List['PollChoice']] = relationship('PollChoice', back_populates='question', cascade='all, delete')

    @property
    def custom_id(self):
        return f'poll_question:{self.message_id}'

    def validate(self):
        if not self.text:
            raise ValueError('Poll question text cannot be empty')
        if len(self.text) > 1500:
            raise ValueError('Poll question text cannot be longer than 1500 characters')


class PollChoice(DB.Model):
    __tablename__ = 'poll_choice'

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey('poll_question.id'))
    text: Mapped[str] = mapped_column()
    ordinal: Mapped[int] = mapped_column()

    responses: Mapped[List['PollResponse']] = relationship('PollResponse', back_populates='choice', cascade='all, delete')
    question: Mapped['PollQuestion'] = relationship('PollQuestion', back_populates='choices')

    @property
    def custom_id(self):
        return f'poll_choice:{self.id}'

    def validate(self):
        if not self.text:
            raise ValueError('Choice text cannot be empty')
        if len(self.text) > 50:
            raise ValueError('Choice text cannot be longer than 50 characters')


class PollResponse(DB.Model):
    __tablename__ = 'poll_response'

    choice_id: Mapped[int] = mapped_column(ForeignKey('poll_choice.id'), primary_key=True)
    respondent_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    choice: Mapped['PollChoice'] = relationship('PollChoice', back_populates='responses')
