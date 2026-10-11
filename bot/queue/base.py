import bafser_tgapi as tgapi
from bafser import Undefined, listfind

from bot.access import require_admin, require_member
from bot.bot import Bot
from bot.queue.utils import (
    get_arg_int,
    get_queue,
    get_queue_by_reply,
    queue_enter_reply_markup,
    rebalance_queue_blocks,
    update_queue_msg_if_changes,
    updateQueue,
)
from bot.utils import silent_mode
from data.queue import Queue
from data.queue_user import QueueUser
from data.user import User


@Bot.add_command(desc_adm=("Создать новую очередь", "<name> [\\s]"))
@Bot.cmd_for_admin
def queue_new(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    silent_mode(bot, args)
    if len(args) < 1:
        return "Укажите имя очереди\nUsage: /queue_new <name> [\\s]"

    name = " ".join(args)
    if len(name) > 128:
        return "Название очереди должно быть не длиннее 128 символов."
    bot.logger.info(f"creating {name}")
    ok, r = bot.sendMessage(f"📝 Очередь {name}:\n⏳ Создание...")
    if not ok:
        bot.logger.error(r)
        return "Error!"

    queue = Queue.new_by_message(r, name)
    bot.logger.info(f"created id={queue.id} ({name})")
    tgapi.pinChatMessage(r.chat.id, r.message_id)
    updateQueue(bot, queue)


# /queue_enter queue_id
# /queue_enter queue_id block_i priority_i
# /queue_enter queue_id block_i priority_i user_id
@Bot.add_command()
def queue_enter(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    queue = get_queue(bot, args)
    if len(args) < 4:
        require_member(bot, bot.user)
    if len(args) == 1:
        qu = QueueUser.get(queue.id, bot.user.id)
        if qu is not None:
            return "Уже в очереди"

        bot.logger.info(f"qid={queue.id} uid={bot.user.id} ({bot.user.get_username()})")
        with update_queue_msg_if_changes(bot, queue):
            QueueUser.new(queue.id, bot.user.id)
    else:
        block = get_arg_int(args, 1, "block is not int")
        priority = get_arg_int(args, 2, "priority is not int")
        block = min(max(block, 0), len(queue.blocks or [""]) - 1)
        priority = min(max(priority, 0), len(queue.priorities or [""]) - 1)

        user_id = bot.user.id
        log_username = bot.user.get_username()
        another_user = False
        if len(args) >= 4:
            require_admin(bot)
            if bot.callback_query and Undefined.defined(bot.callback_query.message):
                msg = bot.callback_query.message
                tgapi.deleteMessage(msg.chat.id, msg.message_id)
            user_id = get_arg_int(args, 3, "user_id is not int")
            user = User.get(user_id)
            if not user:
                return "user not found"
            require_member(bot, user)
            log_username = user.get_username()
            another_user = user.get_full_username(queue.msg.chat_id)

        qu = QueueUser.get(queue.id, user_id)
        if qu is not None and (another_user or (qu.block == block and qu.priority == priority)):
            return "Уже в очереди"

        bot.logger.info(f"qid={queue.id} uid={user_id} ({log_username}) {block=} {priority=}")
        with update_queue_msg_if_changes(bot, queue):
            if qu:
                qu.update(block, priority)
            else:
                QueueUser.new(queue.id, user_id, block, priority)
            rebalance_queue_blocks(queue, added_user_id=user_id)
        if another_user:
            bot.sendMessage(f"🟢 {another_user} теперь в очереди {queue.name}")
            return
    return "Вы встали в очередь"


@Bot.add_command()
def queue_exit(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    queue = get_queue(bot, args)
    qu = QueueUser.get(queue.id, bot.user.id)
    if qu is None:
        return "Уже не в очереди"

    bot.logger.info(f"qid={queue.id} uid={bot.user.id} ({bot.user.get_username()})")
    with update_queue_msg_if_changes(bot, queue):
        qu.delete()
    return "Вы вышли из очереди"


@Bot.add_command()
def queue_pass(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    queue = get_queue(bot, args)
    qus = list(QueueUser.all_in_queue(queue.id))
    uid = bot.user.id
    qu = listfind(qus, lambda x: x.user_id == uid)
    if qu is None:
        return "Вы не в очереди"
    qui = qus.index(qu)

    if qui + 1 >= len(qus):
        return "Вы уже в конце очереди"

    next_qu = qus[qui + 1]

    if next_qu.block != qu.block:
        return "Вы уже в конце блока"

    if next_qu.priority != qu.priority:
        return "Нельзя пропустить человека с другим приоритетом"

    bot.logger.info(f"qid={queue.id} uid={bot.user.id} ({bot.user.get_username()})")
    with update_queue_msg_if_changes(bot, queue):
        QueueUser.swap_enter_date(qu, next_qu)

    return "Вы пропустили одного"


@Bot.add_command()
def queue_end(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    queue = get_queue(bot, args)
    bot.logger.info(f"qid={queue.id} uid={bot.user.id} ({bot.user.get_username()})")
    qu = QueueUser.get(queue.id, bot.user.id)
    with update_queue_msg_if_changes(bot, queue):
        if qu is None:
            require_member(bot, bot.user)
            QueueUser.new(queue.id, bot.user.id)
        else:
            qu.set_now_as_enter_date()

    return "Вы встали в конец"


@Bot.add_command(desc_adm=("Добавить в очередь", "<username> [\\s]"))
@Bot.cmd_for_admin
def queue_add(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)

    if len(args) < 1:
        return "Укажите кого добавить в очередь\nUsage: /queue_add <username> [\\s]"

    queue = get_queue_by_reply(bot)
    username = args[0]
    user = User.get_by_username(username)

    if not user:
        return f"👻 Этот пользователь ({username}) не знаком боту (если в имени ошибки нет, пускай он хотя бы раз повзаимодействует с ботом)"

    require_member(bot, user)

    blocks = queue.blocks if queue.blocks else [""]
    priorities = queue.priorities if queue.priorities else [""]
    simple_queue = len(blocks) <= 1 and len(priorities) <= 1

    qu = QueueUser.get(queue.id, user.id)
    if qu is not None:
        return f"🟢 {user.get_full_username(queue.msg.chat_id)} уже в очереди {queue.name}"

    if not simple_queue:
        reply_markup = queue_enter_reply_markup(queue.id, blocks, priorities, user.id)
        bot.sendMessage(
            f"Куда добавить {username}?",
            reply_markup=tgapi.reply_markup(*reply_markup),
        )
        return

    bot.logger.info(f"qid={queue.id} uid={user.id} ({user.get_username()})")
    with update_queue_msg_if_changes(bot, queue):
        QueueUser.new(queue.id, user.id)

    if not s:
        return f"🟢 {user.get_full_username(queue.msg.chat_id)} теперь в очереди {queue.name}"
