import sys
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

import bafser_tgapi as tgapi
import portalocker
from bafser import AppConfig, create_app

import bafser_config
from bot.bot import Bot
from utils.http import register_http
from utils.maintenance import maintenance_loop
from utils.runtime import update_lock
from utils.webhook import configure_webhook, publish_commands


def create_application(dev: bool = False):
    ZoneInfo(bafser_config.queue_timezone)
    Path("storage").mkdir(exist_ok=True)
    with update_lock:
        app, run = create_app(__name__, AppConfig(DEV_MODE=dev))
        run(False)
        tgapi.setup(botCls=Bot, dev=dev)
        register_http(app)
    return app


def main():
    dev = "dev" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "dev"]
    mode = args[0] if args else bafser_config.bot_mode
    if len(args) > 1 or mode not in ("polling", "webhook", "set-webhook", "setup"):
        raise ValueError("Usage: python main.py [polling|webhook|set-webhook|setup] [dev]")
    if mode in ("setup", "set-webhook"):
        create_application(dev)
        if mode == "set-webhook":
            configure_webhook()
            publish_commands()
        return
    Path("storage").mkdir(exist_ok=True)
    # Standalone modes have one server/polling process and an internal scheduler.
    with portalocker.Lock("storage/bot.lock", timeout=0):
        app = create_application(dev)
        if mode == "webhook":
            configure_webhook()
        else:
            ok, result = tgapi.deleteWebhook(drop_pending_updates=False)
            if not ok:
                raise RuntimeError(f"Cannot switch to polling: {result}")
        publish_commands()
        stop = Event()
        maintenance = Thread(target=maintenance_loop, args=(app, stop), daemon=True)
        maintenance.start()
        try:
            if mode == "webhook":
                from waitress import serve

                serve(app, host=bafser_config.bot_host, port=bafser_config.bot_port)
            else:
                tgapi.run_long_polling()
                raise RuntimeError("Telegram polling stopped; restart the service.")
        finally:
            stop.set()
            maintenance.join(timeout=10)


if __name__ == "__main__":
    main()

