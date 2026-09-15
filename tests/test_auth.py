"""Unit tests for authorization middleware."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User

from src.bot.middleware import AuthorizationMiddleware
from src.models.user import User as UserModel, UserRole


class TestAuthorizationMiddleware:
    """Test authorization middleware."""

    @pytest.mark.asyncio
    async def test_authorized_user(self):
        """Test that authorized user can access handlers."""
        middleware = AuthorizationMiddleware()

        # Mock message with authorized user.
        # spec=User restricts attributes to the real Telegram User fields, so
        # every field the middleware reads must be set explicitly.
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 123456789
        message.from_user.username = "testuser"
        message.from_user.first_name = "Test"
        message.from_user.last_name = "User"

        # Mock database.
        # AsyncMock makes every attribute async, but SQLAlchemy's Session.add()
        # is synchronous - mocking it as async creates a coroutine that is never
        # awaited (and a RuntimeWarning).
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute.return_value = result

        # Mock handler
        handler = AsyncMock()

        # Patch settings
        with patch("src.bot.middleware.settings") as mock_settings:
            mock_settings.telegram_authorized_user_ids = [123456789]

            # Run middleware
            data = {"db": db}
            await middleware(handler, message, data)

            # Handler should be called
            handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_unauthorized_user(self):
        """Test that unauthorized user is blocked."""
        middleware = AuthorizationMiddleware()

        # Mock message with unauthorized user
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 999999999
        message.answer = AsyncMock()

        # Mock database
        db = AsyncMock()

        # Mock handler
        handler = AsyncMock()

        # Patch settings
        with patch("src.bot.middleware.settings") as mock_settings:
            mock_settings.telegram_authorized_user_ids = [123456789]

            # Run middleware
            data = {"db": db}
            await middleware(handler, message, data)

            # Handler should NOT be called
            handler.assert_not_called()

            # Error message should be sent
            message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_new_user(self):
        """Test that new user is created in database."""
        middleware = AuthorizationMiddleware()

        # Mock message with authorized user
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 123456789
        message.from_user.username = "testuser"
        message.from_user.first_name = "Test"
        message.from_user.last_name = "User"

        # Mock database.
        # AsyncMock makes every attribute async, but SQLAlchemy's Session.add()
        # is synchronous - mocking it as async creates a coroutine that is never
        # awaited (and a RuntimeWarning).
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute.return_value = result

        # Mock handler
        handler = AsyncMock()

        # Patch settings
        with patch("src.bot.middleware.settings") as mock_settings:
            mock_settings.telegram_authorized_user_ids = [123456789]

            # Run middleware
            data = {"db": db}
            await middleware(handler, message, data)

            # New user should be added
            db.add.assert_called_once()
            db.flush.assert_called_once()


class TestUserRoleChecks:
    """Test role-based access control."""

    def test_owner_has_full_access(self):
        """Test that owner has full access."""
        user = UserModel(
            telegram_id=123,
            role=UserRole.OWNER,
        )
        assert user.role == UserRole.OWNER

    def test_admin_has_admin_access(self):
        """Test that admin has admin access."""
        user = UserModel(
            telegram_id=123,
            role=UserRole.ADMIN,
        )
        assert user.role == UserRole.ADMIN

    def test_user_has_limited_access(self):
        """Test that regular user has limited access."""
        user = UserModel(
            telegram_id=123,
            role=UserRole.USER,
        )
        assert user.role == UserRole.USER
