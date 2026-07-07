import logging
import asyncio
from django.conf import settings
from pyrogram import Client

log = logging.getLogger(__name__)

def send_telegram_message(chat_id: str, text: str):
    """
    Synchronously sends a message using Pyrogram by bridging to its async methods.
    Expects TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, and TELEGRAM_API_HASH in settings.
    """
    if not chat_id:
        log.info("No chat_id provided, skipping notification: %s", text)
        return

    async def _send():
        try:
            app = Client(
                "cooplink_notifier",
                bot_token=settings.env("TELEGRAM_BOT_TOKEN"),
                api_id=settings.env("TELEGRAM_API_ID", default=None),
                api_hash=settings.env("TELEGRAM_API_HASH", default=None),
                in_memory=True,
            )
            async with app:
                await app.send_message(chat_id, text)
            log.info("Telegram message sent to %s", chat_id)
        except Exception as exc:
            log.error("Failed to send telegram message to %s: %s", chat_id, exc)

    try:
        # bridge async logic to sync task
        asyncio.run(_send())
    except Exception as exc:
        log.error("Asyncio loop error while sending telegram message: %s", exc)
