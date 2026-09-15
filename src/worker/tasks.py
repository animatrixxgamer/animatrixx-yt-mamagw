"""Celery tasks for background video processing."""

import tempfile
import os
from datetime import datetime

from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.worker import celery_app
from src.config import get_settings
from src.models.upload_job import UploadJob, JobStatus
from src.models.media import MediaAsset, MediaStatus
from src.models.youtube_channel import YouTubeChannel, TokenStatus
from src.services.youtube import youtube_service
from src.services.encryption import encryption_service

settings = get_settings()

# Database connection for worker
engine = create_engine(settings.database_url_sync)
Session = sessionmaker(bind=engine)


class DatabaseTask(Task):
    """Base task with database session."""

    _session = None

    @property
    def session(self):
        if self._session is None:
            self._session = Session()
        return self._session

    def after_return(self, *args, **kwargs):
        if self._session:
            self._session.close()
            self._session = None


@celery_app.task(
    base=DatabaseTask,
    bind=True,
    name="worker.process_upload",
    max_retries=3,
    default_retry_delay=60,
)
def process_upload(self: Task, job_id: int) -> dict:
    """Process a YouTube upload job in the background."""
    session = self.session

    try:
        # Get job
        job = session.query(UploadJob).filter(UploadJob.id == job_id).first()
        if not job:
            return {"error": "Job not found"}

        # Update status
        job.status = JobStatus.PROCESSING
        job.last_attempt_at = datetime.utcnow()
        job.attempt_count += 1
        session.commit()

        # Get media
        media = session.query(MediaAsset).filter(MediaAsset.id == job.media_id).first()
        if not media or media.status != MediaStatus.VALID:
            job.status = JobStatus.FAILED
            job.error_code = "INVALID_MEDIA"
            job.error_message = "Media file is invalid or missing"
            session.commit()
            return {"error": "Invalid media"}

        # Get channel
        channel = session.query(YouTubeChannel).filter(
            YouTubeChannel.id == job.channel_id
        ).first()
        if not channel or channel.token_status != TokenStatus.VALID:
            job.status = JobStatus.FAILED
            job.error_code = "INVALID_CHANNEL"
            job.error_message = "Channel is invalid or tokens expired"
            session.commit()
            return {"error": "Invalid channel"}

        # Decrypt tokens
        access_token = encryption_service.decrypt(channel.encrypted_access_token)
        refresh_token = encryption_service.decrypt(channel.encrypted_refresh_token)

        # Create credentials
        credentials = youtube_service.get_credentials_from_tokens(
            access_token, refresh_token, channel.token_expires_at
        )

        # Download media file
        from src.services.media import media_service
        file_data = media_service.storage.download(media.storage_key)

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            # Update status to uploading
            job.status = JobStatus.UPLOADING
            session.commit()

            # Prepare metadata
            metadata = {
                "title": job.title or "Untitled Video",
                "description": job.description or "",
                "tags": job.tags.split(",") if job.tags else [],
                "category_id": job.category_id,
                "privacy_status": job.privacy_status,
                "scheduled_at": job.scheduled_at,
                "mime_type": media.mime_type,
            }

            # Upload to YouTube
            result = youtube_service.upload_video(
                credentials, tmp_path, metadata
            )

            # Update job
            job.youtube_video_id = result["video_id"]
            job.youtube_video_url = result["video_url"]
            job.status = JobStatus.PUBLISHED
            job.completed_at = datetime.utcnow()
            session.commit()

            # Update channel quota
            channel.upload_quota_used += 1600  # Approximate cost
            channel.last_upload_at = datetime.utcnow()
            session.commit()

            return {
                "success": True,
                "video_id": result["video_id"],
                "video_url": result["video_url"],
            }

        finally:
            os.unlink(tmp_path)

    except Exception as e:
        # Handle failure
        job = session.query(UploadJob).filter(UploadJob.id == job_id).first()
        if job:
            if job.attempt_count >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.error_code = type(e).__name__
                job.error_message = str(e)
            else:
                job.status = JobStatus.RETRYING
                job.error_code = type(e).__name__
                job.error_message = str(e)
            session.commit()

        # Retry if attempts remaining
        if job and job.attempt_count < job.max_attempts:
            raise self.retry(exc=e, countdown=60 * (2 ** job.attempt_count))

        return {"error": str(e)}


@celery_app.task(name="worker.cleanup_expired_tokens")
def cleanup_expired_tokens():
    """Clean up expired OAuth tokens."""
    session = Session()
    try:
        expired = session.query(YouTubeChannel).filter(
            YouTubeChannel.token_expires_at < datetime.utcnow(),
            YouTubeChannel.token_status == TokenStatus.VALID,
        ).all()

        for channel in expired:
            channel.token_status = TokenStatus.EXPIRED
        session.commit()

        return {"cleaned": len(expired)}
    finally:
        session.close()


@celery_app.task(name="worker.cleanup_old_media")
def cleanup_old_media(days: int = 7):
    """Clean up old media files."""
    session = Session()
    try:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)

        old_media = session.query(MediaAsset).filter(
            MediaAsset.created_at < cutoff,
            MediaAsset.status.in_([
                MediaStatus.UPLOADING_COMPLETE,
                MediaStatus.FAILED,
                MediaStatus.CLEANED_UP,
            ]),
        ).all()

        from src.services.media import media_service
        cleaned = 0
        for media in old_media:
            try:
                media_service.storage.delete(media.storage_key)
                media.status = MediaStatus.CLEANED_UP
                cleaned += 1
            except Exception:
                pass

        session.commit()
        return {"cleaned": cleaned}
    finally:
        session.close()
