import logging

import bafser_tgapi as tgapi
from bafser import Undefined

from bot.access import require_member
from bot.bot import Bot
from bot.queue.utils import updateQueue, updateQueueLoudness
from bot.tic_tac_toe import update_msg
from data.msg import Msg
from data.queue import Queue
from data.queue_user import QueueUser
from data.tic_tac_toe import TicTacToe
from data.user import User
from data.user_name import UserName

logger = logging.getLogger(__name__)


def name_target(bot: Bot, args: tgapi.BotCmdArgs):
    if not bot.chat or bot.chat.type not in ("group", "supergroup"):
        tgapi.raiseBotAnswer("Отображаемые имена настраиваются администраторами группы.")
    if bot.message and Undefined.defined(bot.message.reply_to_message):
        replied = bot.message.reply_to_message
        if not Undefined.defined(replied.sender) or replied.sender.is_bot:
            tgapi.raiseBotAnswer("Ответьте на сообщение пользователя от его личного аккаунта.")
        user = User.get_by_id_tg(replied.sender.id)
        if user is None:
            user = User.new_from_data(replied.sender)
        name = args.raw_args.strip()
    else:
        if not len(args) or not args[0].startswith("@"):
            tgapi.raiseBotAnswer("Укажите @username или ответьте на сообщение пользователя.")
        user = User.get_by_username(args[0])
        if user is None:
            tgapi.raiseBotAnswer("Пользователь не знаком боту; попросите его написать /start.")
        parts = args.raw_args.split(maxsplit=1)
        name = parts[1].strip() if len(parts) == 2 else ""
    require_member(bot, user)
    return user, name


def refresh_name(bot: Bot, user: User):
    failed = False
    queues = (Queue.query().join(Msg, Msg.id == Queue.msg_id)
              .join(QueueUser, QueueUser.queue_id == Queue.id)
              .filter(Msg.chat_id == bot.chat.id, QueueUser.user_id == user.id).all())
    for queue in queues:
        try:
            updateQueue(bot, queue, updateQueueLoudness.quiet)
        except Exception:
            failed = True
            bot.db_sess.rollback()
            logger.exception("Failed to refresh queue %s after name change", queue.id)
    games = (TicTacToe.query().join(Msg, Msg.id == TicTacToe.msg_id)
             .filter(Msg.chat_id == bot.chat.id, TicTacToe.started.is_(True),
                     (TicTacToe.player1_id == user.id) | (TicTacToe.player2_id == user.id)).all())
    for game in games:
        if game.get_status()[0] != "turn":
            continue
        try:
            update_msg(game)
        except Exception:
            failed = True
            bot.db_sess.rollback()
            logger.exception("Failed to refresh game %s after name change", game.id)
    return "\nЧасть сообщений не обновилась; имя сохранено и появится при следующем обновлении." if failed else ""


@Bot.add_command(desc_adm=("Имя участника в этой группе", ["@username <имя>", "<имя> — ответом пользователю"]))
@Bot.cmd_for_admin
def user_name(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    user, name = name_target(bot, args)
    if not name or len(name) > 128 or "\n" in name or "\r" in name:
        return "Укажите имя одной строкой, от 1 до 128 символов."
    UserName.set(bot.chat.id, user.id, name)
    return f"✏ Имя в этой группе: {user.get_full_username(bot.chat.id)}" + refresh_name(bot, user)


@Bot.add_command(desc_adm=("Сбросить имя участника", ["@username", "ответом пользователю"]))
@Bot.cmd_for_admin
def user_name_reset(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    user, remaining = name_target(bot, args)
    if remaining:
        return "Укажите только @username или ответьте пользователю без аргументов."
    UserName.set(bot.chat.id, user.id, None)
    return f"✏ Восстановлено имя Telegram: {user.get_full_username(bot.chat.id)}" + refresh_name(bot, user)
