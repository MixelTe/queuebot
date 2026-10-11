from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from bafser import UserBase, db_session
from flask import g

from data.processed_update import ProcessedUpdate

_deliveries: ContextVar[list[dict] | None] = ContextVar("queuebot_deliveries", default=None)


@contextmanager
def collect_deliveries():
    pending: list[dict] = []
    token = _deliveries.set(pending)
    try:
        yield pending
    finally:
        _deliveries.reset(token)


def defer_message(kind: str, object_id: int, loudness: int = 0):
    pending = _deliveries.get()
    if pending is None:
        return False
    for job in pending:
        if job["kind"] == kind and job["id"] == object_id:
            job["loudness"] = max(job["loudness"], loudness)
            return True
    pending.append({"kind": kind, "id": object_id, "loudness": loudness})
    return True


def deliver_pending(app, update_id: int):
    # Import here: renderers themselves use defer_message during command execution.
    from bot.bot import Bot
    from bot.queue.utils import updateQueue
    from bot.tic_tac_toe import update_msg
    from data.queue import Queue
    from data.tic_tac_toe import TicTacToe
    from utils.runtime import update_lock

    with update_lock, app.test_request_context(), db_session.create_session() as session, Bot() as bot:
        g.db_session = session
        g.user = UserBase.get_fake_system()
        update = session.get(ProcessedUpdate, update_id)
        if update is None:
            return

        def checkpoint(loudness):
            # Commit new message IDs and delivery progress together. A retry edits
            # an already created card/notification instead of sending another one.
            assert update.deliveries
            update.deliveries = [{**update.deliveries[0], "loudness": loudness}] + update.deliveries[1:]
            session.commit()

        while update.deliveries:
            job = update.deliveries[0]
            if job["kind"] == "queue":
                queue = Queue.get(job["id"], db_sess=session)
                if queue is not None:
                    updateQueue(bot, queue, job["loudness"], checkpoint=checkpoint)
            elif job["kind"] == "game":
                game = TicTacToe.get(job["id"], db_sess=session)
                if game is not None:
                    update_msg(game)
            else:
                raise ValueError(f"Unknown delivery kind: {job['kind']}")
            update.deliveries = update.deliveries[1:] or None
            update.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
