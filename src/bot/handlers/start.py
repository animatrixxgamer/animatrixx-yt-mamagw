"""Start and help command handlers."""

from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart, Command

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, db_user=None) -> None:
    """Handle /start command."""
    welcome_text = (
        "🎬 <b>Welcome to YouTube Telegram Bot!</b>\n\n"
        "I help you manage your YouTube channel uploads directly from Telegram.\n\n"
        "<b>Quick Start:</b>\n"
        "1. Connect your YouTube channel with /connect\n"
        "2. Send me a video file\n"
        "3. Configure metadata and publish!\n\n"
        "Type /help for all available commands."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📺 Connect YouTube", callback_data="connect_youtube"),
                InlineKeyboardButton(text="📖 Help", callback_data="show_help"),
            ],
            [
                InlineKeyboardButton(text="📋 My Channels", callback_data="list_channels"),
            ],
        ]
    )

    await message.answer(welcome_text, reply_markup=keyboard)


@router.message(Command("help"))
@router.callback_query(F.data == "show_help")
async def cmd_help(message: Message) -> None:
    """Handle /help command or callback."""
    help_text = (
        "📖 <b>YouTube Bot Commands</b>\n\n"
        "<b>YouTube Channel Management:</b>\n"
        "/connect - Connect a YouTube channel via OAuth\n"
        "/channels - List connected channels\n"
        "/disconnect [channel_id] - Disconnect a channel\n\n"
        "<b>Video Upload:</b>\n"
        "Send a video file to start upload process\n"
        "/upload - Upload a previously sent video\n"
        "/status - View upload job status\n"
        "/jobs - List your upload jobs\n"
        "/cancel [job_id] - Cancel a pending job\n\n"
        "<b>Metadata Configuration:</b>\n"
        "/title [text] - Set video title\n"
        "/description [text] - Set video description\n"
        "/tags [tag1, tag2] - Set video tags\n"
        "/hashtags [tag1, tag2] - Set hashtags\n"
        "/suggest_hashtags - Get hashtag suggestions\n"
        "/privacy [public/private/unlisted] - Set privacy\n"
        "/schedule [YYYY-MM-DD HH:MM] - Schedule upload\n\n"
        "<b>Admin Commands:</b>\n"
        "/admin - Admin panel (admin only)\n"
        "/audit - View audit log (admin only)\n"
        "/quota - View YouTube quota usage\n\n"
        "<b>Other:</b>\n"
        "/cancel_operation - Cancel current operation\n"
        "/start - Show welcome message"
    )

    if isinstance(message, Message):
        await message.answer(help_text)
