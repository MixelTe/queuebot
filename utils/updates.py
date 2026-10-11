import bafser_tgapi as tgapi
from bafser_tgapi import utils as tgutils

from bot.bot import Bot


def process_update(update: tgapi.Update, req_id: str = ""):
    previous_bot = getattr(tgutils.thread_local, "bot", None)
    previous_request = getattr(tgutils.thread_local, "req_id", "")
    tgutils.thread_local.req_id = req_id
    try:
        with Bot() as bot:
            tgutils.thread_local.bot = bot
            try:
                bot._process_update(update)
            except tgutils.BotAnswerException as answer:
                if bot.callback_query:
                    bot.answerCallbackQuery(answer.text)
                else:
                    bot.sendMessage(answer.text, entities=answer.entities)
    finally:
        tgutils.thread_local.bot = previous_bot
        tgutils.thread_local.req_id = previous_request
