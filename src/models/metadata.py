"""Metadata draft model for upload configuration."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class MetadataDraft(Base):
    """Draft metadata for video upload configuration."""
    
    __tablename__ = "metadata_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("upload_jobs.id"), index=True
    )
    
    # Title and description
    title: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    # Tags and hashtags
    tags: Mapped[Optional[str]] = mapped_column(Text)
    hashtags: Mapped[Optional[str]] = mapped_column(Text)
    
    # Privacy and scheduling
    privacy_status: Mapped[str] = mapped_column(Text, default="private")
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Category and language
    category_id: Mapped[str] = mapped_column(Text, default="22")
    language: Mapped[str] = mapped_column(Text, default="en")
    timezone: Mapped[str] = mapped_column(Text, default="UTC")
    
    # Playlist
    playlist_id: Mapped[Optional[str]] = mapped_column(Text)
    playlist_title: Mapped[Optional[str]] = mapped_column(Text)
    
    # Thumbnail
    thumbnail_file_id: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_filename: Mapped[Optional[str]] = mapped_column(Text)
    
    # Additional options
    options: Mapped[Optional[dict]] = mapped_column(JSONB)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    job: Mapped[Optional["UploadJob"]] = relationship("UploadJob")

    def __repr__(self) -> str:
        return f"<MetadataDraft {self.title}>"
