"""Admin handlers for user and system management."""

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.filters import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User, UserRole
from src.models.audit import AuditEvent
from src.models.youtube_channel import YouTubeChannel

router = Router()


def is_admin(user: User) -> bool:
    """Check if user has admin privileges."""
    return user.role in [UserRole.OWNER, UserRole.ADMIN]


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: AsyncSession, db_user=None) -> None:
    """Admin panel for user management."""
    if not is_admin(db_user):
        await message.answer("⛔ Admin access required.")
        return

    # Get statistics
    users_count = (await db.execute(select(User))).scalar_one().count
    channels_count = (await db.execute(select(YouTubeChannel))).scalar_one().count

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"👥 Users ({users_count})",
                    callback_data="admin_users",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"📺 Channels ({channels_count})",
                    callback_data="admin_channels",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Audit Log",
                    callback_data="admin_audit",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Settings",
                    callback_data="admin_settings",
                ),
            ],
        ]
    )

    await message.answer(
        "🔧 <b>Admin Panel</b>\n\n"
        f"👥 Users: {users_count}\n"
        f"📺 Channels: {channels_count}\n\n"
        "Select an option:",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, db: AsyncSession, db_user=None) -> None:
    """List all users."""
    if not is_admin(db_user):
        await callback.answer("⛔ Admin access required.", show_alert=True)
        return

    result = await db.execute(select(User).limit(20))
    users = list(result.scalars().all())

    text = "👥 <b>All Users</b>\n\n"
    buttons = []

    for user in users:
        status = "✅" if user.is_active else "❌"
        text += f"{status} <b>{user.first_name or user.username or user.telegram_id}</b>\n"
        text += f"   ID: {user.telegram_id}\n"
        text += f"   Role: {user.role.value}\n\n"

        if user.role != UserRole.OWNER:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{'⬇️ Demote' if user.role == UserRole.ADMIN else '⬆️ Promote'} {user.first_name or user.telegram_id}",
                    callback_data=f"toggle_role:{user.id}",
                )
            ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_role:"))
async def toggle_role(callback: CallbackQuery, db: AsyncSession, db_user=None) -> None:
    """Toggle user role between admin and user."""
    if not is_admin(db_user):
        await callback.answer("⛔ Admin access required.", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user:
        if user.role == UserRole.ADMIN:
            user.role = UserRole.USER
        else:
            user.role = UserRole.ADMIN
        await db.commit()
        await callback.answer(f"Updated {user.first_name or user.telegram_id}'s role.")

    # Refresh the list
    await admin_users(callback, db, db_user)


@router.callback_query(F.data == "admin_audit")
async def admin_audit(callback: CallbackQuery, db: AsyncSession, db_user=None) -> None:
    """View audit log."""
    if not is_admin(db_user):
        await callback.answer("⛔ Admin access required.", show_alert=True)
        return

    result = await db.execute(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(20)
    )
    events = list(result.scalars().all())

    text = "📋 <b>Recent Audit Events</b>\n\n"
    for event in events:
        text += f"• {event.action.value}\n"
        text += f"  Time: {event.created_at}\n"
        if event.user_id:
            text += f"  User: {event.user_id}\n"
        if event.error_message:
            text += f"  Error: {event.error_message[:50]}\n"
        text += "\n"

    await callback.message.edit_text(text)
    await callback.answer()
