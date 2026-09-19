"""Job status and history handlers."""

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

from src.models.upload_job import UploadJob, JobStatus
from src.services.job import job_service

router = Router()


@router.message(Command("status"))
async def cmd_status(message: Message, db: AsyncSession, db_user=None) -> None:
    """Show status of recent upload jobs."""
    jobs = await job_service.get_user_jobs(db, db_user.id, limit=5)

    if not jobs:
        await message.answer(
            "📋 <b>No Upload Jobs</b>\n\n"
            "You haven't uploaded any videos yet.\n"
            "Send me a video to get started!"
        )
        return

    status_emojis = {
        JobStatus.PENDING: "⏳",
        JobStatus.PROCESSING: "⚙️",
        JobStatus.UPLOADING: "📤",
        JobStatus.PUBLISHED: "✅",
        JobStatus.SCHEDULED: "📅",
        JobStatus.FAILED: "❌",
        JobStatus.CANCELLED: "🚫",
        JobStatus.RETRYING: "🔄",
    }

    text = "📋 <b>Recent Upload Jobs</b>\n\n"
    buttons = []

    for job in jobs:
        emoji = status_emojis.get(job.status, "❓")
        text += f"{emoji} <b>Job #{job.id}</b>\n"
        text += f"   Title: {job.title or 'Untitled'}\n"
        text += f"   Status: {job.status.value}\n"
        if job.youtube_video_url:
            text += f"   URL: {job.youtube_video_url}\n"
        text += "\n"

        if job.status in [JobStatus.PENDING, JobStatus.PROCESSING]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"❌ Cancel Job #{job.id}",
                    callback_data=f"cancel_job:{job.id}",
                )
            ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    await message.answer(text, reply_markup=keyboard)


@router.message(Command("jobs"))
async def cmd_jobs(message: Message, db: AsyncSession, db_user=None) -> None:
    """List all upload jobs with pagination."""
    jobs = await job_service.get_user_jobs(db, db_user.id, limit=10)

    if not jobs:
        await message.answer("📋 No upload jobs found.")
        return

    text = "📋 <b>All Upload Jobs</b>\n\n"
    for job in jobs:
        status_emoji = "✅" if job.status == JobStatus.PUBLISHED else "⏳"
        text += f"{status_emoji} <b>#{job.id}</b> - {job.title or 'Untitled'}\n"
        text += f"   Status: {job.status.value}\n"
        if job.youtube_video_url:
            text += f"   🔗 {job.youtube_video_url}\n"
        text += "\n"

    await message.answer(text)


@router.callback_query(F.data.startswith("cancel_job:"))
async def cancel_job(callback: CallbackQuery, db: AsyncSession, db_user=None) -> None:
    """Cancel a pending job."""
    job_id = int(callback.data.split(":")[1])

    job = await job_service.get_job(db, job_id)
    if not job:
        await callback.message.edit_text("❌ Job not found.")
        await callback.answer()
        return

    if job.user_id != db_user.id:
        await callback.message.edit_text("❌ You can only cancel your own jobs.")
        await callback.answer()
        return

    if job.status not in [JobStatus.PENDING, JobStatus.PROCESSING]:
        await callback.message.edit_text("❌ This job cannot be cancelled.")
        await callback.answer()
        return

    await job_service.update_job_status(db, job, JobStatus.CANCELLED)
    await db.commit()

    await callback.message.edit_text(f"✅ Job #{job_id} cancelled.")
    await callback.answer()


@router.message(Command("quota"))
async def cmd_quota(message: Message, db: AsyncSession, db_user=None) -> None:
    """Show YouTube quota usage."""
    from src.models.youtube_channel import YouTubeChannel, TokenStatus

    result = await db.execute(
        select(YouTubeChannel).where(
            YouTubeChannel.user_id == db_user.id,
            YouTubeChannel.token_status == TokenStatus.VALID,
        )
    )
    channels = list(result.scalars().all())

    if not channels:
        await message.answer("📺 No connected channels to show quota for.")
        return

    text = "📊 <b>YouTube Quota Usage</b>\n\n"
    for ch in channels:
        text += f"📺 <b>{ch.channel_name}</b>\n"
        text += f"   Quota used: {ch.upload_quota_used} units\n"
        text += f"   Last upload: {ch.last_upload_at or 'Never'}\n\n"

    text += (
        "ℹ️ <b>Note:</b> YouTube API has a daily quota limit of 10,000 units.\n"
        "Video uploads cost approximately 1,600 units per video.\n"
        "Quota resets daily at midnight Pacific Time."
    )

    await message.answer(text)
