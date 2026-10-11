from datetime import datetime, timedelta, timezone

import bafser_tgapi as tgapi
from bafser import Undefined, get_datetime_now, listfind

from bot.access import require_member
from bot.bot import Bot
from bot.queue.utils import (
    get_arg_int,
    get_queue_by_id,
    get_queue_by_reply,
    rebalance_queue_blocks,
    update_queue_msg_if_changes,
    updateQueue,
    updateQueueLoudness,
)
from bot.utils import get_users_from_msg, silent_mode
from data.cache import Cache
from data.queue import Queue
from data.queue_user import QueueUser
from data.user import User
from utils import num_noun, parse_int


@Bot.add_command(desc_adm=("Переименновать очередь", "<new_name> [\\s]"))
@Bot.cmd_for_admin
def queue_rename(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    if len(args) < 1:
        return "Укажите новое имя очереди\nUsage: /queue_rename <new_name> [\\s]"

    old_name = queue.name
    name = " ".join(args)
    if len(name) > 128:
        return "Название очереди должно быть не длиннее 128 символов."
    queue.update_name(name)

    bot.logger.info(f'qid={queue.id} ("{old_name}" -> "{name}")')
    updateQueue(bot, queue, updateQueueLoudness.quiet)
    if not s:
        return f"✏ Имя очереди {old_name} обновлено на очередь {name}"


@Bot.add_command(desc_adm="Очистить очередь")
@Bot.cmd_for_admin
def queue_clear(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    QueueUser.delete_all_in_queue(queue.id)

    bot.logger.info(f"qid={queue.id}")
    updateQueue(bot, queue)
    if not s:
        return f"✏ Очередь {queue.name} очищена"


@Bot.add_command(desc_adm="Обновить очередь")
@Bot.cmd_for_admin
def queue_force_update(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    bot.logger.info(f"qid={queue.id}")
    updateQueue(bot, queue, updateQueueLoudness.scream)


@Bot.add_command(desc_adm=("Выпнуть из очереди", ["<username> [\\s]", "<number> [\\s]"]))
@Bot.cmd_for_admin
def queue_kick(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    if len(args) < 1:
        return "Укажите ник или номер человека в очереди\nUsage: /queue_kick <username> [\\s]\n/queue_kick <position> [\\s]"

    num = parse_int(args[0])
    uq = None
    if num is None:
        username = args[0]
        uq = QueueUser.get_by_username(queue.id, username)
    elif num - 1 >= 0:
        uq = QueueUser.get_by_order(queue.id, num - 1)

    if uq is None:
        return "Человек не найден в очереди\nUsage: /queue_kick <username> [\\s]\n/queue_kick <number> [\\s]"

    user = uq.user

    if num is not None:
        bot.sendMessage(
            f"Удалить {user.get_full_username(queue.msg.chat_id)} ?",
            reply_markup=tgapi.reply_markup(
                [
                    ("🟢 Да", f"queue_kick_cmd + {queue.id} {user.id}" + (" \\s" if s else "")),
                    ("🔴 Отмена", f"queue_kick_cmd - {queue.id} {user.id}" + (" \\s" if s else "")),
                ]
            ),
        )
        return

    bot.logger.info(f"qid={queue.id} uid={user.id} ({user.get_username()})")
    with update_queue_msg_if_changes(bot, queue):
        uq.delete()

    if not s:
        return f"🔴 {user.get_full_username(queue.msg.chat_id)} теперь не в очереди {queue.name}"


@Bot.add_command()
@Bot.cmd_for_admin
def queue_kick_cmd(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)

    if len(args) < 3:
        return "not enought args"

    queue_id = get_arg_int(args, 1, "queue_id is not int")
    user_id = get_arg_int(args, 2, "user_id is not int")
    queue = get_queue_by_id(bot, queue_id)

    if bot.callback_query and Undefined.defined(bot.callback_query.message):
        msg = bot.callback_query.message
        tgapi.deleteMessage(msg.chat.id, msg.message_id)

    if args[0] == "-":
        return

    uq = QueueUser.get_by_user_id(queue.id, user_id)
    if uq is None:
        return "user not found in queue"

    user = uq.user
    bot.logger.info(f"qid={queue.id} uid={user.id} ({user.get_username()})")
    with update_queue_msg_if_changes(bot, queue):
        uq.delete()

    if not s:
        return f"🔴 {user.get_full_username(queue.msg.chat_id)} теперь не в очереди {queue.name}"


@Bot.add_command(desc_adm=("Добавить на позицию в очереди", "<position> <username> [priority] [\\s]"))
@Bot.cmd_for_admin
def queue_add_to(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    if len(args) < 2:
        return "Укажите позицию для вставки и ник человека\nUsage: /queue_add_to <position> <username> [priority] [\\s]"

    pos = get_arg_int(args, 0, "Позиция для вставки должна быть целым числом")
    pos -= 1

    username = args[1]
    user = User.get_by_username(username)
    if not user:
        return "👻 Этот пользователь не знаком боту (если в имени ошибки нет, пускай он хотя бы раз повзаимодействует с ботом)"

    require_member(bot, user)

    priority = 0 if len(args) < 3 else parse_int(args[2])
    if priority is None:
        return "Приоритет для вставки должен быть целым числом"
    priorities = queue.priorities or [""]
    priority = min(max(priority, 0), len(priorities) - 1)

    with update_queue_msg_if_changes(bot, queue):
        qus = QueueUser.all_in_queue(queue.id)
        qu = listfind(qus, lambda x: x.user_id == user.id)
        if qu is None:
            qu = QueueUser.new(queue.id, user.id)
            qus.append(qu)
        qui = qus.index(qu)

        while True:
            if qui < pos:
                if qui >= len(qus) - 1:
                    break
                qu.block = qus[qui + 1].block
                qus[qui], qus[qui + 1] = qus[qui + 1], qus[qui]
                QueueUser.swap_enter_date(qus[qui], qus[qui + 1], commit=False)
                qui += 1
            elif qui > pos:
                if qui <= 0:
                    break
                qu.block = qus[qui - 1].block
                qus[qui], qus[qui - 1] = qus[qui - 1], qus[qui]
                QueueUser.swap_enter_date(qus[qui], qus[qui - 1], commit=False)
                qui -= 1
            else:
                break
        qu.priority = priority
        rebalance_queue_blocks(queue, qus)
        bot.db_sess.commit()

    bot.logger.info(f"qid={queue.id} uid={user.id} ({user.get_username()}) qui={qui}")
    if not s:
        p = priorities[priority].strip()
        p = f" ({p})" if p else ""
        return f"🟢 {user.get_full_username(queue.msg.chat_id)} теперь в очереди {queue.name} на позиции {qui + 1}{p}"


@Bot.add_command(desc_adm=("Полностью изменить очередь", "<username> [...<username>] [\\s]"))
@Bot.cmd_for_admin
def queue_set(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    users, err = get_users_from_msg(bot, args)
    if err:
        return err

    if len(users) == 0:
        return

    for user in users:
        require_member(bot, user)

    bot.logger.info(f"qid={queue.id} [{'; '.join(f'{u.id} ({u.get_username()})' for u in users)}]")
    QueueUser.delete_all_in_queue(queue.id)
    now = get_datetime_now() - timedelta(seconds=len(users))
    for i, user in enumerate(users):
        qu = QueueUser.new(queue.id, user.id, commit=False)
        qu.enter_date = now + timedelta(seconds=i)

    bot.db_sess.commit()
    updateQueue(bot, queue)

    if not s:
        return f"✏ Очередь {queue.name} изменена"


@Bot.add_command(desc_adm=("Установить время очистки", ["reset [\\s]", "<day of week> <time> [\\s]"]))
@Bot.cmd_for_admin
def queue_set_clear_at(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    if len(args) == 1 and args[0] == "reset":
        queue.update_clear_at(None)
        bot.logger.info(f"qid={queue.id} (new clear time: None)")
        updateQueue(bot, queue, updateQueueLoudness.quiet)
        if s:
            return
        return f"✏ Отключено авто-очищение очереди {queue.name}"
    if len(args) < 2:
        return (
            "Укажите время очистки\n"
            "Usage:\n"
            "reset time: /queue_set_clear_at reset [\\s]\n"
            "set time: /queue_set_clear_at <day of week> <time> [\\s]\n"
            "Params:\n"
            "<day of week>: Понедельник | Пн | 1\n"
            "<time>: 13:45"
        )

    day = args[0].strip().lower()
    if day in ("понедельник", "пн", "1"):
        dayI = 1
    elif day in ("вторник", "вт", "2"):
        dayI = 2
    elif day in ("среда", "ср", "3"):
        dayI = 3
    elif day in ("четверг", "чт", "4"):
        dayI = 4
    elif day in ("пятница", "пт", "5"):
        dayI = 5
    elif day in ("суббота", "сб", "6"):
        dayI = 6
    elif day in ("воскресенье", "вс", "7"):
        dayI = 7
    else:
        return "Некоректный день недели"

    parts = args[1].strip().split(":")
    if len(parts) != 2:
        return "Время не в формате hh:mm"
    hour, minute = parts
    hour = parse_int(hour)
    minute = parse_int(minute)
    if hour is None or minute is None:
        return "Время не в формате hh:mm"
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return "Некоректное время"

    queue.update_clear_at((dayI, hour, minute), commit=False)
    # Never clear participants for an occurrence before the schedule was set.
    queue.cleared_at = datetime.now(timezone.utc).replace(tzinfo=None)
    queue.db_sess.commit()

    day = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][dayI - 1]
    clear_time = f"{day} {hour:02}:{minute:02}"
    bot.logger.info(f"qid={queue.id} (new clear time: {clear_time})")
    updateQueue(bot, queue, updateQueueLoudness.quiet)
    if not s:
        return f"✏ Время очистки очереди {queue.name} обновлено на {clear_time}"


@Bot.add_command(desc_adm=("Установить блоки (| - для сокр.)", "[...<blockname>] [\\s]"))
@Bot.cmd_for_admin
def queue_set_blocks(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)
    validate_labels(args.lines, 10)

    cur_block_count = len(queue.blocks) if queue.blocks else 0
    if cur_block_count != 0 and cur_block_count != len(args.lines):
        cid = Cache.put(args.lines, list[str], queue_id=queue.id, action="blocks")
        bot.sendMessage(
            f"Сейчас {cur_block_count} {num_noun(cur_block_count, 'блок', 'блока', 'блоков')}, а новых {len(args.lines)}. Изменение сбросит распределение по блокам. Продолжить?",
            reply_markup=tgapi.reply_markup(
                [
                    ("🟢 Да", f"queue_set_blocks_cmd + {queue.id} {cid}" + (" \\s" if s else "")),
                    ("🔴 Отмена", f"queue_set_blocks_cmd - {queue.id} {cid}" + (" \\s" if s else "")),
                ]
            ),
        )
        return

    queue_set_blocks_run(bot, queue, args.lines, s)


@Bot.add_command()
@Bot.cmd_for_admin
def queue_set_blocks_cmd(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)

    if len(args) < 3:
        return "not enought args"

    queue_id = get_arg_int(args, 1, "queue_id is not int")
    queue = get_queue_by_id(bot, queue_id)

    if bot.callback_query and Undefined.defined(bot.callback_query.message):
        msg = bot.callback_query.message
        tgapi.deleteMessage(msg.chat.id, msg.message_id)

    if args[0] == "-":
        Cache.pop(args[2], list[str], queue_id=queue.id, action="blocks")
        return

    lines = Cache.pop(args[2], list[str], queue_id=queue.id, action="blocks")
    if lines is None:
        return "cached lines not found"

    queue_set_blocks_run(bot, queue, lines, s)


def queue_set_blocks_run(bot: Bot, queue: Queue, blocks: list[str], s: bool):
    with update_queue_msg_if_changes(bot, queue):
        queue.update_blocks(blocks, commit=False)
        QueueUser.update_block_for_all_in_queue(queue.id, len(blocks))
        rebalance_queue_blocks(queue)

    bot.logger.info(f"qid={queue.id} (new blocks: {blocks})")
    if not s:
        return f"✏ Блоки очереди {queue.name} обновлены"


@Bot.add_command(desc_adm=("Установить макс. кол-во человек в блоке", "<number> [\\s]"))
@Bot.cmd_for_admin
def queue_set_max_in_block(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)

    if len(args) != 1:
        return "Укажите количество\nUsage: /queue_set_max_in_block <number> [\\s]"

    count = get_arg_int(args, 0, "Количество должно быть целым числом")
    if count < 0:
        return "Количество не может быть отрицательным; 0 отключает лимит."

    with update_queue_msg_if_changes(bot, queue):
        queue.update_max_in_block(count)
        rebalance_queue_blocks(queue)

    bot.logger.info(f"qid={queue.id} (new max in block: {count})")
    if not s:
        return f"✏ Максимальное количество человек в блоке очереди {queue.name} обновлено на {count}"


@Bot.add_command(desc_adm=("Установить приоритеты (| - для сокр.)", "[...<groupname>] [\\s]"))
@Bot.cmd_for_admin
def queue_set_priorities(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)
    queue = get_queue_by_reply(bot)
    validate_labels(args.lines, 5)

    cur_priority_count = len(queue.priorities) if queue.priorities else 0
    if cur_priority_count != 0 and cur_priority_count != len(args.lines):
        cid = Cache.put(args.lines, list[str], queue_id=queue.id, action="priorities")
        bot.sendMessage(
            f"Сейчас {cur_priority_count} {num_noun(cur_priority_count, 'группа', 'группы', 'групп')}, а новых {len(args.lines)}. Изменение сбросит распределение по группам. Продолжить?",
            reply_markup=tgapi.reply_markup(
                [
                    ("🟢 Да", f"queue_set_priorities_cmd + {queue.id} {cid}" + (" \\s" if s else "")),
                    ("🔴 Отмена", f"queue_set_priorities_cmd - {queue.id} {cid}" + (" \\s" if s else "")),
                ]
            ),
        )
        return

    queue_set_priorities_run(bot, queue, args.lines, s)


@Bot.add_command()
@Bot.cmd_for_admin
def queue_set_priorities_cmd(bot: Bot, args: tgapi.BotCmdArgs, **_: str):
    s = silent_mode(bot, args)

    if len(args) < 3:
        return "not enought args"

    queue_id = get_arg_int(args, 1, "queue_id is not int")
    queue = get_queue_by_id(bot, queue_id)

    if bot.callback_query and Undefined.defined(bot.callback_query.message):
        msg = bot.callback_query.message
        tgapi.deleteMessage(msg.chat.id, msg.message_id)

    if args[0] == "-":
        Cache.pop(args[2], list[str], queue_id=queue.id, action="priorities")
        return

    lines = Cache.pop(args[2], list[str], queue_id=queue.id, action="priorities")
    if lines is None:
        return "cached lines not found"

    queue_set_priorities_run(bot, queue, lines, s)


def queue_set_priorities_run(bot: Bot, queue: Queue, priorities: list[str], s: bool):
    with update_queue_msg_if_changes(bot, queue):
        queue.update_priorities(priorities, commit=False)
        QueueUser.update_priority_for_all_in_queue(queue.id, len(priorities))
        rebalance_queue_blocks(queue)

    bot.logger.info(f"qid={queue.id} (new priorities: {priorities})")
    if not s:
        return f"✏ Группы очереди {queue.name} обновлены"


def validate_labels(lines: list[str], maximum: int):
    if len(lines) > maximum or any(len(line) > 64 for line in lines):
        tgapi.raiseBotAnswer(f"Допустимо до {maximum} строк, каждая не длиннее 64 символов.")
