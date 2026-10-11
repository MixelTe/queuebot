import math
from collections.abc import Callable

import bafser_tgapi as tgapi
from bafser import Undefined

import bafser_config
from bot.access import require_same_chat
from bot.bot import Bot
from data.queue import Queue
from data.queue_user import QueueUser
from utils import num_noun, parse_int
from utils.delivery import defer_message


class updateQueueLoudness:
    silent = 0
    quiet = 1
    loud = 2
    scream = 3


def updateQueue(bot: Bot, queue: Queue, loudness=updateQueueLoudness.loud, *, checkpoint: Callable[[int], None] | None = None):
    if defer_message("queue", queue.id, loudness):
        return
    if loudness >= updateQueueLoudness.scream:
        tgapi.deleteMessage(queue.msg.chat_id, queue.msg.message_id)
        ok, r = bot.sendMessage(f"📝 Очередь {queue.name}:\n⏳ Обновление...", chat_id=queue.msg.chat_id,
                                message_thread_id=queue.msg.message_thread_id)
        if not ok:
            raise RuntimeError(f"Failed to recreate queue message: {r}")

        queue.update_msg(r, commit=checkpoint is None)
        if checkpoint:
            checkpoint(updateQueueLoudness.loud)
        tgapi.pinChatMessage(r.chat.id, r.message_id)

    _blocks = queue.blocks if queue.blocks else [""]
    blocks = [[s.strip() for s in block.split("|")] for block in _blocks]
    _priorities = queue.priorities or [""]
    priorities = [[s.strip() for s in p.split("|")] for p in _priorities]

    txt = f"📝 Очередь {queue.name}:\n"

    clear_at = queue.get_parsed_clear_at()
    if clear_at:
        (_, day), _, hour, minute = clear_at
        txt += f"🧹 Очистка в {day} {hour:02}:{minute:02} ({bafser_config.queue_timezone})\n"

    if len(blocks) > 1 and queue.max_in_block > 0:
        txt += f"До {queue.max_in_block} {num_noun(queue.max_in_block, 'человека', 'человек', 'человек')} на блок" + "\n"

    txt += "━" * math.floor(len(queue.name) * 0.5 + 7) + "\n"

    qus = QueueUser.all_in_queue(queue.id)
    if len(blocks) <= 1 and len(priorities) <= 1:
        if len(qus) == 0:
            txt += "Никого в очереди"
        else:
            for i, qu in enumerate(qus):
                txt += f"{i + 1}) {qu.user.get_full_username(queue.msg.chat_id)}\n"

        reply_markup = [
            [
                ("🟢 Встать", f"queue_enter {queue.id}"),
                ("🔴 Выйти", f"queue_exit {queue.id}"),
            ]
        ]
    elif len(blocks) <= 1:
        if len(qus) == 0:
            txt += "Никого в очереди"
        else:
            for i, qu in enumerate(qus):
                priority_i = max(min(qu.priority, len(priorities) - 1), 0)
                username = ""
                if qu.user.username != "":
                    username = f" (@{qu.user.username})"
                txt += f"{i + 1}) {qu.user.get_name(queue.msg.chat_id)} ({priorities[priority_i][0]}){username}\n"

        reply_markup = [
            [
                ("🎭 Пропуск", f"queue_pass {queue.id}"),
                ("🔴 Выйти", f"queue_exit {queue.id}"),
            ]
        ] + queue_enter_reply_markup(queue.id, blocks, priorities)
    else:
        block_i = -1
        for i, qu in enumerate(qus):
            if qu.block != block_i:
                new_block = max(min(qu.block, len(blocks) - 1), 0)
                while block_i < new_block:
                    block_i += 1
                    txt += f"\n{blocks[block_i][0]}\n"
                    if block_i < new_block:
                        txt += "— пока никого\n"
            priority_i = max(min(qu.priority, len(priorities) - 1), 0)
            priority = f" ({priorities[priority_i][0]})" if len(priorities) > 1 else ""
            username = ""
            if qu.user.username != "":
                username = f" (@{qu.user.username})"
            txt += f"{i + 1}) {qu.user.get_name(queue.msg.chat_id)}{priority}{username}\n"
        while block_i < len(blocks) - 1:
            block_i += 1
            txt += f"\n{blocks[block_i][0]}\n"
            txt += "— пока никого\n"

        reply_markup = [
            [
                ("🎭 Пропуск", f"queue_pass {queue.id}"),
                ("🔴 Выйти", f"queue_exit {queue.id}"),
            ]
        ] + queue_enter_reply_markup(queue.id, blocks, priorities)

    _update_next_msg(bot, queue, qus, loudness, commit=checkpoint is None)
    if checkpoint:
        checkpoint(min(loudness, updateQueueLoudness.quiet))

    if len(txt) > 4000:
        txt = txt[:3900].rsplit("\n", 1)[0] + f"\n… Всего участников: {len(qus)}. Список сокращён."
    ok, result = tgapi.editMessageText(queue.msg.chat_id, queue.msg.message_id, txt, reply_markup=tgapi.reply_markup(*reply_markup))
    if not ok and "message is not modified" not in str(result):
        raise RuntimeError(f"Failed to update queue message: {result}")


