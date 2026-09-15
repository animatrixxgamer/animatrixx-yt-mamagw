"""Audit logging service for compliance."""

from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit import AuditEvent, AuditAction


class AuditService:
    """Service for creating audit log entries."""

    async def log_event(
        self,
        db: AsyncSession,
        action: AuditAction,
        user_id: Optional[int] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> AuditEvent:
        """Create an audit log entry."""
        event = AuditEvent(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            error_message=error_message,
        )
        db.add(event)
        await db.flush()
        return event

    async def log_user_action(
        self,
        db: AsyncSession,
        action: AuditAction,
        user_id: int,
        details: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AuditEvent:
        """Log a user action."""
        return await self.log_event(
            db, action, user_id=user_id, details=details, **kwargs
        )

    async def log_channel_action(
        self,
        db: AsyncSession,
        action: AuditAction,
        user_id: int,
        channel_id: str,
        details: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AuditEvent:
        """Log a channel-related action."""
        return await self.log_event(
            db,
            action,
            user_id=user_id,
            resource_type="youtube_channel",
            resource_id=channel_id,
            details=details,
            **kwargs,
        )

    async def log_job_action(
        self,
        db: AsyncSession,
        action: AuditAction,
        user_id: int,
        job_id: int,
        details: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AuditEvent:
        """Log a job-related action."""
        return await self.log_event(
            db,
            action,
            user_id=user_id,
            resource_type="upload_job",
            resource_id=str(job_id),
            details=details,
            **kwargs,
        )


# Singleton instance
audit_service = AuditService()
