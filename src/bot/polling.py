"""Run Telegram bot with polling."""

import asyncio
import logging

from src.config import get_settings
from src.database import init_db, async_session
from src.bot import create_bot, create_dispatcher
from src.bot.handlers import (
    start_router,
    youtube_router,
    upload_router,
    metadata_router,
    status_router,
    admin_router,
)
from src.bot.middleware import AuthorizationMiddleware, RateLimitMiddleware

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


async def main():
    """Main polling loop."""
    logger.info("Starting bot polling...")

    # Initialize database
    await init_db()

    # Create bot and dispatcher
    bot = create_bot()
    dp = create_dispatcher()

    # Register routers
    dp.include_router(start_router)
    dp.include_router(youtube_router)
    dp.include_router(upload_router)
    dp.include_router(metadata_router)
    dp.include_router(status_router)
    dp.include_router(admin_router)

    # Add middleware
    dp.message.middleware(AuthorizationMiddleware())
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(AuthorizationMiddleware())

    try:
        # Start polling
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
