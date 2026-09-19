"""Media asset model for uploaded videos."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class MediaStatus(str, enum.Enum):
    """Media processing status."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADING_COMPLETE = "downloading_complete"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    UPLOADING = "uploading"
    UPLOADING_COMPLETE = "uploading_complete"
    FAILED = "failed"
    CLEANED_UP = "cleaned_up"


class MediaSource(str, enum.Enum):
    """Source of the media file."""
    TELEGRAM = "telegram"
    TIKTOK = "tiktok"
    URL = "url"
    DIRECT = "direct"


class MediaAsset(Base):
    """Uploaded media file with metadata."""
    
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(512), index=True)
    
    # File information
    filename: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[Optional[str]] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(BigInteger)
    
    # Storage
    storage_backend: Mapped[str] = mapped_column(String(50))
    storage_bucket: Mapped[Optional[str]] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024))
    
    # Processing status
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus), default=MediaStatus.PENDING, index=True
    )
    status_message: Mapped[Optional[str]] = mapped_column(Text)
    
    # Source information
    source: Mapped[MediaSource] = mapped_column(
        Enum(MediaSource), default=MediaSource.TELEGRAM
    )
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    source_metadata: Mapped[Optional[str]] = mapped_column(Text)  # JSON
    
    # Virus scan results
    scan_status: Mapped[Optional[str]] = mapped_column(String(50))
    scan_result: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="media_assets")
    upload_job: Mapped[Optional["UploadJob"]] = relationship(
        "UploadJob", back_populates="media", uselist=False
    )

    def __repr__(self) -> str:
        return f"<MediaAsset {self.filename} ({self.status.value})>"
