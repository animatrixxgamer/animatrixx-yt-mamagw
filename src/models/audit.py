"""Audit event model for tracking important actions."""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class AuditAction(str, enum.Enum):
    """Types of auditable actions."""
    USER_CREATED = "user_created"
    USER_LOGIN = "user_login"
    CHANNEL_CONNECTED = "channel_connected"
    CHANNEL_DISCONNECTED = "channel_disconnected"
    TOKEN_REFRESHED = "token_refreshed"
    VIDEO_UPLOADED = "video_uploaded"
    VIDEO_PUBLISHED = "video_published"
    VIDEO_SCHEDULED = "video_scheduled"
    JOB_CREATED = "job_created"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"
    JOB_RETRYING = "job_retrying"
    MEDIA_UPLOADED = "media_uploaded"
    MEDIA_DELETED = "media_deleted"
    ADMIN_ACTION = "admin_action"


class AuditEvent(Base):
    """Audit log entry for compliance and debugging."""
    
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(Text, index=True)
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
    
    # Event details
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    ip_address: Mapped[Optional[str]] = mapped_column(Text)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    
    # Success/failure tracking
    success: Mapped[bool] = mapped_column(default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        Index("ix_audit_events_user_action", "user_id", "action"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditEvent {self.action.value} at {self.created_at}>"
