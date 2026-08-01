import asyncio
import logging
import os

log = logging.getLogger(__name__)


def send_telegram_message(chat_id: str, text: str):
    """
    Synchronously sends a message using Pyrogram by bridging to its async methods.
    Expects TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, and TELEGRAM_API_HASH in env.
    """
    if not chat_id:
        log.info("No chat_id provided, skipping notification: %s", text)
        return

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    api_id = os.environ.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")

    if not bot_token:
        log.error("TELEGRAM_BOT_TOKEN is not set — cannot send message to %s", chat_id)
        return

    from pyrogram import Client

    async def _send():
        try:
            app = Client(
                "cooplink_notifier",
                bot_token=bot_token,
                api_id=api_id or None,
                api_hash=api_hash or None,
                in_memory=True,
            )
            async with app:
                await app.send_message(chat_id, text)
            log.info("Telegram message sent to %s", chat_id)
        except Exception as exc:
            log.error("Failed to send telegram message to %s: %s", chat_id, exc)

    try:
        asyncio.run(_send())
    except Exception as exc:
        log.error("Asyncio loop error while sending telegram message: %s", exc)
