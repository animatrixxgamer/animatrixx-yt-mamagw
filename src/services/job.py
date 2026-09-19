"""Job service for managing upload jobs."""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.upload_job import UploadJob, JobStatus, IdempotencyKey
from src.models.media import MediaAsset, MediaStatus
from src.models.youtube_channel import YouTubeChannel, TokenStatus
from src.services.youtube import youtube_service
from src.services.encryption import encryption_service


class JobService:
    """Service for managing upload jobs."""

    def generate_idempotency_key(self, user_id: int, channel_id: int, media_id: int) -> str:
        """Generate a unique idempotency key for a job."""
        return f"{user_id}:{channel_id}:{media_id}:{uuid.uuid4().hex[:16]}"

    async def create_job(
        self,
        db: AsyncSession,
        user_id: int,
        channel_id: int,
        media_id: int,
        metadata: dict,
    ) -> UploadJob:
        """Create a new upload job with idempotency check."""
        # Check for existing job with same user/channel/media
        existing = await db.execute(
            select(UploadJob).where(
                UploadJob.user_id == user_id,
                UploadJob.channel_id == channel_id,
                UploadJob.media_id == media_id,
                UploadJob.status.in_([
                    JobStatus.PENDING,
                    JobStatus.PROCESSING,
                    JobStatus.UPLOADING,
                ]),
            )
        )
        if existing_job := existing.scalar_one_or_none():
            return existing_job

        # Create new job
        idempotency_key = self.generate_idempotency_key(user_id, channel_id, media_id)
        job = UploadJob(
            user_id=user_id,
            channel_id=channel_id,
            media_id=media_id,
            idempotency_key=idempotency_key,
            title=metadata.get("title"),
            description=metadata.get("description"),
            tags=metadata.get("tags"),
            hashtags=metadata.get("hashtags"),
            category_id=metadata.get("category_id", "22"),
            privacy_status=metadata.get("privacy_status", "private"),
            scheduled_at=metadata.get("scheduled_at"),
            playlist_id=metadata.get("playlist_id"),
            thumbnail_filename=metadata.get("thumbnail_filename"),
        )
        db.add(job)

        # Create idempotency key record
        idempotency_record = IdempotencyKey(
            key=idempotency_key,
            job_id=job.id,
            user_id=user_id,
            expires_at=datetime.utcnow() + timedelta(hours=24),
        )
        db.add(idempotency_record)

        await db.flush()
        return job

    async def get_job(self, db: AsyncSession, job_id: int) -> Optional[UploadJob]:
        """Get upload job by ID."""
        result = await db.execute(select(UploadJob).where(UploadJob.id == job_id))
        return result.scalar_one_or_none()

    async def get_user_jobs(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[UploadJob]:
        """Get user's upload jobs."""
        result = await db.execute(
            select(UploadJob)
            .where(UploadJob.user_id == user_id)
            .order_by(UploadJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_job_status(
        self,
        db: AsyncSession,
        job: UploadJob,
        status: JobStatus,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update job status with error tracking."""
        job.status = status
        job.last_attempt_at = datetime.utcnow()

        if error_code:
            job.error_code = error_code
        if error_message:
            job.error_message = error_message

        if status == JobStatus.PUBLISHED:
            job.completed_at = datetime.utcnow()

        await db.flush()

    async def process_upload(self, db: AsyncSession, job: UploadJob) -> None:
        """Process an upload job."""
        try:
            # Update status to processing
            await self.update_job_status(db, job, JobStatus.PROCESSING)

            # Get media file
            media = job.media
            if not media or media.status != MediaStatus.VALID:
                raise ValueError("Invalid or missing media file")

            # Get channel credentials
            channel = job.channel
            if not channel or channel.token_status != TokenStatus.VALID:
                raise ValueError("Invalid or missing channel credentials")

            # Decrypt tokens
            access_token = encryption_service.decrypt(channel.encrypted_access_token)
            refresh_token = encryption_service.decrypt(channel.encrypted_refresh_token)

            # Create credentials
            credentials = youtube_service.get_credentials_from_tokens(
                access_token, refresh_token, channel.token_expires_at
            )

            # Download media file to temporary location
            await self.update_job_status(db, job, JobStatus.UPLOADING)
            file_data = await media.storage_backend.download(media.storage_key)

            # Save to temp file for upload
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            try:
                # Prepare metadata
                metadata = {
                    "title": job.title or "Untitled Video",
                    "description": job.description or "",
                    "tags": self._parse_tags(job.tags, job.hashtags),
                    "category_id": job.category_id,
                    "privacy_status": job.privacy_status,
                    "scheduled_at": job.scheduled_at,
                    "mime_type": media.mime_type,
                }

                # Upload to YouTube
                result = await youtube_service.upload_video(
                    credentials, tmp_path, metadata
                )

                # Update job with YouTube video details
                job.youtube_video_id = result["video_id"]
                job.youtube_video_url = result["video_url"]

                # Upload thumbnail if provided
                if job.thumbnail_storage_key:
                    thumbnail_data = await media.storage_backend.download(
                        job.thumbnail_storage_key
                    )
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as thumb_tmp:
                        thumb_tmp.write(thumbnail_data)
                        thumb_path = thumb_tmp.name

                    try:
                        await youtube_service.upload_thumbnail(
                            credentials, result["video_id"], thumb_path
                        )
                    finally:
                        import os
                        os.unlink(thumb_path)

                # Add to playlist if specified
                if job.playlist_id:
                    await youtube_service.add_to_playlist(
                        credentials, result["video_id"], job.playlist_id
                    )

                # Mark as published
                await self.update_job_status(db, job, JobStatus.PUBLISHED)

            finally:
                import os
                os.unlink(tmp_path)

        except Exception as e:
            # Handle failure with retry logic
            job.attempt_count += 1
            if job.attempt_count >= job.max_attempts:
                await self.update_job_status(
                    db, job, JobStatus.FAILED,
                    error_code="MAX_ATTEMPTS_EXCEEDED",
                    error_message=str(e),
                )
            else:
                # Schedule retry with exponential backoff
                retry_delay = 2 ** job.attempt_count * 60  # 2, 4, 8 minutes
                job.next_retry_at = datetime.utcnow() + timedelta(seconds=retry_delay)
                await self.update_job_status(
                    db, job, JobStatus.RETRYING,
                    error_code=type(e).__name__,
                    error_message=str(e),
                )

    def _parse_tags(self, tags: Optional[str], hashtags: Optional[str]) -> list[str]:
        """Parse tags and hashtags into a combined list."""
        from src.services.hashtag import hashtag_service

        all_tags = []
        if tags:
            all_tags.extend(tags.split(","))
        if hashtags:
            all_tags.extend(hashtags.split(","))

        return hashtag_service.normalize_hashtags(all_tags)


# Singleton instance
job_service = JobService()
