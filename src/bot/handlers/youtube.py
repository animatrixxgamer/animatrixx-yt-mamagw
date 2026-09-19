"""YouTube channel connection handlers."""

import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.youtube_channel import YouTubeChannel, TokenStatus
from src.services.youtube import youtube_service
from src.services.encryption import encryption_service
from src.services.audit import audit_service
from src.models.audit import AuditAction

router = Router()

# Rate limit for manual OAuth code exchange (prevents spam)
_oauth_paste_cooldown: dict[int, float] = {}  # user_id -> last attempt timestamp
OAUTH_PASTE_COOLDOWN_SECONDS = 30


class YouTubeStates(StatesGroup):
    """FSM states for YouTube channel connection."""
    WAITING_OAUTH = State()
    SELECTING_CHANNEL = State()
    PASTE_CODE = State()


@router.message(Command("connect"))
async def cmd_connect(message: Message, state: FSMContext, db: AsyncSession) -> None:
    """Start YouTube channel connection flow."""
    # Generate OAuth state token
    state_token = secrets.token_urlsafe(32)
    await state.update_data(oauth_state=state_token)

    # Get authorization URL
    auth_url = youtube_service.get_authorization_url(state_token)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f510 Authorize YouTube Access",
                    url=auth_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4cb Paste Code Manually",
                    callback_data="paste_oauth_code",
                )
            ],
            [
                InlineKeyboardButton(
                    text="\u274c Cancel",
                    callback_data="cancel_connect",
                )
            ],
        ]
    )

    await message.answer(
        "\U0001f4fa <b>Connect YouTube Channel</b>\n\n"
        "Click the button below to authorize this bot to access your YouTube channel.\n\n"
        "\u26a0\ufe0f <b>Important:</b>\n"
        "- You'll be redirected to Google's authorization page\n"
        "- Grant access to manage your YouTube videos\n"
        "- You can revoke access anytime from Google account settings\n\n"
        "After authorization, the bot will automatically connect your channel.\n\n"
        "\U0001f4cb <b>Hosting on KataBump, Pterodactyl, or no HTTPS?</b>\n"
        "If the redirect page fails to load, tap <b>Paste Code Manually</b> below, "
        "copy the URL from your browser's address bar, and paste it here.",
        reply_markup=keyboard,
    )

    await state.set_state(YouTubeStates.WAITING_OAUTH)


