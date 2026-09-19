"""Video upload handlers."""

import os
from typing import Any

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

from src.models.media import MediaAsset, MediaStatus, MediaSource
from src.models.youtube_channel import YouTubeChannel, TokenStatus
from src.services.media import media_service
from src.services.job import job_service
from src.config import get_settings

router = Router()
settings = get_settings()


class UploadStates(StatesGroup):
    """FSM states for video upload."""
    SELECTING_CHANNEL = State()
    CONFIRMING_UPLOAD = State()


@router.message(F.video)
async def handle_video(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    db_user=None,
) -> None:
    """Handle incoming video file."""
    video = message.video
    if not video:
        await message.answer("❌ No video file detected.")
        return

    # Validate file
    if not media_service.validate_video_file(
        video.file_name or "video.mp4",
        video.file_size,
        video.mime_type,
    ):
        await message.answer(
            "❌ <b>Invalid video file</b>\n\n"
            f"Supported formats: {settings.supported_video_formats}\n"
            f"Maximum size: {settings.max_file_size_mb}MB"
        )
        return

    # Download from Telegram
    await message.answer("⏳ Downloading video from Telegram...")
    try:
        file = await message.bot.get_file(video.file_id)
        file_data = await message.bot.download_file(file.file_path)

        # Upload to storage
        filename = video.file_name or f"video_{video.file_id}.mp4"
        storage_result = await media_service.upload_file(
            db_user.id,
            filename,
            file_data,
            video.mime_type,
        )

        # Create media asset record
        media = MediaAsset(
            user_id=db_user.id,
            telegram_file_id=video.file_id,
            filename=filename,
            original_filename=video.file_name,
            mime_type=video.mime_type,
            file_size=video.file_size,
            storage_backend=settings.storage_backend,
            storage_bucket=settings.storage_bucket,
            storage_key=storage_result["key"],
            status=MediaStatus.VALID,
            source=MediaSource.TELEGRAM,
        )
        db.add(media)
        await db.flush()

        # Check for connected channels
        result = await db.execute(
            select(YouTubeChannel).where(
                YouTubeChannel.user_id == db_user.id,
                YouTubeChannel.token_status == TokenStatus.VALID,
            )
        )
        channels = list(result.scalars().all())

        if not channels:
            await message.answer(
                "⚠️ <b>No Connected Channels</b>\n\n"
                "Please connect a YouTube channel first with /connect"
            )
            return

        # Store media ID in state
        await state.update_data(media_id=media.id)
        await state.set_state(UploadStates.SELECTING_CHANNEL)

        # Show channel selection
        buttons = []
        for ch in channels:
            buttons.append([
                InlineKeyboardButton(
                    text=f"📺 {ch.channel_name}",
                    callback_data=f"upload_select_channel:{ch.id}",
                )
            ])

        buttons.append([
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_upload")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            "✅ <b>Video Downloaded</b>\n\n"
            f"📁 File: {filename}\n"
            f"📊 Size: {video.file_size / (1024 * 1024):.1f}MB\n\n"
            "Select a YouTube channel to upload to:",
            reply_markup=keyboard,
        )

    except Exception as e:
        await message.answer(f"❌ Failed to download video: {str(e)}")


@router.callback_query(F.data.startswith("upload_select_channel:"))
async def select_channel_for_upload(
    callback: CallbackQuery,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    """Select channel for upload."""
    channel_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    media_id = data.get("media_id")

    if not media_id:
        await callback.message.edit_text("❌ Upload session expired. Please send the video again.")
        await callback.answer()
        return

    # Create upload job
    job = await job_service.create_job(
        db,
        callback.from_user.id,
        channel_id,
        media_id,
        {"privacy_status": "private"},
    )

    await state.update_data(job_id=job.id)
    await state.set_state(UploadStates.CONFIRMING_UPLOAD)

    # Get channel info
    result = await db.execute(
        select(YouTubeChannel).where(YouTubeChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Edit Metadata",
                    callback_data="edit_metadata",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Upload Now",
                    callback_data="confirm_upload",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="cancel_upload",
                ),
            ],
        ]
    )

    await callback.message.edit_text(
        "📋 <b>Upload Configuration</b>\n\n"
        f"📺 Channel: {channel.channel_name if channel else 'Unknown'}\n"
        f"🔒 Privacy: Private (default)\n\n"
        "Click 'Edit Metadata' to configure title, description, tags, etc.\n"
        "Or click 'Upload Now' to proceed with defaults.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_upload")
async def confirm_upload(
    callback: CallbackQuery,
    state: FSMContext,
    db: AsyncSession,
) -> None:
    """Confirm and start upload."""
    data = await state.get_data()
    job_id = data.get("job_id")

    if not job_id:
        await callback.message.edit_text("❌ Upload session expired.")
        await callback.answer()
        return

    # Get job
    job = await job_service.get_job(db, job_id)
    if not job:
        await callback.message.edit_text("❌ Upload job not found.")
        await callback.answer()
        return

    # Start processing (in production, this would be a background task)
    await callback.message.edit_text("⏳ <b>Starting upload...</b>\n\nThis may take a few minutes.")

    try:
        await job_service.process_upload(db, job)
        await db.commit()

        if job.youtube_video_url:
            await callback.message.edit_text(
                f"✅ <b>Upload Successful!</b>\n\n"
                f"🎬 Video: {job.title}\n"
                f"📺 Channel: {job.channel.channel_name}\n"
                f"🔗 URL: {job.youtube_video_url}\n"
                f"🔒 Privacy: {job.privacy_status}"
            )
        else:
            await callback.message.edit_text(
                f"⚠️ <b>Upload Processing</b>\n\n"
                f"Job ID: {job.id}\n"
                f"Status: {job.status.value}\n\n"
                "Check /status for updates."
            )
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Upload Failed</b>\n\n"
            f"Error: {str(e)}\n\n"
            "Try again or contact support."
        )

    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "cancel_upload")
async def cancel_upload(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel upload."""
    data = await state.get_data()
    job_id = data.get("job_id")

    if job_id:
        # Cancel the job in database
        pass  # Implementation would cancel the job

    await state.clear()
    await callback.message.edit_text("❌ Upload cancelled.")
    await callback.answer()
