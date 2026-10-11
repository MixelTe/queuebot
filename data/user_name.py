from bafser import Log, SqlAlchemyBase, get_db_session
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from data import Tables


class UserName(SqlAlchemyBase):
    __tablename__ = Tables.UserName

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{Tables.User}.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))

    @classmethod
    def get(cls, chat_id: int, user_id: int, *, db_sess=None):
        session = db_sess if db_sess is not None else get_db_session()
        return session.get(cls, (chat_id, user_id))

    @classmethod
    def set(cls, chat_id: int, user_id: int, name: str | None):
        item = cls.get(chat_id, user_id)
        if name is None:
            if item:
                item.db_sess.delete(item)
                Log.deleted(item, changes=[("chat_id", chat_id), ("user_id", user_id), ("name", item.name)])
        elif item:
            item.name = name
            Log.updated(item)
        else:
            Log.added(cls(chat_id=chat_id, user_id=user_id, name=name))