@router.callback_query(F.data == "paste_oauth_code")
async def cb_paste_oauth_code(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt the user to paste the failed OAuth redirect URL."""
    await callback.answer()

    await callback.message.edit_text(
        "\U0001f4cb <b>Paste the Failed URL</b>\n\n"
        "After Google redirects you and the page fails to load:\n"
        "1. <b>Copy the URL</b> from the browser address bar\n"
        "2. Paste it here\n\n"
        "The URL should look like:\n"
        "<code>http://localhost:8080/oauth/callback?code=4/0A...&amp;scope=...&amp;state=</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\u21a9\ufe0f Back", callback_data="cancel_connect")]
            ]
        ),
    )

    await state.set_state(YouTubeStates.PASTE_CODE)


@router.message(YouTubeStates.PASTE_CODE)
async def handle_pasted_oauth_code(message: Message, state: FSMContext, db: AsyncSession) -> None:
    """Process a pasted OAuth redirect URL to extract and exchange the authorization code."""
    user_id = message.from_user.id
    text = message.text or ""

    # Rate limit: prevent spamming the token exchange endpoint
    now_ts = time.time()
    last_attempt = _oauth_paste_cooldown.get(user_id, 0)
    if now_ts - last_attempt < OAUTH_PASTE_COOLDOWN_SECONDS:
        remaining = int(OAUTH_PASTE_COOLDOWN_SECONDS - (now_ts - last_attempt))
        await message.reply(
            f"\u23f3 Please wait {remaining}s before trying again."
        )
        return
    _oauth_paste_cooldown[user_id] = now_ts

    await state.clear()

    # Extract the authorization code from the pasted URL
    code_match = re.search(r"code=([^&]+)", text)
    if not code_match:
        await message.reply(
            "\u274c I couldn't find an authorization code in that URL.\n\n"
            "Make sure you copy the <b>full URL</b> from the browser address bar."
        )
        return

    # URL-decode the code (codes contain %2F which must become /)
    raw_code = code_match.group(1)
    auth_code = unquote(raw_code)

    # Validate the code looks reasonable
    if len(auth_code) < 10:
        await message.reply("\u274c That doesn't look like a valid authorization code.")
        return

    status_msg = await message.reply(
        "\U0001f504 Exchanging authorization code for tokens\u2026"
    )

    # Exchange the authorization code for access + refresh tokens
    try:
        tokens = await youtube_service.exchange_code(auth_code)
    except Exception as exc:
        err_text = str(exc)
        # Try to parse Google's JSON error response for friendly messages
        err_json = {}
        if hasattr(exc, "response"):
            try:
                err_json = exc.response.json()
            except Exception:
                pass
        error_code = err_json.get("error", "")
        error_desc = err_json.get("error_description", err_text)

        if error_code == "invalid_grant":
            friendly = (
                "\u274c <b>Code expired or already used</b>\n\n"
                "The authorization code is no longer valid.\n"
                "Tap <b>Connect YouTube</b> to generate a new one."
            )
        elif error_code == "redirect_uri_mismatch":
            friendly = (
                f"\u274c <b>Redirect URI mismatch</b>\n\n"
                f"Google says the redirect URI doesn't match.\n"
                f"Expected: <code>{youtube_service.redirect_uri}</code>"
            )
        else:
            friendly = f"\u274c Token exchange failed:\n<code>{error_desc[:300]}</code>"

        await status_msg.edit_text(
            friendly,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="\u25b6 Try Again", callback_data="connect_youtube"
                    )]
                ]
            ),
        )
        return

    # Check for refresh_token (required for long-term access)
    refresh = tokens.get("refresh_token", "")
    if not refresh:
        await status_msg.edit_text(
            "\u26a0\ufe0f <b>No refresh token received</b>\n\n"
            "Google didn't return a refresh token. This happens when the app was previously authorized.\n\n"
            "<b>Fix:</b> Revoke access at\n"
            '<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>\n'
            "then try again.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="\u25b6 Try Again", callback_data="connect_youtube"
                    )]
                ]
            ),
            disable_web_page_preview=True,
        )
        return

    # Fetch channel info and save
    try:
        expiry = datetime.utcnow() + timedelta(
            seconds=int(tokens.get("expires_in", 3600))
        )
        credentials = youtube_service.get_credentials_from_tokens(
            access_token=tokens.get("access_token", ""),
            refresh_token=refresh,
            expires_at=expiry,
        )
        channel_info = await youtube_service.get_channel_info(credentials)
    except Exception as exc:
        await status_msg.edit_text(
            f"\u274c Could not fetch channel info:\n<code>{str(exc)[:300]}</code>"
        )
        return

    # Encrypt tokens
    encrypted_access = encryption_service.encrypt(tokens.get("access_token", ""))
    encrypted_refresh = encryption_service.encrypt(refresh)

    # Create or update channel record
    result = await db.execute(
        select(YouTubeChannel).where(
            YouTubeChannel.channel_id == channel_info["channel_id"],
            YouTubeChannel.user_id == user_id,
        )
    )
    channel = result.scalar_one_or_none()

    if channel:
        channel.encrypted_access_token = encrypted_access
        channel.encrypted_refresh_token = encrypted_refresh
        channel.token_expires_at = expiry
        channel.token_status = TokenStatus.VALID
    else:
        channel = YouTubeChannel(
            user_id=user_id,
            channel_id=channel_info["channel_id"],
            channel_name=channel_info["channel_name"],
            channel_title=channel_info.get("channel_title"),
            thumbnail_url=channel_info.get("thumbnail_url"),
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_expires_at=expiry,
            token_status=TokenStatus.VALID,
        )
        db.add(channel)

    await db.flush()

    # Audit log
    await audit_service.log_channel_action(
        db,
        AuditAction.CHANNEL_CONNECTED,
        user_id,
        channel_info["channel_id"],
        details={"channel_name": channel_info["channel_name"]},
    )

    await status_msg.edit_text(
        f"\u2705 <b>YouTube connected!</b>\n\n"
        f"\U0001f4fa {channel_info['channel_name']}\n"
        f"\U0001f194 <code>{channel_info['channel_id']}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\u25b6 Upload Something",
                        callback_data="upload_video",
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data == "cancel_connect")
async def cancel_connect(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel YouTube connection flow."""
    await state.clear()
    await callback.message.edit_text("\u274c YouTube connection cancelled.")
    await callback.answer()


@router.message(Command("channels"))
@router.callback_query(F.data == "list_channels")
async def list_channels(
    message: Message | CallbackQuery,
    db: AsyncSession,
    db_user=None,
) -> None:
    """List connected YouTube channels."""
    result = await db.execute(
        select(YouTubeChannel).where(
            YouTubeChannel.user_id == db_user.id,
            YouTubeChannel.token_status != TokenStatus.REVOKED,
        )
    )
    channels = list(result.scalars().all())

    if not channels:
        text = "\U0001f4fa <b>No Connected Channels</b>\n\nYou haven't connected any YouTube channels yet.\n\nUse /connect to get started."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="\U0001f4fa Connect Channel", callback_data="connect_youtube")]
            ]
        )
    else:
        text = "\U0001f4fa <b>Connected YouTube Channels</b>\n\n"
        buttons = []
        for ch in channels:
            status_emoji = "\u2705" if ch.token_status == TokenStatus.VALID else "\u26a0\ufe0f"
            text += f"{status_emoji} <b>{ch.channel_name}</b>\n"
            text += f"   ID: <code>{ch.channel_id[:20]}...</code>\n"
            text += f"   Quota used: {ch.upload_quota_used}\n\n"

            buttons.append([
                InlineKeyboardButton(
                    text=f"\u2705 Select {ch.channel_name}",
                    callback_data=f"select_channel:{ch.id}",
                ),
                InlineKeyboardButton(
                    text=f"\U0001f50c Disconnect",
                    callback_data=f"disconnect_channel:{ch.id}",
                ),
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, reply_markup=keyboard)
        await message.answer()
    else:
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("select_channel:"))
async def select_channel(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    """Select a channel for uploads."""
    channel_id = int(callback.data.split(":")[1])
    await state.update_data(selected_channel_id=channel_id)

    result = await db.execute(
        select(YouTubeChannel).where(YouTubeChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    if channel:
        await callback.message.edit_text(
            f"\u2705 Selected channel: <b>{channel.channel_name}</b>\n\n"
            "Now send me a video to upload!"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("disconnect_channel:"))
async def disconnect_channel(
    callback: CallbackQuery,
    db: AsyncSession,
    db_user=None,
) -> None:
    """Disconnect a YouTube channel."""
    channel_id = int(callback.data.split(":")[1])

    result = await db.execute(
        select(YouTubeChannel).where(YouTubeChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    if channel and channel.user_id == db_user.id:
        channel.token_status = TokenStatus.REVOKED
        await db.flush()

        # Audit log
        await audit_service.log_channel_action(
            db,
            AuditAction.CHANNEL_DISCONNECTED,
            db_user.id,
            channel.channel_id,
        )

        await callback.message.edit_text(
            f"\U0001f50c Disconnected channel: <b>{channel.channel_name}</b>\n\n"
            "You can reconnect it anytime with /connect."
        )
    await callback.answer()
