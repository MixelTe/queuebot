import re
from urllib.parse import urlparse

import bafser_tgapi as tgapi
from bafser_tgapi import utils as tgutils

from bot.bot import Bot


def publish_commands():
    with Bot() as bot:
        public = bot.get_my_commands()
        admin = dict(public + bot.get_my_commands(True))
    for commands, scope in ((public, None), (list(admin.items()), tgapi.BotCommandScope.all_chat_administrators())):
        items = [tgapi.BotCommand(command=name, description=desc if isinstance(desc, str) else desc[0]) for name, desc in commands]
        ok, result = tgapi.setMyCommands(items, scope)
        if not ok:
            raise RuntimeError(f"Failed to publish bot commands: {result}")


def configure_webhook():
    token = tgutils.webhook_token
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token) or token in ("unused-in-polling-mode", "PUT_RANDOM_SECRET_HERE"):
        raise ValueError("Set a random webhook_token in config.txt (A-Z, a-z, 0-9, _ or -).")
    if urlparse(tgutils.url).scheme != "https":
        raise ValueError("Webhook url must use HTTPS.")
    # The wrapper does not expose max_connections; use the existing generic API.
    ok, result = tgutils.call("setWebhook", {
        "url": tgutils.get_url("/webhook"), "secret_token": token,
        "max_connections": 1, "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    })
    if not ok:
        raise RuntimeError(f"Failed to configure webhook: {result}")
