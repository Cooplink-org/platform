"""
aiogram v3 bot for Cooplink phone verification.

This module is loaded by the Django webhook view to process incoming
Telegram updates. It sets up the Dispatcher and registers all handlers.

Usage from Django webhook view:
    from notifications.bot import get_dispatcher, get_bot
    dp = get_dispatcher()
    bot = get_bot()
    update = Update.model_validate(update_data, context={"bot": bot})
    await dp.feed_update(bot, update)
"""

import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .handlers import register_handlers

logger = logging.getLogger(__name__)

_dispatcher: Dispatcher | None = None
_bot: Bot | None = None


def get_bot() -> Bot:
    """Return a singleton Bot instance configured with the project's token."""
    global _bot
    if _bot is None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")
        _bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    """Return a singleton Dispatcher with all handlers registered."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        register_handlers(_dispatcher)
    return _dispatcher


def reset_bot():
    """Reset bot instance (useful for testing or token rotation)."""
    global _bot, _dispatcher
    if _bot is not None:
        try:
            import asyncio

            asyncio.get_event_loop().run_until_complete(_bot.session.close())
        except Exception:
            pass
    _bot = None
    _dispatcher = None
