import bafser_tgapi as tgapi
from bafser import Undefined

from bot.bot import Bot
from data.user import User


def get_users_from_msg(bot: Bot, args: tgapi.BotCmdArgs):
    assert bot.message
    usernames: list[str] = []
    users: list[User] = []
    users_not_found: list[str] = []

    users_notag = [e for e in bot.message.entities if e.type == "text_mention"]
    for e in users_notag:
        username = e.get_msg_text(args.input)
        if username in usernames:
            continue
        usernames.append(username)
        assert Undefined.defined(e.user)
        user = User.get_by_id_tg(e.user.id)
        if user:
            users.append(user)
        else:
            users_not_found.append(username)

    for username in args:
        if username in usernames:
            continue
        usernames.append(username)
        user = User.get_by_username(username)
        if user:
            users.append(user)
        else:
            users_not_found.append(username)

    users = list({user.id: user for user in users}.values())
    err = None
    if len(users_not_found) != 0:
        if len(users_not_found) == 1:
            err = f"👻 Этот пользователь не знаком боту: {users_not_found[0]}" + \
                "\n(если в имени ошибки нет, пускай он хотя бы раз повзаимодействует с ботом)"
        else:
            err = f"Эти пользователи не знакомы боту: {', '.join(users_not_found)}" + \
                "\n(если в именах ошибки нет, пускай они хотя бы раз повзаимодействуют с ботом)"

    return users, err


def silent_mode(bot: Bot, args: tgapi.BotCmdArgs):
    if len(args) == 0 or args[-1] != "\\s":
        return False
    args.args = args.args[:-1]
    args.raw_args = args.raw_args.rstrip()[:-2].rstrip()
    args.lines = [line.strip() for line in args.raw_args.split("\n") if line.strip()]
    if bot.message:
        tgapi.deleteMessage(bot.message.chat.id, bot.message.message_id)
    return True


