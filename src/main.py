"""Main application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from src.web.routes import router as web_router

settings = get_settings()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting YouTube Telegram Bot...")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

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

    # Store bot in app state
    app.state.bot = bot
    app.state.dp = dp

    logger.info("Bot started successfully")

    yield

    # Cleanup
    logger.info("Shutting down...")
    await bot.session.close()


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="YouTube Telegram Bot",
        description="Telegram bot for managing YouTube channel uploads",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include web routes
    app.include_router(web_router, prefix="/api")

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    @app.get("/ready")
    async def readiness_check():
        return {"status": "ready"}

    return app


app = create_app()


async def run_bot():
    """Run the Telegram bot polling."""
    bot = app.state.bot
    dp = app.state.dp

    # Get database session for middleware
    async with async_session() as db:
        dp["db"] = db

        # Start polling
        await dp.start_polling(bot)


if __name__ == "__main__":
    import uvicorn

    # Run both FastAPI server and bot polling
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.app_debug,
    )
