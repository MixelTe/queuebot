import logging
from datetime import datetime, timedelta, timezone
from threading import Event
from zoneinfo import ZoneInfo

import bafser_tgapi as tgapi
from bafser import UserBase, db_session
from flask import g

import bafser_config
from bot.bot import Bot
from bot.queue.utils import updateQueue
from data.cache import Cache
from data.processed_update import ProcessedUpdate
from data.queue import Queue
from data.queue_user import QueueUser
from utils.runtime import update_lock

logger = logging.getLogger(__name__)


def scheduled_at(queue: Queue, now: datetime):
    parsed = queue.get_parsed_clear_at()
    if not parsed:
        return None
    _, day, hour, minute = parsed
    local = now.astimezone(ZoneInfo(bafser_config.queue_timezone))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    due -= timedelta(days=(local.isoweekday() - day) % 7)
    if due > local:
        due -= timedelta(days=7)
    return due.astimezone(timezone.utc).replace(tzinfo=None)


def clear_due_queues(app, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    with update_lock, app.test_request_context(), db_session.create_session() as session:
        g.db_session = session
        g.user = UserBase.get_fake_system()
        Cache.delete_stale()
        session.query(ProcessedUpdate).filter(
            ProcessedUpdate.processed_at < now.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=7),
            ProcessedUpdate.deliveries.is_(None),
        ).delete()
        session.commit()
        queue_ids = [queue.id for queue in Queue.query().filter(Queue.clear_at.is_not(None)).all()]
        for queue_id in queue_ids:
            try:
                queue = Queue.get(queue_id)
                due = scheduled_at(queue, now)
                if due is None or (queue.cleared_at and queue.cleared_at >= due):
                    continue
                ok, exists = tgapi.check_message_exists(queue.msg.chat_id, queue.msg.message_id)
                if not exists:
                    if ok:
                        queue.delete2(db_sess=session)
                    continue
                QueueUser.delete_all_in_queue(queue.id, commit=False)
                queue.cleared_at = now.astimezone(timezone.utc).replace(tzinfo=None)
                # Commit the cleanup once, independently of Telegram delivery.
                session.commit()
                with Bot() as bot:
                    updateQueue(bot, queue)
                    ok, result = bot.sendMessage(
                        f"🧹 Очередь {queue.name} очищена по расписанию.",
                        chat_id=queue.msg.chat_id,
                        message_thread_id=queue.msg.message_thread_id,
                        reply_parameters=tgapi.ReplyParameters(
                            message_id=queue.msg.message_id, allow_sending_without_reply=True,
                        ),
                    )
                    if not ok:
                        raise RuntimeError(f"Failed to send cleanup notification: {result}")
            except Exception:
                session.rollback()
                logger.exception("Failed to clear queue %s", queue_id)


def maintenance_loop(app, stop: Event):
    while not stop.is_set():
        try:
            clear_due_queues(app)
        except Exception:
            logger.exception("Queue maintenance failed")
        stop.wait(30)
