import os

from dotenv import load_dotenv

load_dotenv()

db_dev_path = "storage/db/dev.db"
db_path = os.environ.get("DBPATH", "storage/db/bot.db")
db_mysql = os.environ.get("DB_MYSQL", "0") == "1"
sql_echo = False

use_alembic = True
migrations_folder = "alembic"

log_info_path = "storage/logs/log_info.csv"
log_requests_path = "storage/logs/log_requests.csv"
log_errors_path = "storage/logs/log_errors.log"
log_frontend_path = "storage/logs/log_frontend.log"
log_dashboard_path = "storage/logs/log_dashboard.csv"
jwt_key_file_path = "storage/secret_key_jwt.txt"
images_folder = "storage/images"
login_page_url = "/login"
api_url = "/api/"
blueprints_folder = "blueprints"
data_tables_folder = "data"

log_bot_path = "storage/logs/bot.csv"
bot_folder = "bot"
config_path = "config.txt"
config_dev_path = "config_dev.txt"

queue_timezone = os.environ.get("QUEUE_TIMEZONE", "Europe/Moscow")
update_lock_path = "storage/updates.lock"
ping_token = os.environ.get("PING_TOKEN", "")
ping_auth_required = os.environ.get("PING_AUTH_REQUIRED", "1") != "0"
bot_mode = os.environ.get("BOT_MODE", "polling")
bot_host = os.environ.get("BOT_HOST", "127.0.0.1")
bot_port = int(os.environ.get("BOT_PORT", "5000"))
