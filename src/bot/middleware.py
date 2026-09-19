"""Bot middleware for authorization and request logging."""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User, UserRole


class AuthorizationMiddleware(BaseMiddleware):
    """Middleware to check user authorization before handling messages."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Extract user from event
        user = None
        if isinstance(event, Message) and event.from_user:
            user = event.from_user

        if not user:
            return await handler(event, data)

        # Check if user is in authorized list
        if user.id not in settings.telegram_authorized_user_ids:
            if isinstance(event, Message):
                await event.answer(
                    "⛔ You are not authorized to use this bot.\n"
                    "Please contact the administrator for access."
                )
            return

        # Get or create user in database
        db: AsyncSession = data["db"]
        result = await db.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()

        if not db_user:
            # Create new user with default role
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                role=UserRole.USER,
            )
            db.add(db_user)
            await db.flush()

        # Add user to handler data
        data["db_user"] = db_user
        data["user_id"] = user.id

        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Simple rate limiting middleware."""

    def __init__(self, limit: int = 30, period: int = 60) -> None:
        self.limit = limit
        self.period = period
        self.user_timestamps: Dict[int, list[float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        import time

        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        # Check rate limit
        now = time.time()
        timestamps = self.user_timestamps.get(user_id, [])
        timestamps = [t for t in timestamps if now - t < self.period]

        if len(timestamps) >= self.limit:
            if isinstance(event, Message):
                await event.answer("⚠️ Rate limit exceeded. Please wait a moment.")
            return

        timestamps.append(now)
        self.user_timestamps[user_id] = timestamps

        return await handler(event, data)


from src.config import get_settings
settings = get_settings()
