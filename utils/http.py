import hmac
from datetime import datetime, timezone

import bafser_tgapi as tgapi
from bafser import JsonParseError, db_session
from bafser_tgapi import utils as tgutils
from flask import g, request

import bafser_config
from data.processed_update import ProcessedUpdate
from utils.delivery import collect_deliveries, deliver_pending
from utils.maintenance import clear_due_queues
from utils.runtime import update_lock
from utils.updates import process_update


def register_http(app):
    @app.post("/webhook")
    def webhook():
        expected = tgutils.webhook_token
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if expected in ("", "unused-in-polling-mode", "PUT_RANDOM_SECRET_HERE") or not hmac.compare_digest(token.encode(), expected.encode()):
            return "Forbidden", 403
        if not request.is_json:
            return "Expected JSON", 415
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return "Invalid update", 400
        try:
            update = tgapi.Update.new(data).valid()
        except (JsonParseError, TypeError, ValueError):
            return "Invalid update", 400
        with update_lock, db_session.create_session() as session:
            previous = session.get(ProcessedUpdate, update.update_id)
            if previous is None:
                with collect_deliveries() as pending:
                    process_update(update, g.get("req_id", ""))
                # Persist completion of the command before delivering its cards.
                # A failed edit retries delivery, never the already applied action.
                session.add(ProcessedUpdate(
                    update_id=update.update_id, processed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    deliveries=pending or None,
                ))
                session.commit()
            elif not previous.deliveries:
                return "ok"
            # No background thread: return 2xx only after delivery completes.
            deliver_pending(app, update.update_id)
        return "ok"

    @app.route("/api/ping", methods=["GET", "POST"])
    def ping():
        if bafser_config.ping_auth_required:
            token = request.headers.get("X-Queuebot-Ping-Token", "") or request.args.get("token", "")
            expected = bafser_config.ping_token
            if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
                return "Forbidden", 403
        clear_due_queues(app)
        return "ok"
