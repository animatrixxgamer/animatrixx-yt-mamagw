"""Web routes for OAuth callbacks and API endpoints."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.youtube_channel import YouTubeChannel, TokenStatus
from src.models.user import User
from src.services.youtube import youtube_service
from src.services.encryption import encryption_service
from src.services.audit import audit_service
from src.models.audit import AuditAction

router = APIRouter()


class OAuthCallbackResponse(BaseModel):
    """OAuth callback response model."""
    success: bool
    message: str
    channel_name: Optional[str] = None
    channel_id: Optional[str] = None


@router.get("/oauth/youtube/callback")
async def youtube_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle YouTube OAuth callback."""
    try:
        # Exchange code for tokens
        token_data = await youtube_service.exchange_code(code)

        # Create credentials to get channel info
        credentials = youtube_service.get_credentials_from_tokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600)),
        )

        # Get channel information
        channel_info = await youtube_service.get_channel_info(credentials)

        # TODO: Extract user_id from state token
        # For now, this is a placeholder
        user_id = 1  # Should be extracted from state

        # Encrypt tokens
        encrypted_access = encryption_service.encrypt(token_data["access_token"])
        encrypted_refresh = encryption_service.encrypt(token_data.get("refresh_token", ""))

        # Create or update channel record
        result = await db.execute(
            select(YouTubeChannel).where(
                YouTubeChannel.channel_id == channel_info["channel_id"],
                YouTubeChannel.user_id == user_id,
            )
        )
        channel = result.scalar_one_or_none()

        if channel:
            # Update existing channel
            channel.encrypted_access_token = encrypted_access
            channel.encrypted_refresh_token = encrypted_refresh
            channel.token_expires_at = datetime.utcnow() + timedelta(
                seconds=token_data.get("expires_in", 3600)
            )
            channel.token_status = TokenStatus.VALID
        else:
            # Create new channel
            channel = YouTubeChannel(
                user_id=user_id,
                channel_id=channel_info["channel_id"],
                channel_name=channel_info["channel_name"],
                channel_title=channel_info.get("channel_title"),
                thumbnail_url=channel_info.get("thumbnail_url"),
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                token_expires_at=datetime.utcnow() + timedelta(
                    seconds=token_data.get("expires_in", 3600)
                ),
                token_status=TokenStatus.VALID,
            )
            db.add(channel)

        await db.flush()

        # Audit log
        await audit_service.log_channel_action(
            db,
            AuditAction.CHANNEL_CONNECTED,
            user_id,
            channel_info["channel_id"],
            details={"channel_name": channel_info["channel_name"]},
        )

        # Redirect to success page
        return RedirectResponse(
            url=f"/success?channel={channel_info['channel_name']}",
            status_code=302,
        )

    except Exception as e:
        # Redirect to error page
        return RedirectResponse(
            url=f"/error?message={str(e)}",
            status_code=302,
        )


@router.get("/success")
async def success_page(channel: str):
    """Success page after OAuth."""
    return {
        "success": True,
        "message": f"Successfully connected channel: {channel}",
    }


@router.get("/error")
async def error_page(message: str):
    """Error page after OAuth."""
    return {
        "success": False,
        "message": f"Error connecting channel: {message}",
    }


@router.post("/api/channels/{channel_id}/refresh")
async def refresh_channel_token(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Refresh channel OAuth token."""
    result = await db.execute(
        select(YouTubeChannel).where(YouTubeChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    try:
        # Decrypt refresh token
        refresh_token = encryption_service.decrypt(channel.encrypted_refresh_token)

        # Refresh token
        token_data = await youtube_service.refresh_token(refresh_token)

        # Update channel
        channel.encrypted_access_token = encryption_service.encrypt(
            token_data["access_token"]
        )
        channel.token_expires_at = datetime.utcnow() + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )
        channel.token_status = TokenStatus.VALID

        await db.flush()

        return {"success": True, "message": "Token refreshed"}

    except Exception as e:
        channel.token_status = TokenStatus.EXPIRED
        await db.flush()
        raise HTTPException(status_code=400, detail=str(e))
