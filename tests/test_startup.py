import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class StartupTests(unittest.TestCase):
    def run_isolated(self, body):
        prefix = '''
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import bafser_config
root = Path(os.environ["QUEUEBOT_TEST_ROOT"])
for key in ("log_info_path", "log_requests_path", "log_errors_path", "log_frontend_path",
            "log_dashboard_path", "log_bot_path", "jwt_key_file_path", "images_folder"):
    setattr(bafser_config, key, str(root / key))
bafser_config.update_lock_path = str(root / "updates.lock")
bafser_config.ping_token = "test-ping"
bafser_config.config_path = str(root / "config.txt")
Path(bafser_config.config_path).write_text(
    "bot_token = fake\\nbot_name = @TestQueueBot\\nwebhook_token = test-secret\\nurl = https://example.com/\\n")
'''
        cleanup = '''
from bafser import db_session
with db_session.create_session() as session:
    session.get_bind().dispose()
logging.shutdown()
'''
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            environment.update(DBPATH=str(Path(temporary) / "production.db"), DB_MYSQL="0",
                               QUEUEBOT_TEST_ROOT=temporary, QUEUE_TIMEZONE="UTC", BOT_MODE="polling",
                               PYTHONIOENCODING="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", prefix + body + cleanup], cwd=Path(__file__).resolve().parents[1],
                env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wsgi_import_and_requests_work_without_threads_or_network(self):
        self.run_isolated('''
from bafser import db_session
from sqlalchemy import inspect
with patch("threading.Thread.start", side_effect=AssertionError("background task forbidden")), \\
     patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
    project = Path.cwd()
    sys.path.insert(0, str(project))
    os.chdir(root)
    import wsgi
    assert Path.cwd() == project
    client = wsgi.application.test_client()
    assert client.post("/webhook", json={"update_id": 123}, headers={
        "X-Telegram-Bot-Api-Secret-Token": "test-secret",
    }).status_code == 200
    assert client.get("/api/ping?token=test-ping").status_code == 200
    assert client.get("/api/ping").status_code == 403
with db_session.create_session() as session:
    assert {"Queue", "UserName", "ProcessedUpdate"} <= set(inspect(session.get_bind()).get_table_names())
    assert session.execute(__import__("sqlalchemy").text("SELECT update_id FROM ProcessedUpdate")).scalar() == 123
''')

    def test_standalone_webhook_configures_and_serves_without_polling(self):
        self.run_isolated('''
import main
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_bot import FakeTelegram
api = FakeTelegram()
def idle_maintenance(app, stop):
    stop.wait()
with patch("sys.argv", ["main.py", "webhook"]), \\
     patch("bafser_tgapi.utils.call", return_value=(True, {})) as configure, \\
     patch("bafser_tgapi.methods.call", side_effect=api.call), \\
     patch("main.tgapi.deleteWebhook", side_effect=AssertionError("deleteWebhook forbidden")), \\
     patch("main.tgapi.run_long_polling", side_effect=AssertionError("polling forbidden")), \\
     patch("main.maintenance_loop", side_effect=idle_maintenance), \\
     patch("waitress.serve") as serve, \\
     patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
    main.main()
    configure.assert_called_once_with("setWebhook", {
        "url": "https://example.com/webhook", "secret_token": "test-secret",
        "max_connections": 1, "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    })
    serve.assert_called_once()
    assert serve.call_args.kwargs == {"host": bafser_config.bot_host, "port": bafser_config.bot_port}
    assert serve.call_args.args[0].test_client().get("/api/ping").status_code == 403
menus = [data for method, data in api.calls if method == "setMyCommands"]
assert len(menus) == 2
assert any(command.command == "user_name" for command in menus[1]["commands"])
assert not any(command.command == "user_name" for command in menus[0]["commands"])
''')

    def test_one_time_webhook_registration_has_no_background_tasks(self):
        self.run_isolated('''
import main
with patch("sys.argv", ["main.py", "set-webhook"]), \\
     patch("main.configure_webhook") as webhook, patch("main.publish_commands") as commands, \\
     patch("threading.Thread.start", side_effect=AssertionError("background task forbidden")), \\
     patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
    main.main()
    webhook.assert_called_once()
    commands.assert_called_once()
''')

    def test_fresh_wsgi_worker_recovers_persisted_delivery_without_repeating_action(self):
        self.run_isolated('''
import subprocess
import main
from bafser import db_session
from data.processed_update import ProcessedUpdate
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_bot import FakeTelegram
api = FakeTelegram()
api.admins.add((-1001, 100))
app = main.create_application()
app.config["PROPAGATE_EXCEPTIONS"] = False
update = {"update_id": 100, "message": api.message(-1001, 50, "/queue_new Once")}
def fail_edit(method, data=None, **kwargs):
    if method == "editMessageText":
        return False, {"description": "temporary Telegram error"}
    return api.call(method, data, **kwargs)
with patch("bafser_tgapi.methods.call", side_effect=fail_edit), \\
     patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
    response = app.test_client().post("/webhook", json=update, headers={
        "X-Telegram-Bot-Api-Secret-Token": "test-secret",
    })
    assert response.status_code == 500
with db_session.create_session() as session:
    assert session.get(ProcessedUpdate, 100).deliveries
worker = """
import os
import sys
from pathlib import Path
from unittest.mock import patch
import bafser_config
root = Path(os.environ['QUEUEBOT_TEST_ROOT'])
for key in ('log_info_path', 'log_requests_path', 'log_errors_path', 'log_frontend_path',
            'log_dashboard_path', 'log_bot_path', 'jwt_key_file_path', 'images_folder'):
    setattr(bafser_config, key, str(root / key))
bafser_config.config_path = str(root / 'config.txt')
bafser_config.update_lock_path = str(root / 'updates.lock')
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_bot import FakeTelegram
api = FakeTelegram()
with patch('threading.Thread.start', side_effect=AssertionError('background task forbidden')), \\
     patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')), \\
     patch('bafser_tgapi.methods.call', side_effect=api.call), \\
     patch('utils.http.process_update', side_effect=AssertionError('action must not run twice')):
    import wsgi
    response = wsgi.application.test_client().post('/webhook', json={
        'update_id': 100, 'message': api.message(-1001, 50, '/queue_new Once'),
    }, headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'})
    assert response.status_code == 200
    assert [method for method, _ in api.calls] == ['editMessageText']
from bafser import db_session
from data.queue import Queue
from data.processed_update import ProcessedUpdate
with db_session.create_session() as session:
    assert session.query(Queue).count() == 1
    assert session.get(ProcessedUpdate, 100).deliveries is None
    session.get_bind().dispose()
import logging
logging.shutdown()
"""
result = subprocess.run([sys.executable, "-c", worker], capture_output=True, text=True,
                        encoding="utf-8", timeout=20)
assert result.returncode == 0, result.stdout + result.stderr
''')

    def test_upgrade_preserves_existing_queues_and_users(self):
        self.run_isolated('''
import sqlite3
from contextlib import closing
from alembic import command
from bafser.alembic import create_alembic_config

cfg = create_alembic_config(False)
command.upgrade(cfg, "9f42dc56b8b5")
with closing(sqlite3.connect(bafser_config.db_path)) as connection:
    connection.execute("INSERT INTO Msg (id,message_id,chat_id,text) VALUES (1,1,-1001,'Queue')")
    connection.execute("INSERT INTO Queue (id,msg_id,name) VALUES (1,1,'Existing')")
    connection.execute("""INSERT INTO User
        (id,id_tg,is_bot,first_name,last_name,username,language_code,login,password,name)
        VALUES (100,100,0,'Alice','','alice','en','alice','','Alice')""")
    connection.execute("""INSERT INTO QueueUser (queue_id,user_id,block,priority,enter_date)
        VALUES (1,100,0,0,'2026-10-11 12:00:00')""")
    connection.commit()
command.upgrade(cfg, "17a58065a1ed")
with closing(sqlite3.connect(bafser_config.db_path)) as connection:
    connection.execute("INSERT INTO ProcessedUpdate (update_id,processed_at) VALUES (7,'2026-10-11 12:00:00')")
    connection.commit()
import main
with patch("threading.Thread.start", side_effect=AssertionError("background task forbidden")), \\
     patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
    main.create_application()
with closing(sqlite3.connect(bafser_config.db_path)) as connection:
    assert connection.execute("SELECT name FROM Queue WHERE id=1").fetchone() == ('Existing',)
    assert connection.execute("SELECT user_id FROM QueueUser WHERE queue_id=1").fetchone() == (100,)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ('b64128dc67ef',)
    assert connection.execute("SELECT COUNT(*) FROM UserName").fetchone() == (0,)
    assert connection.execute("SELECT deliveries FROM ProcessedUpdate WHERE update_id=7").fetchone() == (None,)
''')

    def test_production_entrypoint_initializes_database_and_stops_offline(self):
        # Separate process: production config and real entry point, no shared test DB.
        script = '''
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import bafser_config
root = Path(os.environ["QUEUEBOT_TEST_ROOT"])
for key in ("log_info_path", "log_requests_path", "log_errors_path", "log_frontend_path",
            "log_dashboard_path", "log_bot_path", "jwt_key_file_path", "images_folder"):
    setattr(bafser_config, key, str(root / key))
bafser_config.config_path = str(root / "config.txt")
Path(bafser_config.config_path).write_text(
    "bot_token = fake\\nbot_name = @TestQueueBot\\nwebhook_token = unused\\nurl = https://example.com/\\n")

import main
from bafser import db_session
from sqlalchemy import inspect

class PollingReached(Exception):
    pass

def stop_polling():
    raise PollingReached()

with patch("sys.argv", ["main.py"]), \\
     patch("bafser_tgapi.bot.call_async"), \\
     patch("main.publish_commands"), \\
     patch("main.tgapi.deleteWebhook", return_value=(True, {})) as webhook, \\
     patch("main.tgapi.run_long_polling", side_effect=stop_polling), \\
     patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
    try:
        main.main()
    except PollingReached:
        pass
    else:
        raise AssertionError("Polling was never started")
    webhook.assert_called_once_with(drop_pending_updates=False)
with db_session.create_session() as session:
    assert {"Queue", "QueueUser", "TicTacToe"} <= set(inspect(session.get_bind()).get_table_names())
    session.get_bind().dispose()
logging.shutdown()
print("Production startup OK")
'''
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            environment.update(DBPATH=str(Path(temporary) / "production.db"), DB_MYSQL="0",
                               QUEUEBOT_TEST_ROOT=temporary, QUEUE_TIMEZONE="UTC", PYTHONIOENCODING="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Production startup OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
