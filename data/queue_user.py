from __future__ import annotations

from datetime import datetime
from typing import overload

from bafser import Log, SqlAlchemyBase, get_datetime_now, get_db_session
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from data._tables import Tables
from data.user import User


class QueueUser(SqlAlchemyBase):
    __tablename__ = Tables.QueueUser

    queue_id: Mapped[int] = mapped_column(ForeignKey(f"{Tables.Queue}.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{Tables.User}.id"), primary_key=True)
    enter_date: Mapped[datetime]
    block: Mapped[int]
    priority: Mapped[int]

    user: Mapped[User] = relationship(init=False)

    def __repr__(self):
        return f"<QueueUser> qid={self.queue_id} uid={self.user_id}"

    @staticmethod
    def new(queue_id: int, user_id: int, block: int = 0, priority: int = 0, *, commit=True):
        now = get_datetime_now()
        qu = QueueUser(queue_id=queue_id, user_id=user_id, enter_date=now, block=block, priority=priority)
        Log.added(qu, now=now, commit=commit)
        return qu

    @staticmethod
    def _base_query(queue_id: int):
        return get_db_session().query(QueueUser).filter(QueueUser.queue_id == queue_id)

    @staticmethod
    def _base_query_sorted(queue_id: int):
        return QueueUser._base_query(queue_id).order_by(QueueUser.block, QueueUser.priority, QueueUser.enter_date)

    @staticmethod
    @overload
    def sorted(queue_users: None) -> None: ...
    @staticmethod
    @overload
    def sorted(queue_users: list[QueueUser]) -> list[QueueUser]: ...
    @staticmethod
    def sorted(queue_users: list[QueueUser] | None) -> list[QueueUser] | None:
        if queue_users is None:
            return None
        return sorted(queue_users, key=lambda user: (user.block, user.priority, user.enter_date))

    @staticmethod
    def get(queue_id: int, user_id: int):
        return QueueUser._base_query(queue_id).filter(QueueUser.user_id == user_id).first()

    @staticmethod
    def get_by_username(queue_id: int, username: str):
        username = username.removeprefix("@")
        return QueueUser._base_query(queue_id).join(User, User.id == QueueUser.user_id).filter(User.username == username).first()

    @staticmethod
    def get_by_user_id(queue_id: int, user_id: int):
        return QueueUser._base_query(queue_id).join(User, User.id == QueueUser.user_id).filter(User.id == user_id).first()

    @staticmethod
    def get_by_order(queue_id: int, order: int):
        return QueueUser._base_query_sorted(queue_id).offset(order).first()

    @staticmethod
    def all_in_queue(queue_id: int):
        return QueueUser._base_query_sorted(queue_id).all()

    @staticmethod
    def first2_in_queue(queue_id: int):
        r = QueueUser._base_query_sorted(queue_id).limit(2).all()
        if len(r) == 2:
            return r[0], r[1]
        if len(r) == 1:
            return r[0], None
        return None, None

    @staticmethod
    def count_in_queue(queue_id: int):
        return QueueUser._base_query(queue_id).count()

    @staticmethod
    def delete_all_in_queue(queue_id: int, *, commit=True):
        db_sess = get_db_session()
        qus = QueueUser.all_in_queue(queue_id)
        for qu in qus:
            qu.delete(commit=False)
        if commit:
            db_sess.commit()

    def delete(self, commit=True):
        self.db_sess.delete(self)
        Log.deleted(
            self,
            None,
            [
                ("queue_id", self.queue_id),
                ("user_id", self.user_id),
            ],
            commit=commit,
        )

    @staticmethod
    def swap_enter_date(qu1: QueueUser, qu2: QueueUser, *, commit=True):
        qu1.enter_date, qu2.enter_date = qu2.enter_date, qu1.enter_date
        Log.updated(qu1, commit=False)
        Log.updated(qu2, commit=commit)

    def update_enter_date(self, enter_date: datetime, *, commit=True):
        self.enter_date = enter_date
        Log.updated(self, commit=commit)

    def set_now_as_enter_date(self):
        self.enter_date = get_datetime_now()
        Log.updated(self)

    @staticmethod
    def update_block_for_all_in_queue(queue_id: int, block_count: int):
        block_count = max(block_count, 1)
        QueueUser._base_query(queue_id).filter(QueueUser.block >= block_count).update({QueueUser.block: block_count - 1})

    def update_block(self, block: int, *, commit=True):
        self.block = block
        Log.updated(self, commit=commit)

    @staticmethod
    def update_priority_for_all_in_queue(queue_id: int, max_priority: int):
        max_priority = max(max_priority, 1)
        QueueUser._base_query(queue_id).filter(QueueUser.priority >= max_priority).update({QueueUser.priority: max_priority - 1})

    def update_priority(self, priority: int, *, commit=True):
        self.priority = priority
        Log.updated(self, commit=commit)

    def update(self, block: int, priority: int, *, commit=True):
        self.block = block
        self.priority = priority
        self.enter_date = get_datetime_now()
        Log.updated(self, commit=commit)
