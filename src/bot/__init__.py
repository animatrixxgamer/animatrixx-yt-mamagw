"""Telegram bot application."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from src.config import get_settings

settings = get_settings()


def create_bot() -> Bot:
    """Create and configure Telegram bot."""
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )


def create_dispatcher() -> Dispatcher:
    """Create and configure bot dispatcher."""
    return Dispatcher()
