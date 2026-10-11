import bafser_tgapi as tgapi
from bafser import add_logger, create_log_handler
from flask import g, has_request_context

import bafser_config
from bot.access import chat_admin
from data.user import User
from utils.runtime import update_lock


class Bot(tgapi.BotWithDB[User]):
    _userCls = User

    cmd_for_admin = staticmethod(chat_admin)

    @classmethod
    def init(cls):
        # WSGI initialization must not create background threads or call Telegram.
        cls._logger = add_logger("bot", create_log_handler(
            bafser_config.log_bot_path,
            "%(asctime)s;%(levelname)s;%(module)s;%(uid)-10s;%(uname)-15s;%(cmd)-15s;%(message)s",
            outer_args=["uid", "uname", "cmd"],
        ))

    def _on_command(self, input: str):
        if has_request_context() and self.sender and not self.sender.is_bot:
            previous = g.pop("db_session", None)
            if previous:
                previous.close()
            # In synchronous webhook requests Flask's context takes precedence.
            g.db_session = self.db_sess
            g.user = self.user
        return super()._on_command(input)

    def __enter__(self):
        update_lock.acquire()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            update_lock.release()
