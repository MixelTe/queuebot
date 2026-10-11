from __future__ import annotations

from typing import Optional

import bafser_tgapi as tgapi
from bafser import IdMixin, Log, SqlAlchemyBase, get_db_session
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from data._tables import Tables
from data.msg import Msg
from data.user import User


class TicTacToe(SqlAlchemyBase, IdMixin):
    __tablename__ = Tables.TicTacToe

    msg_id: Mapped[int] = mapped_column(ForeignKey(f"{Tables.Msg}.id"))
    player1_id: Mapped[int] = mapped_column(ForeignKey(f"{Tables.User}.id"))
    player2_id: Mapped[Optional[int]] = mapped_column(ForeignKey(f"{Tables.User}.id"))
    field: Mapped[str] = mapped_column(String(10))
    started: Mapped[bool] = mapped_column(default=False, server_default="0")

    msg: Mapped[Msg] = relationship(init=False)
    player1: Mapped[User] = relationship(foreign_keys=[player1_id], init=False)
    player2: Mapped[Optional[User]] = relationship(foreign_keys=[player2_id], init=False)

    @staticmethod
    def new(msg_id: int, player1_id: int, player2_id: int | None = None):
        field = "0" * 9
        game = TicTacToe(msg_id=msg_id, player1_id=player1_id, player2_id=player2_id, field=field)
        Log.added(game)
        return game

    @staticmethod
    def new_by_message(message: tgapi.Message, player1_id: int, player2_id: int | None = None):
        msg = Msg.new_from_data(message)
        return TicTacToe.new(msg.id, player1_id, player2_id)

    @staticmethod
    def get_by_message(message: tgapi.Message):
        return (get_db_session().query(TicTacToe)
                .join(Msg, Msg.id == TicTacToe.msg_id)
                .where(Msg.message_id == message.message_id, Msg.chat_id == message.chat.id)
                .first())

    def update_player2(self, player2_id: int):
        self.player2_id = player2_id
        self.started = True
        Log.updated(self)

    def get_status(self):
        winner: int | None = None

        def set_winner(row):
            nonlocal winner
            if row == "111":
                winner = 1
            elif row == "222":
                winner = 2

        for y in range(3):
            set_winner(self.field[y * 3:y * 3 + 3])
        for y in range(3):
            set_winner(self.field[y] + self.field[y + 3] + self.field[y + 6])
        set_winner(self.field[0] + self.field[4] + self.field[8])
        set_winner(self.field[2] + self.field[4] + self.field[6])

        if winner:
            return "winner", winner

        c0 = str(self.field).count("0")
        c1 = str(self.field).count("1")
        c2 = str(self.field).count("2")
        if c0 == 0:
            return "draw", 0
        turn = 1 if c2 > c1 else 2
        return "turn", turn
