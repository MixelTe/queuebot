from functools import wraps

import bafser_tgapi as tgapi


def require_chat(bot):
    if not bot.chat or not bot.sender or bot.sender.is_bot:
        tgapi.raiseBotAnswer("Команда доступна пользователю в личном чате или группе.")
    if bot.chat.type not in ("private", "group", "supergroup"):
        tgapi.raiseBotAnswer("Используйте личный чат или группу.")
    if bot.chat.type == "private" and bot.chat.id != bot.sender.id:
        tgapi.raiseBotAnswer("Это чужой личный чат.")


def require_same_chat(bot, chat_id: int):
    require_chat(bot)
    if bot.chat.id != chat_id:
        tgapi.raiseBotAnswer("В этом чате очередь или игра не найдена.")


def require_admin(bot):
    require_chat(bot)
    if bot.chat.type == "private":
        return
    ok, member = tgapi.getChatMember(bot.chat.id, bot.sender.id)
    if not ok or member.status not in ("creator", "administrator"):
        tgapi.raiseBotAnswer("Управлять очередями могут только администраторы этой группы.")


def require_member(bot, user):
    require_chat(bot)
    if user.is_bot:
        tgapi.raiseBotAnswer("Бота нельзя добавить как участника.")
    if bot.chat.type == "private":
        if bot.chat.id != user.id_tg:
            tgapi.raiseBotAnswer("В личном чате можно добавить только себя.")
        return
    ok, member = tgapi.getChatMember(bot.chat.id, user.id_tg)
    if not ok or member.status not in ("creator", "administrator", "member"):
        tgapi.raiseBotAnswer("Пользователь должен состоять в этой группе.")


def chat_admin(fn):
    @wraps(fn)
    def wrapped(bot, args, **kwargs):
        require_admin(bot)
        return fn(bot, args, **kwargs)

    return wrapped
