"""Upload job model for YouTube uploads."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class JobStatus(str, enum.Enum):
    """Upload job status with state transitions."""
    PENDING = "pending"
    PROCESSING = "processing"
    UPLOADING = "uploading"
    PROCESSING_METADATA = "processing_metadata"
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class UploadJob(Base):
    """YouTube upload job with metadata and retry logic."""
    
    __tablename__ = "upload_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("youtube_channels.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_assets.id"), index=True)
    
    # Job tracking
    idempotency_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.PENDING, index=True
    )
    
    # YouTube video details
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(50))
    youtube_video_url: Mapped[Optional[str]] = mapped_column(Text)
    
    # Upload metadata
    title: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[str]] = mapped_column(Text)  # Comma-separated
    hashtags: Mapped[Optional[str]] = mapped_column(Text)  # Comma-separated
    category_id: Mapped[str] = mapped_column(String(10), default="22")
    privacy_status: Mapped[str] = mapped_column(String(50), default="private")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    playlist_id: Mapped[Optional[str]] = mapped_column(String(100))
    
    # Thumbnail
    thumbnail_filename: Mapped[Optional[str]] = mapped_column(String(512))
    thumbnail_storage_key: Mapped[Optional[str]] = mapped_column(String(1024))
    
    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Error tracking
    error_code: Mapped[Optional[str]] = mapped_column(String(100))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    
    # Additional metadata (JSON for flexibility).
    # NOTE: do not name this attribute `metadata` — SQLAlchemy's Declarative API
    # reserves that name for Base.metadata and raises InvalidRequestError at
    # import time, which broke the entire ORM layer.
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)
    
    # Quota tracking
    quota_cost: Mapped[int] = mapped_column(default=0)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="upload_jobs")
    channel: Mapped["YouTubeChannel"] = relationship(
        "YouTubeChannel", back_populates="upload_jobs"
    )
    media: Mapped["MediaAsset"] = relationship("MediaAsset", back_populates="upload_job")

    __table_args__ = (
        Index("ix_upload_jobs_user_status", "user_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<UploadJob {self.id} ({self.status.value})>"


class IdempotencyKey(Base):
    """Idempotency keys for preventing duplicate operations."""
    
    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("upload_jobs.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<IdempotencyKey {self.key}>"
