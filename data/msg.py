from bafser_tgapi import MsgBase
from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from data import Tables


class Msg(MsgBase):
    __tablename__ = Tables.Msg

    text: Mapped[str | None] = mapped_column(Text)