def queue_enter_reply_markup(queue_id: int, blocks: list[list[str]] | list[str], priorities: list[list[str]] | list[str], user_id: int | None = None):
    reply_markup: list[list[tuple[str, str]]] = []
    K = 3
    cmd_postfix = f" {user_id}" if user_id is not None else ""
    for pi, p in enumerate(priorities):
        if isinstance(p, str):
            p = [s.strip() for s in p.split("|")]
        p = p[1] if len(p) > 1 else p[0]
        postfix = f" ({p})" if p else ""
        for i in range(len(blocks) // K + 1):
            line: list[tuple[str, str]] = []
            for j in range(K):
                block_i = i * K + j
                if block_i >= len(blocks):
                    break
                block = blocks[block_i]
                if isinstance(block, str):
                    block = [s.strip() for s in block.split("|")]
                block_name = block[1] if len(block) > 1 else block[0]
                if block_name:
                    name = (block_name + postfix).strip()
                else:
                    name = f"🟢 Встать {p}"
                line.append((name, f"queue_enter {queue_id} {block_i} {pi}" + cmd_postfix))
            if line:
                reply_markup.append(line)
    return reply_markup


def _update_next_msg(bot: Bot, queue: Queue, qus: list[QueueUser], loudness: int, *, commit=True):
    if len(qus) == 0:
        if queue.msg_next is not None:
            tgapi.deleteMessage(queue.msg_next.chat_id, queue.msg_next.message_id)
            queue.update_msg_next(None, commit=commit)
        return

    simple_queue = len(queue.blocks or []) <= 1 and len(queue.priorities or []) <= 1
    if loudness >= updateQueueLoudness.quiet:
        if len(qus) > 1:
            txt_next = f"🎞 Следующие в очереди {queue.name}\n🥇-> {qus[0].user.get_full_username(queue.msg.chat_id)}\n🥈-> {qus[1].user.get_full_username(queue.msg.chat_id)}"
            if len(qus) == 3:
                txt_next += "\n💤 И ещё 1 ждущий"
            elif len(qus) > 3:
                txt_next += f"\n💤 И ещё {len(qus) - 2} ждущих"
        else:
            txt_next = f"🎞 Следующий в очереди {queue.name}\n🥇-> {qus[0].user.get_full_username(queue.msg.chat_id)}"

        btns: list[tuple[str, str]] = []
        if len(qus) > 1:
            btns.append(("🎭 Пропуск", f"queue_pass {queue.id}"))
        btns.append(("🔴 Выйти", f"queue_exit {queue.id}"))
        if simple_queue:
            btns.append(("💫 В конец", f"queue_end {queue.id}"))

        if loudness >= updateQueueLoudness.loud or queue.msg_next is None:
            if queue.msg_next is not None:
                tgapi.deleteMessage(queue.msg_next.chat_id, queue.msg_next.message_id)
            ok, r = bot.sendMessage(
                txt_next,
                chat_id=queue.msg.chat_id,
                reply_markup=tgapi.reply_markup(btns),
                message_thread_id=queue.msg.message_thread_id,
                reply_parameters=tgapi.ReplyParameters(message_id=queue.msg.message_id),
            )
            if not ok:
                raise RuntimeError(f"Failed to send queue notification: {r}")
            queue.update_msg_next(r, commit=commit)
        else:
            if queue.msg_next is not None:
                ok, result = tgapi.editMessageText(queue.msg_next.chat_id, queue.msg_next.message_id, txt_next, reply_markup=tgapi.reply_markup(btns))
                if not ok and "message is not modified" not in str(result):
                    raise RuntimeError(f"Failed to update queue notification: {result}")


def get_queue(bot: Bot, args: tgapi.BotCmdArgs) -> Queue:
    if len(args) < 1:
        tgapi.raiseBotAnswer("No queue id provided")

    id = parse_int(args[0])
    if id is None:
        tgapi.raiseBotAnswer("id is NaN")

    return get_queue_by_id(bot, id)


def get_queue_by_id(bot: Bot, id: int) -> Queue:
    queue = Queue.get(id, for_update=True)
    if queue is None:
        tgapi.raiseBotAnswer(f"queue with id={id} doesnt exist")

    require_same_chat(bot, queue.msg.chat_id)
    return queue


def get_arg_int(args: tgapi.BotCmdArgs, i: int, errmsg: str):
    if len(args) <= i:
        tgapi.raiseBotAnswer(errmsg)
    v = parse_int(args[i])
    if v is None:
        tgapi.raiseBotAnswer(errmsg)
    return v


def get_queue_by_reply(bot: Bot):
    if not bot.message or not Undefined.defined(bot.message.reply_to_message):
        tgapi.raiseBotAnswer("Укажите очередь, ответив на неё")

    queue = Queue.get_by_message(bot.message.reply_to_message)
    if not queue:
        tgapi.raiseBotAnswer("Необходимо ответить на сообщение очереди (это не оно, или оно уже не действительно)")

    require_same_chat(bot, queue.msg.chat_id)
    return queue


class update_queue_msg_if_changes:
    def __init__(self, bot: Bot, queue: Queue):
        self.bot = bot
        self.queue = queue

    def __enter__(self):
        self.first, self.second = QueueUser.first2_in_queue(self.queue.id)
        self.count = QueueUser.count_in_queue(self.queue.id)
        return self

    def __exit__(self, exception_type, exception_value, exception_traceback):
        if exception_type is not None:
            return
        first, second = QueueUser.first2_in_queue(self.queue.id)
        count = QueueUser.count_in_queue(self.queue.id)
        big_changes = False
        if self.first is not None and first is not None:
            if self.first.user_id != first.user_id:
                big_changes = True
        else:
            if (self.first is None) != (first is None):
                big_changes = True
        if self.second is not None and second is not None:
            if self.second.user_id != second.user_id:
                big_changes = True
        else:
            if (self.second is None) != (second is None):
                big_changes = True
        count_changed = self.count != count
        silence = updateQueueLoudness.silent
        if count_changed:
            silence = updateQueueLoudness.quiet
        if big_changes:
            silence = updateQueueLoudness.loud
        updateQueue(self.bot, self.queue, silence)


def rebalance_queue_blocks(queue: Queue, qus: list[QueueUser] | None = None, added_user_id: int | None = None):
    blocks = queue.blocks if queue.blocks else [""]
    max_in_block = max(queue.max_in_block, 0)

    if len(blocks) <= 1 or max_in_block <= 0:
        queue.db_sess.commit()
        return

    qus = QueueUser.sorted(qus) or QueueUser.all_in_queue(queue.id)

    qu_by_block: list[list[QueueUser]] = [[] for _ in range(len(blocks))]
    for qu in qus:
        block_i = max(min(qu.block, len(blocks) - 1), 0)
        qu_by_block[block_i].append(qu)

    for block_i in range(len(blocks) - 1):
        block = qu_by_block[block_i]

        if len(block) <= max_in_block:
            continue

        qu_by_block[block_i] = block[:max_in_block]

        overflow = block[max_in_block:]
        next_block = qu_by_block[block_i + 1]

        # Перенесённые пользователи должны оказаться первыми
        # среди пользователей с тем же priority.
        #
        # Исключение — только что добавленный пользователь:
        # он должен остаться последним среди своего priority.
        moved_by_priority: dict[int, list[QueueUser]] = {}
        for qu in overflow:
            moved_by_priority.setdefault(qu.priority, []).append(qu)

        new_next_block: list[QueueUser] = []

        priorities = sorted({qu.priority for qu in overflow + next_block})
        for priority in priorities:
            moved = moved_by_priority.get(priority, [])

            existing = [qu for qu in next_block if qu.priority == priority]
            moved_regular = [qu for qu in moved if qu.user_id != added_user_id]
            moved_added = [qu for qu in moved if qu.user_id == added_user_id]

            priority_group = moved_regular + existing + moved_added

            enter_dates = sorted(qu.enter_date for qu in priority_group)

            for qu, enter_date in zip(priority_group, enter_dates, strict=True):
                if qu.enter_date != enter_date:
                    qu.update_enter_date(enter_date, commit=False)

            new_next_block.extend(priority_group)

        qu_by_block[block_i + 1] = new_next_block

    for block_i, block in enumerate(qu_by_block):
        for qu in block:
            if qu.block != block_i:
                qu.update_block(block_i, commit=False)

    queue.db_sess.commit()
