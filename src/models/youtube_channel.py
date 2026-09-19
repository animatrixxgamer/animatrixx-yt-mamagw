"""YouTube channel model with encrypted OAuth tokens."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class TokenStatus(str, enum.Enum):
    """OAuth token status."""
    VALID = "valid"
    EXPIRED = "expired"
    REVOKED = "revoked"


class YouTubeChannel(Base):
    """Connected YouTube channel with encrypted credentials."""
    
    __tablename__ = "youtube_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(255), index=True)
    channel_name: Mapped[str] = mapped_column(String(255))
    channel_title: Mapped[Optional[str]] = mapped_column(String(255))
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    
    # Encrypted OAuth tokens (Fernet encryption)
    encrypted_access_token: Mapped[Text] = mapped_column(Text)
    encrypted_refresh_token: Mapped[Text] = mapped_column(Text)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    token_status: Mapped[TokenStatus] = mapped_column(
        Enum(TokenStatus), default=TokenStatus.VALID
    )
    
    # YouTube settings
    default_privacy_status: Mapped[str] = mapped_column(String(50), default="private")
    default_category_id: Mapped[str] = mapped_column(String(10), default="22")
    default_language: Mapped[str] = mapped_column(String(10), default="en")
    
    # Usage tracking
    upload_quota_used: Mapped[int] = mapped_column(default=0)
    last_upload_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="channels")
    upload_jobs: Mapped[list["UploadJob"]] = relationship(
        "UploadJob", back_populates="channel"
    )

    __table_args__ = (
        Index("ix_youtube_channels_user_channel", "user_id", "channel_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<YouTubeChannel {self.channel_name} ({self.token_status.value})>"
