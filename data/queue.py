from datetime import datetime

import bafser_tgapi as tgapi
from bafser import Log, ObjMixin, SqlAlchemyBase
from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from data._tables import Tables
from data.msg import Msg


class Queue(SqlAlchemyBase, ObjMixin):
    __tablename__ = Tables.Queue

    msg_id: Mapped[int] = mapped_column(ForeignKey(f"{Tables.Msg}.id"), index=True)
    msg_next_id: Mapped[int | None] = mapped_column(ForeignKey(f"{Tables.Msg}.id"), init=False, index=True)
    name: Mapped[str] = mapped_column(String(128))
    clear_at: Mapped[int | None] = mapped_column(default=None)
    cleared_at: Mapped[datetime | None] = mapped_column(default=None)
    max_in_block: Mapped[int] = mapped_column(default=0, server_default='0')
    blocks: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    priorities: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    msg: Mapped[Msg] = relationship(foreign_keys=[msg_id], init=False)
    msg_next: Mapped[Msg | None] = relationship(foreign_keys=[msg_next_id], init=False)

    @staticmethod
    def new(msg_id: int, name: str):
        queue = Queue(msg_id=msg_id, name=name)
        Log.added(queue)
        return queue

    @staticmethod
    def new_by_message(message: tgapi.Message, name: str):
        msg = Msg.new_from_data(message)
        return Queue.new(msg.id, name)

    @staticmethod
    def get_by_message(message: tgapi.Message):
        return (
            Queue.query()
            .join(Msg, (Msg.id == Queue.msg_id) | (Msg.id == Queue.msg_next_id))
            .where(Msg.message_id == message.message_id, Msg.chat_id == message.chat.id)
            .first()
        )

    def update_name(self, name: str, *, commit=True):
        self.name = name
        Log.updated(self, commit=commit)

    def update_msg(self, message: tgapi.Message, *, commit=True):
        new_msg = Msg.new_from_data(message, db_sess=self.db_sess)
        self.msg.delete(commit=False, db_sess=self.db_sess)
        self.msg = new_msg
        Log.updated(self, commit=commit)

    def update_msg_next(self, message: tgapi.Message | None, *, commit=True):
        msg_next = None if message is None else Msg.new_from_data(message, db_sess=self.db_sess)
        if self.msg_next:
            self.msg_next.delete(commit=False, db_sess=self.db_sess)
        self.msg_next = msg_next
        Log.updated(self, commit=commit)

    def update_clear_at(self, clear_at: tuple[int, int, int] | None, *, commit=True):
        if not clear_at:
            self.clear_at = None
        else:
            day, hour, minute = clear_at
            self.clear_at = day * 10000 + hour * 100 + minute
        Log.updated(self, commit=commit)

    def get_parsed_clear_at(self):
        if not self.clear_at:
            return None
        dayI = self.clear_at // 10000 % 10
        hour = self.clear_at // 100 % 100
        minute = self.clear_at % 100
        if not (1 <= dayI <= 7):
            return None

        day = [
            ("Понедельник", "Пн"),
            ("Вторник", "Вт"),
            ("Среда", "Ср"),
            ("Четверг", "Чт"),
            ("Пятница", "Пт"),
            ("Суббота", "Сб"),
            ("Воскресенье", "Вс"),
        ][dayI - 1]
        return day, dayI, hour, minute

    def update_max_in_block(self, max_in_block: int, *, commit=True):
        self.max_in_block = max_in_block
        Log.updated(self, commit=commit)

    def update_blocks(self, blocks: list[str], *, commit=True):
        self.blocks = blocks
        Log.updated(self, commit=commit)

    def update_priorities(self, priorities: list[str], *, commit=True):
        self.priorities = priorities
        Log.updated(self, commit=commit)
