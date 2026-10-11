from bafser_tgapi import TgUserBase

from data import Roles


class User(TgUserBase):
    _default_role = Roles.user

    def get_name(self, chat_id: int | None = None):
        if chat_id is not None:
            from data.user_name import UserName

            item = UserName.get(chat_id, self.id, db_sess=self.db_sess)
            if item:
                return item.name
        return super().get_name()

    def get_full_username(self, chat_id: int | None = None):
        if self.username:
            return f"{self.get_name(chat_id)} (@{self.username})"
        return self.get_name(chat_id)
