import logging
import tempfile
import unittest
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import bafser_tgapi as tgapi
from bafser import AppConfig, SqlAlchemyBase, create_app, db_session, get_datetime_now
from bafser_tgapi import utils as tgutils
from flask import g
from sqlalchemy import delete, inspect

import bafser_config
from bot.bot import Bot
from data.cache import Cache
from data.processed_update import ProcessedUpdate
from data.queue import Queue
from data.queue_user import QueueUser
from data.tic_tac_toe import TicTacToe
from data.user import User
from data.user_name import UserName
from utils.http import register_http
from utils.maintenance import clear_due_queues, scheduled_at
from utils.updates import process_update
from utils.webhook import configure_webhook


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.ids = defaultdict(int)
        self.admins = set()
        self.left = set()
        self.missing = set()

    @staticmethod
    def sender(user_id):
        return {"id": user_id, "is_bot": False, "first_name": f"User {user_id}", "username": f"u{user_id}"}

    def message(self, chat_id, message_id, text="", user_id=100, thread=None):
        result = {
            "message_id": message_id, "date": 1791720000,
            "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "supergroup"},
            "from": self.sender(user_id), "text": text,
        }
        if thread is not None:
            result.update(message_thread_id=thread, is_topic_message=True)
        return result

    def call(self, method, data=None, **kwargs):
        data = data or {}
        self.calls.append((method, data.copy()))
        if method == "getChatMember":
            key = (data["chat_id"], data["user_id"])
            status = "left" if key in self.left else "administrator" if key in self.admins else "member"
            result = {"status": status, "user": self.sender(data["user_id"])}
        elif method == "sendMessage":
            chat = data["chat_id"]
            self.ids[chat] += 1
            result = self.message(chat, self.ids[chat], data["text"], thread=data.get("message_thread_id"))
        elif method in ("editMessageText", "editMessageReplyMarkup"):
            result = self.message(data["chat_id"], data["message_id"], data.get("text", ""))
        elif method == "setMessageReaction" and (data["chat_id"], data["message_id"]) in self.missing:
            return False, {"description": "Bad Request: message to react not found"}
        else:
            result = True
        return True, {"ok": True, "result": result}


class BotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        for key in ("log_info_path", "log_requests_path", "log_errors_path", "log_frontend_path",
                    "log_dashboard_path", "log_bot_path", "jwt_key_file_path", "images_folder"):
            setattr(bafser_config, key, str(root / key))
        bafser_config.db_dev_path = str(root / "test.db")
        bafser_config.update_lock_path = str(root / "updates.lock")
        bafser_config.ping_token = "test-ping"
        cls.app, run = create_app("test_queuebot", AppConfig(DEV_MODE=True))
        errors = logging.StreamHandler()
        errors.setLevel(logging.ERROR)
        logging.getLogger().addHandler(errors)
        # Apply the distributed initial migration and initialize framework roles.
        run(False)
        with patch("bafser_tgapi.utils.read_config", return_value={
            "bot_token": "test", "bot_name": "@TestQueueBot", "webhook_token": "test", "url": "https://example.com/",
        }), patch("bafser_tgapi.bot.call_async"), patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
            tgapi.setup(botCls=Bot, dev=True)
        register_http(cls.app)

    @classmethod
    def tearDownClass(cls):
        # Dispose pooled SQLite handles before removing the Windows temp folder.
        with db_session.create_session() as session:
            session.get_bind().dispose()
        logging.shutdown()
        cls.temp.cleanup()

    def setUp(self):
        self.api = FakeTelegram()
        self.api.admins.update({(-1001, 100), (-1002, 100)})
        self.call_patch = patch("bafser_tgapi.methods.call", side_effect=self.api.call)
        self.call_patch.start()
        self.addCleanup(self.call_patch.stop)
        self.network_patch = patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden"))
        self.network_patch.start()
        self.addCleanup(self.network_patch.stop)
        with db_session.create_session() as session:
            for name in ("UserName", "ProcessedUpdate", "QueueUser", "Queue", "TicTacToe", "Cache", "Msg", "UserRole", "User"):
                session.execute(delete(SqlAlchemyBase.metadata.tables[name]))
            session.commit()
        self.update_id = 0

    def command(self, text, chat=-1001, user=100, reply=None, thread=None):
        self.update_id += 1
        message = self.api.message(chat, 10000 + self.update_id, text, user, thread)
        if reply is not None:
            message["reply_to_message"] = self.api.message(chat, reply, thread=thread)
        process_update(tgapi.Update.new({"update_id": self.update_id, "message": message}).valid())

    def callback(self, text, chat=-1001, user=101, message=1, thread=None):
        self.update_id += 1
        update = {"update_id": self.update_id, "callback_query": {
            "id": str(self.update_id), "from": self.api.sender(user), "chat_instance": "test",
            "data": text, "message": self.api.message(chat, message, thread=thread),
        }}
        process_update(tgapi.Update.new(update).valid())

    def snapshot(self, cls, object_id):
        with db_session.create_session() as session:
            result = session.get(cls, object_id)
            session.expunge(result)
            return result

    def queue(self, name="Test queue", chat=-1001, user=100, thread=None):
        self.command(f"/queue_new {name}", chat, user, thread=thread)
        with db_session.create_session() as session:
            queue = session.query(Queue).order_by(Queue.id.desc()).first()
            return queue.id, queue.msg.message_id

    def users(self, queue_id):
        with db_session.create_session() as session:
            return [(qu.user.id_tg, qu.block, qu.priority) for qu in
                    session.query(QueueUser).filter_by(queue_id=queue_id).order_by(
                        QueueUser.block, QueueUser.priority, QueueUser.enter_date).all()]

    def webhook(self, update):
        with self.app.test_client() as client, patch.dict(self.app.config, {
            "TESTING": False, "PROPAGATE_EXCEPTIONS": False,
        }):
            return client.post("/webhook", json=update, headers={
                "X-Telegram-Bot-Api-Secret-Token": "test",
            })

    def webhook_callback(self, command, *, user=101, update_id=100):
        return {"update_id": update_id, "callback_query": {
            "id": str(update_id), "from": self.api.sender(user), "chat_instance": "test",
            "data": command, "message": self.api.message(-1001, 1),
        }}

    def fail_edits(self, method, data=None, **kwargs):
        if method == "editMessageText":
            return False, {"description": "temporary Telegram error"}
        return self.api.call(method, data, **kwargs)

    def test_initial_schema_contains_only_requested_app_tables(self):
        with db_session.create_session() as session:
            tables = set(inspect(session.get_bind()).get_table_names())
            self.assertTrue({"Queue", "QueueUser", "TicTacToe", "Cache", "Msg", "alembic_version"} <= tables)
            self.assertFalse({"Curse", "Transaction", "Tagger", "Checker", "Screamer", "Misc"} & tables)

    def test_reply_lookup_isolated_when_message_ids_collide(self):
        first, msg1 = self.queue("First", -1001)
        second, msg2 = self.queue("Second", -1002)
        self.assertEqual(msg1, msg2)
        self.command("/queue_rename Renamed", -1002, reply=msg2)
        self.assertEqual(self.snapshot(Queue, first).name, "First")
        self.assertEqual(self.snapshot(Queue, second).name, "Renamed")

    def test_all_id_callbacks_reject_other_chat(self):
        queue_id, _ = self.queue()
        self.command("/start", user=101)
        with db_session.create_session() as session:
            user_id = User.get_by_id_tg(101, db_sess=session).id
        for command in (f"queue_enter {queue_id}", f"queue_exit {queue_id}", f"queue_pass {queue_id}",
                        f"queue_end {queue_id}", f"queue_kick_cmd + {queue_id} {user_id}",
                        f"queue_set_blocks_cmd + {queue_id} 1", f"queue_set_priorities_cmd + {queue_id} 1"):
            with self.subTest(command=command):
                self.callback(command, chat=-1002, user=100)
                self.assertEqual(self.users(queue_id), [])
                self.assertIn("не найдена", self.api.calls[-1][1]["text"])

    def test_anyone_can_use_private_queue_but_cannot_access_others(self):
        queue_id, message = self.queue(chat=101, user=101)
        self.callback(f"queue_enter {queue_id}", chat=101, user=101, message=message)
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])
        self.callback(f"queue_exit {queue_id}", chat=102, user=102)
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])

    def test_group_management_requires_current_chat_admin(self):
        queue_id, message = self.queue()
        self.command("/queue_clear", user=101, reply=message)
        self.assertIn("администраторы", self.api.calls[-1][1]["text"])
        self.command("/queue_new Forbidden", user=101)
        with db_session.create_session() as session:
            self.assertEqual(session.query(Queue).count(), 1)
        self.assertIsNotNone(self.snapshot(Queue, queue_id))

    def test_join_pass_end_and_exit(self):
        queue_id, _ = self.queue()
        self.callback(f"queue_enter {queue_id}", user=101)
        self.callback(f"queue_enter {queue_id}", user=102)
        self.callback(f"queue_enter {queue_id}", user=101)
        self.assertEqual([u[0] for u in self.users(queue_id)], [101, 102])
        self.callback(f"queue_pass {queue_id}", user=101)
        self.assertEqual([u[0] for u in self.users(queue_id)], [102, 101])
        self.callback(f"queue_end {queue_id}", user=102)
        self.assertEqual([u[0] for u in self.users(queue_id)], [101, 102])
        self.callback(f"queue_exit {queue_id}", user=101)
        self.assertEqual([u[0] for u in self.users(queue_id)], [102])

    def test_blocks_priorities_and_overflow(self):
        queue_id, message = self.queue(thread=22)
        self.command("/queue_set_blocks\nMorning | AM\nEvening | PM", reply=message, thread=22)
        self.command("/queue_set_priorities\nUrgent\nNormal", reply=message, thread=22)
        self.command("/queue_set_max_in_block 1", reply=message, thread=22)
        self.callback(f"queue_enter {queue_id} 0 1", user=101, thread=22)
        self.callback(f"queue_enter {queue_id} 0 0", user=102, thread=22)
        self.assertEqual(self.users(queue_id), [(102, 0, 0), (101, 1, 1)])
        for method, data in self.api.calls:
            if method == "sendMessage":
                self.assertEqual(data["chat_id"], -1001)
                self.assertEqual(data.get("message_thread_id"), 22)

    def test_simultaneous_callbacks_create_single_entry_per_user(self):
        queue_id, _ = self.queue()
        updates = []
        for i in range(12):
            user = 101 + i % 3
            updates.append(tgapi.Update.new({"update_id": 200 + i, "callback_query": {
                "id": str(200 + i), "from": self.api.sender(user), "chat_instance": "test",
                "data": f"queue_enter {queue_id}", "message": self.api.message(-1001, 1),
            }}).valid())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(process_update, updates))
        self.assertEqual(sorted(u[0] for u in self.users(queue_id)), [101, 102, 103])

    def test_add_another_user_requires_admin_and_membership(self):
        queue_id, message = self.queue()
        self.command("/start", user=102)
        with db_session.create_session() as session:
            user_id = User.get_by_id_tg(102, db_sess=session).id
        self.callback(f"queue_enter {queue_id} 0 0 {user_id}", user=101)
        self.assertEqual(self.users(queue_id), [])
        self.api.left.add((-1001, 102))
        self.command("/queue_add @u102", reply=message)
        self.assertEqual(self.users(queue_id), [])
        self.api.left.clear()
        self.command("/queue_add @u102", reply=message)
        self.assertEqual(self.users(queue_id), [(102, 0, 0)])

    def test_set_deduplicates_aliases_without_partial_changes(self):
        queue_id, message = self.queue()
        self.command("/start", user=101)
        self.command("/queue_set @u101 u101", reply=message)
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])
        self.command("/queue_set @u100 @unknown", reply=message)
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])

    def test_nonmember_cannot_join_queue_from_public_message(self):
        queue_id, _ = self.queue()
        self.api.left.add((-1001, 101))
        self.callback(f"queue_enter {queue_id}", user=101)
        self.assertEqual(self.users(queue_id), [])
        self.callback(f"queue_end {queue_id}", user=101)
        self.assertEqual(self.users(queue_id), [])

    def test_confirmation_cache_bound_to_queue_action_and_expiry(self):
        first, _ = self.queue()
        second, _ = self.queue(chat=-1002)
        with self.app.test_request_context(), db_session.create_session() as session:
            g.db_session = session
            cid = Cache.put(["One"], list[str], queue_id=first, action="blocks")
            self.assertIsNone(Cache.pop(cid, list[str], queue_id=second, action="blocks"))
            self.assertIsNone(Cache.pop(cid, list[str], queue_id=first, action="priorities"))
            self.assertEqual(Cache.pop(cid, list[str], queue_id=first, action="blocks"), ["One"])
            self.assertIsNone(Cache.pop(cid, list[str], queue_id=first, action="blocks"))
            cid = Cache.put(["Expired"], list[str], queue_id=first, action="blocks")
            session.get(Cache, cid).fresh_until = get_datetime_now() - timedelta(hours=2)
            session.commit()
            self.assertIsNone(Cache.pop(cid, list[str], queue_id=first, action="blocks"))

    def test_confirmation_buttons_apply_and_cancel_in_correct_chat(self):
        queue_id, message = self.queue()
        self.command("/queue_set_blocks\nOne\nTwo", reply=message)
        self.command("/queue_set_blocks\nOnly", reply=message)
        prompt = [data for method, data in self.api.calls if method == "sendMessage"][-1]
        data = prompt["reply_markup"].inline_keyboard[0][0].callback_data
        self.callback(data, user=100)
        self.assertEqual(self.snapshot(Queue, queue_id).blocks, ["Only"])

    def test_silent_multiline_settings_exclude_suffix(self):
        queue_id, message = self.queue()
        self.command("/queue_set_blocks\nOne\nTwo \\s", reply=message)
        self.assertEqual(self.snapshot(Queue, queue_id).blocks, ["One", "Two"])

    def test_force_update_keeps_foreign_keys_and_topic(self):
        queue_id, message = self.queue(thread=24)
        self.callback(f"queue_enter {queue_id}", user=101, thread=24)
        self.command("/queue_force_update", reply=message, thread=24)
        with db_session.create_session() as session:
            queue = session.get(Queue, queue_id)
            self.assertNotEqual(queue.msg.message_id, message)
            self.assertEqual(queue.msg.message_thread_id, 24)
            self.assertEqual(queue.msg_next.message_thread_id, 24)

    def test_weekly_cleanup_timezone_restart_and_idempotence(self):
        queue_id, _ = self.queue(thread=20)
        other, _ = self.queue(chat=-1002)
        self.callback(f"queue_enter {queue_id}", user=101, thread=20)
        self.callback(f"queue_enter {other}", chat=-1002, user=102)
        now = datetime(2026, 10, 12, 9, 1, tzinfo=timezone.utc)  # Monday 12:01 Moscow
        with db_session.create_session() as session:
            queue = session.get(Queue, queue_id)
            queue.clear_at = 11200
            queue.cleared_at = datetime(2026, 10, 5, 9, 0)
            session.commit()
        clear_due_queues(self.app, now)
        self.assertEqual(self.users(queue_id), [])
        self.assertEqual(self.users(other), [(102, 0, 0)])
        self.callback(f"queue_enter {queue_id}", user=101, thread=20)
        clear_due_queues(self.app, now + timedelta(minutes=1))
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])
        clear_due_queues(self.app, now + timedelta(days=7))
        self.assertEqual(self.users(queue_id), [])
        notifications = [data for method, data in self.api.calls if method == "sendMessage" and "по расписанию" in data["text"]]
        self.assertEqual(len(notifications), 2)
        self.assertTrue(all(d["chat_id"] == -1001 and d["message_thread_id"] == 20 for d in notifications))

    def test_cleanup_deleted_message_retires_only_its_queue(self):
        queue_id, message = self.queue()
        with db_session.create_session() as session:
            queue = session.get(Queue, queue_id)
            queue.clear_at = 10000
            queue.cleared_at = datetime(2000, 1, 1)
            session.commit()
        self.api.missing.add((-1001, message))
        clear_due_queues(self.app, datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc))
        self.assertTrue(self.snapshot(Queue, queue_id).deleted)

    def test_cleanup_notification_failure_never_clears_new_entries_again(self):
        queue_id, _ = self.queue()
        self.callback(f"queue_enter {queue_id}", user=101)
        now = datetime(2026, 10, 12, 9, 1, tzinfo=timezone.utc)
        with db_session.create_session() as session:
            queue = session.get(Queue, queue_id)
            queue.clear_at = 11200
            queue.cleared_at = datetime(2026, 10, 5, 9, 0)
            session.commit()

        def fail_notification(method, data=None, **kwargs):
            if method == "sendMessage" and "по расписанию" in data["text"]:
                return False, {"description": "temporary delivery error"}
            return self.api.call(method, data, **kwargs)

        with patch("bafser_tgapi.methods.call", side_effect=fail_notification), \
                self.assertLogs("utils.maintenance", level="ERROR"):
            clear_due_queues(self.app, now)
        self.assertEqual(self.users(queue_id), [])
        self.callback(f"queue_enter {queue_id}", user=101)
        clear_due_queues(self.app, now + timedelta(minutes=1))
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])

    def test_schedule_exact_minutes_and_reset(self):
        queue_id, message = self.queue()
        self.command("/queue_set_clear_at Вс 23:59", reply=message)
        self.assertEqual(self.snapshot(Queue, queue_id).clear_at, 72359)
        self.command("/queue_set_clear_at reset", reply=message)
        self.assertIsNone(self.snapshot(Queue, queue_id).clear_at)

    def test_schedule_handles_midnight_and_other_timezone(self):
        queue = Queue(msg_id=1, name="Test", clear_at=10000)
        with patch.object(bafser_config, "queue_timezone", "Asia/Tokyo"):
            due = scheduled_at(queue, datetime(2026, 10, 11, 15, 1, tzinfo=timezone.utc))
        self.assertEqual(due, datetime(2026, 10, 11, 15, 0))

    def test_game_requires_acceptance_and_correct_chat_players_and_turn(self):
        self.command("/start", user=101)
        self.command("/tic_tac_toe @u101")
        with db_session.create_session() as session:
            game_id = session.query(TicTacToe).one().id
        self.callback(f"tic_tac_toe_turn {game_id} 0 0", user=101)
        self.assertEqual(self.snapshot(TicTacToe, game_id).field, "000000000")
        self.callback(f"tic_tac_toe_join {game_id}", user=102)
        self.assertFalse(self.snapshot(TicTacToe, game_id).started)
        self.callback(f"tic_tac_toe_join {game_id}", user=101, chat=-1002)
        self.assertFalse(self.snapshot(TicTacToe, game_id).started)
        self.callback(f"tic_tac_toe_join {game_id}", user=101)
        self.assertTrue(self.snapshot(TicTacToe, game_id).started)
        self.callback(f"tic_tac_toe_turn {game_id} 0 0", user=100)
        self.assertEqual(self.snapshot(TicTacToe, game_id).field, "000000000")
        self.callback(f"tic_tac_toe_turn {game_id} 0 0", user=101)
        self.callback(f"tic_tac_toe_turn {game_id} 0 1", user=100)
        self.callback(f"tic_tac_toe_turn {game_id} 1 0", user=101)
        self.callback(f"tic_tac_toe_turn {game_id} 1 1", user=100)
        self.callback(f"tic_tac_toe_turn {game_id} 2 0", user=101)
        self.assertEqual(self.snapshot(TicTacToe, game_id).get_status(), ("winner", 2))
        self.callback(f"tic_tac_toe_join {game_id}", user=102)
        self.assertEqual(self.snapshot(TicTacToe, game_id).field, "222110000")

    def test_game_open_challenge_rejects_self(self):
        self.command("/tic_tac_toe")
        with db_session.create_session() as session:
            game_id = session.query(TicTacToe).one().id
        self.callback(f"tic_tac_toe_join {game_id}", user=100)
        self.assertFalse(self.snapshot(TicTacToe, game_id).started)
        self.callback(f"tic_tac_toe_join {game_id}", user=101)
        self.assertTrue(self.snapshot(TicTacToe, game_id).started)

    def test_webhook_is_synchronous_and_deduplicates_completed_updates(self):
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/queue_new Webhook queue")}
        with self.app.test_client() as client, patch("threading.Thread.start", side_effect=AssertionError("background thread forbidden")):
            response = client.post("/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "test"})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            repeated = client.post("/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "test"})
            self.assertEqual(repeated.status_code, 200)
        with db_session.create_session() as session:
            self.assertEqual(session.query(Queue).count(), 1)
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 1)

    def test_webhook_recovers_game_join_without_repeating_command(self):
        self.command("/tic_tac_toe")
        with db_session.create_session() as session:
            game_id = session.query(TicTacToe).one().id
        update = self.webhook_callback(f"tic_tac_toe_join {game_id}")
        with patch("bafser_tgapi.methods.call", side_effect=self.fail_edits), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        self.assertTrue(self.snapshot(TicTacToe, game_id).started)
        with db_session.create_session() as session:
            self.assertEqual(session.get(ProcessedUpdate, 100).deliveries[0]["kind"], "game")
        self.api.calls.clear()
        with patch("utils.http.process_update", side_effect=AssertionError("command must not run twice")), \
                patch("threading.Thread.start", side_effect=AssertionError("no background tasks")):
            self.assertEqual(self.webhook(update).status_code, 200)
        edit = next(data for method, data in self.api.calls if method == "editMessageText")
        self.assertEqual(len(edit["reply_markup"].inline_keyboard), 3)
        self.assertIn("Ходит", edit["text"])
        self.assertEqual(self.snapshot(TicTacToe, game_id).field, "000000000")
        with db_session.create_session() as session:
            self.assertIsNone(session.get(ProcessedUpdate, 100).deliveries)
        self.api.calls.clear()
        self.assertEqual(self.webhook(update).status_code, 200)
        self.assertEqual(self.api.calls, [])

    def test_webhook_recovers_game_move_using_latest_board(self):
        self.command("/tic_tac_toe")
        with db_session.create_session() as session:
            game_id = session.query(TicTacToe).one().id
        self.callback(f"tic_tac_toe_join {game_id}")
        update = self.webhook_callback(f"tic_tac_toe_turn {game_id} 0 0")
        with patch("bafser_tgapi.methods.call", side_effect=self.fail_edits), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        self.assertEqual(self.snapshot(TicTacToe, game_id).field, "200000000")
        self.callback(f"tic_tac_toe_turn {game_id} 1 0", user=100)
        self.api.calls.clear()
        with patch("utils.http.process_update", side_effect=AssertionError("move must not run twice")):
            self.assertEqual(self.webhook(update).status_code, 200)
        self.assertEqual(self.snapshot(TicTacToe, game_id).field, "210000000")
        edit = next(data for method, data in self.api.calls if method == "editMessageText")
        self.assertNotEqual(edit["reply_markup"].inline_keyboard[0][0].text, "〰")
        self.assertNotEqual(edit["reply_markup"].inline_keyboard[0][1].text, "〰")

    def test_webhook_recovers_game_invitation_without_creating_another_game(self):
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/tic_tac_toe")}

        def fail_markup(method, data=None, **kwargs):
            if method == "editMessageReplyMarkup":
                return False, {"description": "temporary Telegram error"}
            return self.api.call(method, data, **kwargs)

        with patch("bafser_tgapi.methods.call", side_effect=fail_markup), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        self.assertEqual(self.webhook(update).status_code, 200)
        with db_session.create_session() as session:
            self.assertEqual(session.query(TicTacToe).count(), 1)
            self.assertFalse(session.query(TicTacToe).one().started)
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 1)
        self.assertEqual(sum(method == "editMessageReplyMarkup" for method, _ in self.api.calls), 1)

    def test_webhook_recovers_queue_join_and_notification_across_repeated_failures(self):
        queue_id, _ = self.queue()
        self.api.calls.clear()
        update = self.webhook_callback(f"queue_enter {queue_id}")
        with patch("bafser_tgapi.methods.call", side_effect=self.fail_edits), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
            self.assertEqual(self.webhook(update).status_code, 500)
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])
        with db_session.create_session() as session:
            entry = session.query(QueueUser).filter_by(queue_id=queue_id).one().enter_date
            notification = session.get(Queue, queue_id).msg_next.message_id
        with patch("utils.http.process_update", side_effect=AssertionError("join must not run twice")):
            self.assertEqual(self.webhook(update).status_code, 200)
        with db_session.create_session() as session:
            self.assertEqual(session.query(QueueUser).filter_by(queue_id=queue_id).one().enter_date, entry)
            self.assertEqual(session.get(Queue, queue_id).msg_next.message_id, notification)
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 1)
        self.assertTrue(any("User 101" in data["text"] for method, data in self.api.calls if method == "editMessageText"))

    def test_webhook_recovers_queue_pass_without_passing_second_person(self):
        queue_id, _ = self.queue()
        for user in (101, 102, 103):
            self.callback(f"queue_enter {queue_id}", user=user)
        self.api.calls.clear()
        update = self.webhook_callback(f"queue_pass {queue_id}")
        with patch("bafser_tgapi.methods.call", side_effect=self.fail_edits), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        expected = [(102, 0, 0), (101, 0, 0), (103, 0, 0)]
        self.assertEqual(self.users(queue_id), expected)
        self.assertEqual(self.webhook(update).status_code, 200)
        self.assertEqual(self.users(queue_id), expected)
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 1)

    def test_webhook_recovers_queue_creation_without_creating_another_queue(self):
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/queue_new Once")}
        with patch("bafser_tgapi.methods.call", side_effect=self.fail_edits), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        self.assertEqual(self.webhook(update).status_code, 200)
        with db_session.create_session() as session:
            self.assertEqual(session.query(Queue).count(), 1)
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 1)

    def test_webhook_recovers_force_update_without_recreating_card_again(self):
        queue_id, original = self.queue()
        self.callback(f"queue_enter {queue_id}")
        message = self.api.message(-1001, 50, "/queue_force_update")
        message["reply_to_message"] = self.api.message(-1001, original)
        update = {"update_id": 100, "message": message}
        self.api.calls.clear()
        with patch("bafser_tgapi.methods.call", side_effect=self.fail_edits), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        with db_session.create_session() as session:
            recreated = session.get(Queue, queue_id).msg.message_id
        self.assertNotEqual(recreated, original)
        self.assertEqual(self.webhook(update).status_code, 200)
        with db_session.create_session() as session:
            self.assertEqual(session.get(Queue, queue_id).msg.message_id, recreated)
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 2)

    def test_webhook_treats_unchanged_game_message_as_delivered(self):
        self.command("/tic_tac_toe")
        with db_session.create_session() as session:
            game_id = session.query(TicTacToe).one().id
        update = self.webhook_callback(f"tic_tac_toe_join {game_id}")

        def already_updated(method, data=None, **kwargs):
            if method == "editMessageText":
                return False, {"description": "Bad Request: message is not modified"}
            return self.api.call(method, data, **kwargs)

        with patch("bafser_tgapi.methods.call", side_effect=already_updated):
            self.assertEqual(self.webhook(update).status_code, 200)
        self.assertTrue(self.snapshot(TicTacToe, game_id).started)
        with db_session.create_session() as session:
            self.assertIsNone(session.get(ProcessedUpdate, 100).deliveries)

    def test_webhook_resumes_only_unfinished_messages_in_multi_queue_update(self):
        first, _ = self.queue("First")
        second, second_message = self.queue("Second")
        for queue_id in (first, second):
            self.callback(f"queue_enter {queue_id}")
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/user_name @u101 Alice")}

        def fail_second_queue(method, data=None, **kwargs):
            if method == "editMessageText" and data["message_id"] == second_message:
                return False, {"description": "temporary Telegram error"}
            return self.api.call(method, data, **kwargs)

        with patch("bafser_tgapi.methods.call", side_effect=fail_second_queue), self.assertLogs(level="ERROR"):
            self.assertEqual(self.webhook(update).status_code, 500)
        with db_session.create_session() as session:
            self.assertEqual(session.get(ProcessedUpdate, 100).deliveries, [{
                "kind": "queue", "id": second, "loudness": 1,
            }])
        self.api.calls.clear()
        with patch("utils.http.process_update", side_effect=AssertionError("name command must not run twice")):
            self.assertEqual(self.webhook(update).status_code, 200)
        with db_session.create_session() as session:
            queue = session.get(Queue, second)
            expected_messages = {queue.msg.message_id, queue.msg_next.message_id}
        edits = [data for method, data in self.api.calls if method == "editMessageText"]
        self.assertEqual({data["message_id"] for data in edits}, expected_messages)
        self.assertTrue(all("Alice" in data["text"] for data in edits))

    def test_ping_preserves_old_unfinished_deliveries(self):
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
        with db_session.create_session() as session:
            session.add(ProcessedUpdate(update_id=100, processed_at=old, deliveries=[{
                "kind": "game", "id": 1, "loudness": 0,
            }]))
            session.commit()
        with self.app.test_client() as client:
            self.assertEqual(client.get("/api/ping?token=test-ping").status_code, 200)
        with db_session.create_session() as session:
            self.assertIsNotNone(session.get(ProcessedUpdate, 100))

    def test_webhook_rejects_missing_wrong_secret_and_invalid_updates(self):
        with self.app.test_client() as client:
            self.assertEqual(client.post("/webhook", json={"update_id": 1}).status_code, 403)
            self.assertEqual(client.post("/webhook", json={"update_id": 1}, headers={
                "X-Telegram-Bot-Api-Secret-Token": "wrong",
            }).status_code, 403)
            headers = {"X-Telegram-Bot-Api-Secret-Token": "test"}
            self.assertEqual(client.post("/webhook", data="text", headers=headers).status_code, 415)
            self.assertEqual(client.post("/webhook", json=[1], headers=headers).status_code, 400)
            self.assertEqual(client.post("/webhook", json={"message": {}}, headers=headers).status_code, 400)
        self.assertEqual(self.api.calls, [])

    def test_webhook_callback_uses_sender_session_and_current_user(self):
        queue_id, _ = self.queue()
        update = {"update_id": 100, "callback_query": {
            "id": "100", "from": self.api.sender(101), "chat_instance": "test",
            "data": f"queue_enter {queue_id}", "message": self.api.message(-1001, 1),
        }}
        with self.app.test_client() as client:
            response = client.post("/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "test"})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.users(queue_id), [(101, 0, 0)])

    def test_concurrent_webhook_retries_create_one_queue(self):
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/queue_new Once")}

        def receive(_):
            with self.app.test_client() as client:
                return client.post("/webhook", json=update, headers={
                    "X-Telegram-Bot-Api-Secret-Token": "test",
                }).status_code

        with ThreadPoolExecutor(max_workers=3) as executor:
            self.assertEqual(list(executor.map(receive, range(3))), [200] * 3)
        with db_session.create_session() as session:
            self.assertEqual(session.query(Queue).count(), 1)
            self.assertEqual(session.query(ProcessedUpdate).count(), 1)
        self.assertEqual(sum(method == "sendMessage" for method, _ in self.api.calls), 1)

    def test_webhook_and_ping_reject_empty_placeholder_and_unicode_secrets(self):
        with self.app.test_client() as client:
            self.assertEqual(client.post("/webhook", json={"update_id": 1}, headers={
                "X-Telegram-Bot-Api-Secret-Token": "неверный",
            }).status_code, 403)
            for token in ("", "PUT_RANDOM_SECRET_HERE", "unused-in-polling-mode"):
                with patch.object(tgutils, "webhook_token", token):
                    self.assertEqual(client.post("/webhook", json={"update_id": 1}, headers={
                        "X-Telegram-Bot-Api-Secret-Token": token,
                    }).status_code, 403)
            with patch.object(bafser_config, "ping_token", ""):
                self.assertEqual(client.get("/api/ping?token=test-ping").status_code, 403)
            self.assertEqual(client.get("/api/ping?token=неверный").status_code, 403)

    def test_webhook_registration_rejects_insecure_configuration_and_api_errors(self):
        for token in ("", "PUT_RANDOM_SECRET_HERE", "bad secret", "a" * 257):
            with patch.object(tgutils, "webhook_token", token), self.assertRaises(ValueError):
                configure_webhook()
        with patch.object(tgutils, "url", "http://example.com/"), self.assertRaises(ValueError):
            configure_webhook()
        with patch.object(tgutils, "call", return_value=(False, {"description": "bad URL"})), self.assertRaises(RuntimeError):
            configure_webhook()

    def test_ping_expires_only_old_webhook_receipts(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with db_session.create_session() as session:
            session.add_all([
                ProcessedUpdate(update_id=1, processed_at=now - timedelta(days=8)),
                ProcessedUpdate(update_id=2, processed_at=now - timedelta(days=6)),
            ])
            session.commit()
        with self.app.test_client() as client:
            self.assertEqual(client.get("/api/ping?token=test-ping").status_code, 200)
        with db_session.create_session() as session:
            self.assertIsNone(session.get(ProcessedUpdate, 1))
            self.assertIsNotNone(session.get(ProcessedUpdate, 2))

    def test_webhook_failure_returns_500_without_acknowledging_update(self):
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/queue_new Webhook queue")}
        with self.app.test_client() as client, patch("utils.http.process_update", side_effect=RuntimeError("processing failed")), \
                patch.dict(self.app.config, {"TESTING": False, "PROPAGATE_EXCEPTIONS": False}), self.assertLogs(level="ERROR"):
            response = client.post("/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "test"})
            self.assertEqual(response.status_code, 500)
        with db_session.create_session() as session:
            self.assertEqual(session.query(SqlAlchemyBase.metadata.tables["ProcessedUpdate"]).count(), 0)

    def test_ping_requires_secret_and_clears_queues_inside_request(self):
        queue_id, _ = self.queue()
        self.callback(f"queue_enter {queue_id}", user=101)
        with db_session.create_session() as session:
            queue = session.get(Queue, queue_id)
            queue.clear_at = 10000
            queue.cleared_at = datetime(2000, 1, 1)
            session.commit()
        with self.app.test_client() as client, patch("threading.Thread.start", side_effect=AssertionError("background thread forbidden")):
            self.assertEqual(client.get("/api/ping").status_code, 403)
            self.assertEqual(self.users(queue_id), [(101, 0, 0)])
            self.assertEqual(client.get("/api/ping?token=wrong").status_code, 403)
            self.assertEqual(client.get("/api/ping?token=test-ping").status_code, 200)
            self.assertEqual(client.post("/api/ping", headers={"X-Queuebot-Ping-Token": "test-ping"}).status_code, 200)
        self.assertEqual(self.users(queue_id), [])

    def test_group_names_are_scoped_and_refresh_queue_notifications(self):
        first, _ = self.queue()
        second, _ = self.queue(chat=-1002)
        self.callback(f"queue_enter {first}", user=101)
        self.callback(f"queue_enter {second}", user=101, chat=-1002)
        self.api.calls.clear()
        self.command("/user_name @u101 Alice Smith")
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertEqual(user.get_name(-1001), "Alice Smith")
            self.assertEqual(user.get_name(-1002), "User 101")
            self.assertEqual(user.get_name(), "User 101")
            self.assertEqual(user.get_username(), "u101")
        edits = [data for method, data in self.api.calls if method == "editMessageText"]
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(data["chat_id"] == -1001 and "Alice Smith" in data["text"] for data in edits))
        self.command("/user_name_reset @u101")
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertIsNone(UserName.get(-1001, user.id, db_sess=session))

    def test_group_name_change_requires_admin_and_target_membership(self):
        self.command("/start", user=101)
        self.command("/user_name @u101 Not allowed", user=102)
        self.api.left.add((-1001, 101))
        self.command("/user_name @u101 Not member")
        with db_session.create_session() as session:
            self.assertEqual(session.query(UserName).count(), 0)

    def test_group_name_saved_when_telegram_cannot_refresh_messages(self):
        queue_id, _ = self.queue()
        self.callback(f"queue_enter {queue_id}", user=101)
        self.api.calls.clear()

        def fail_edits(method, data=None, **kwargs):
            if method == "editMessageText":
                return False, {"description": "temporary Telegram error"}
            return self.api.call(method, data, **kwargs)

        with patch("bafser_tgapi.methods.call", side_effect=fail_edits), self.assertLogs("bot.user_names", level="ERROR"):
            self.command("/user_name @u101 Alice")
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertEqual(user.get_name(-1001), "Alice")
        self.assertIn("Часть сообщений не обновилась", self.api.calls[-1][1]["text"])

    def test_group_name_change_via_webhook_and_reset_by_reply(self):
        queue_id, _ = self.queue()
        self.callback(f"queue_enter {queue_id}", user=101)
        update = {"update_id": 100, "message": self.api.message(-1001, 50, "/user_name @u101 Alice")}
        with self.app.test_client() as client:
            self.assertEqual(client.post("/webhook", json=update, headers={
                "X-Telegram-Bot-Api-Secret-Token": "test",
            }).status_code, 200)
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertEqual(user.get_name(-1001), "Alice")
        message = self.api.message(-1001, 52, "/user_name_reset")
        message["reply_to_message"] = self.api.message(-1001, 51, "hello", user_id=101)
        process_update(tgapi.Update.new({"update_id": 101, "message": message}).valid())
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertIsNone(UserName.get(-1001, user.id, db_sess=session))

    def test_group_name_reply_supports_user_without_username(self):
        self.update_id += 1
        message = self.api.message(-1001, 50, "/user_name Alice without username")
        message["reply_to_message"] = self.api.message(-1001, 49, "hello", user_id=101)
        message["reply_to_message"]["from"].pop("username")
        process_update(tgapi.Update.new({"update_id": self.update_id, "message": message}).valid())
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertEqual(user.get_name(-1001), "Alice without username")
            self.assertEqual(user.username, "")

    def test_group_name_updates_active_game(self):
        self.command("/tic_tac_toe")
        with db_session.create_session() as session:
            game_id = session.query(TicTacToe).one().id
        self.callback(f"tic_tac_toe_join {game_id}", user=101)
        self.api.calls.clear()
        self.command("/user_name @u101 Alice")
        texts = [data["text"] for method, data in self.api.calls if method == "editMessageText"]
        self.assertEqual(len(texts), 1)
        self.assertIn("Alice", texts[0])

    def test_invalid_display_name_does_not_change_existing_name(self):
        self.command("/start", user=101)
        self.command("/user_name @u101 Alice")
        for name in ("", "a" * 129, "First\nSecond"):
            self.command("/user_name @u101 " + name)
        self.command("/user_name_reset @u101 Unexpected")
        with db_session.create_session() as session:
            user = User.get_by_id_tg(101, db_sess=session)
            self.assertEqual(user.get_name(-1001), "Alice")


if __name__ == "__main__":
    unittest.main()
