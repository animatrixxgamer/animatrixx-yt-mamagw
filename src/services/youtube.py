"""YouTube service for OAuth and video uploads."""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import httpx

from src.config import get_settings
from src.services.encryption import encryption_service

settings = get_settings()

# YouTube API constants
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
UPLOAD_CHUNK_SIZE = 1024 * 1024 * 10  # 10MB chunks for resumable upload


class YouTubeService:
    """Service for YouTube OAuth and video operations."""

    def __init__(self) -> None:
        self.client_id = settings.youtube_client_id
        self.client_secret = settings.youtube_client_secret
        self.redirect_uri = settings.youtube_redirect_uri
        self.scopes = settings.youtube_scopes_list

    def get_authorization_url(self, state: str) -> str:
        """Generate Google OAuth authorization URL."""
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=self.scopes,
        )
        flow.redirect_uri = self.redirect_uri

        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            state=state,
            prompt="consent",
        )
        return authorization_url

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            return response.json()

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            return response.json()

    def get_youtube_client(self, credentials: Credentials) -> Any:
        """Build YouTube API client from credentials."""
        return build(
            YOUTUBE_API_SERVICE_NAME,
            YOUTUBE_API_VERSION,
            credentials=credentials,
        )

    def get_credentials_from_tokens(
        self, access_token: str, refresh_token: str, expires_at: Optional[datetime]
    ) -> Credentials:
        """Create Credentials object from tokens."""
        return Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            expiry=expires_at,
        )

    async def get_channel_info(self, credentials: Credentials) -> Dict[str, Any]:
        """Get authenticated user's YouTube channel information."""
        youtube = self.get_youtube_client(credentials)
        response = youtube.channels().list(
            part="snippet,statistics",
            mine=True,
        ).execute()

        if not response.get("items"):
            raise ValueError("No YouTube channel found for this account")

        channel = response["items"][0]
        return {
            "channel_id": channel["id"],
            "channel_name": channel["snippet"]["title"],
            "channel_title": channel["snippet"].get("title"),
            "thumbnail_url": channel["snippet"]["thumbnails"].get("default", {}).get("url"),
        }

    async def upload_video(
        self,
        credentials: Credentials,
        file_path: str,
        metadata: Dict[str, Any],
        on_progress: Optional[callable] = None,
    ) -> Dict[str, Any]:
        """Upload a video to YouTube with resumable upload support."""
        youtube = self.get_youtube_client(credentials)

        body = {
            "snippet": {
                "title": metadata.get("title", "Untitled Video"),
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
                "categoryId": metadata.get("category_id", "22"),
                "defaultLanguage": metadata.get("language", "en"),
            },
            "status": {
                "privacyStatus": metadata.get("privacy_status", "private"),
                "selfDeclaredMadeForKids": False,
            },
        }

        # Add scheduling if provided
        if metadata.get("scheduled_at"):
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = metadata["scheduled_at"].isoformat() + "Z"

        # Create resumable upload
        media = MediaFileUpload(
            file_path,
            mimetype=metadata.get("mime_type", "video/mp4"),
            resumable=True,
            chunksize=UPLOAD_CHUNK_SIZE,
        )

        insert_request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = insert_request.next_chunk()
            if status and on_progress:
                on_progress(status.progress())

        return {
            "video_id": response["id"],
            "video_url": f"https://youtu.be/{response['id']}",
            "title": response["snippet"]["title"],
            "privacy_status": response["status"]["privacyStatus"],
        }

    async def update_video_metadata(
        self,
        credentials: Credentials,
        video_id: str,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update video metadata after upload."""
        youtube = self.get_youtube_client(credentials)

        body = {
            "id": video_id,
            "snippet": {
                "title": metadata.get("title"),
                "description": metadata.get("description"),
                "tags": metadata.get("tags", []),
                "categoryId": metadata.get("category_id"),
            },
        }

        response = youtube.videos().update(
            part="snippet",
            body=body,
        ).execute()

        return {
            "video_id": response["id"],
            "title": response["snippet"]["title"],
        }

    async def upload_thumbnail(
        self,
        credentials: Credentials,
        video_id: str,
        file_path: str,
    ) -> bool:
        """Upload a custom thumbnail for a video."""
        youtube = self.get_youtube_client(credentials)

        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(
                    file_path,
                    mimetype="image/jpeg",
                    resumable=False,
                ),
            ).execute()
            return True
        except HttpError as e:
            if e.resp.status == 403:
                # Thumbnail upload not allowed for this channel
                return False
            raise

    async def add_to_playlist(
        self,
        credentials: Credentials,
        video_id: str,
        playlist_id: str,
    ) -> bool:
        """Add a video to a playlist."""
        youtube = self.get_youtube_client(credentials)

        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    }
                },
            ).execute()
            return True
        except HttpError:
            return False

    async def list_playlists(self, credentials: Credentials) -> List[Dict[str, Any]]:
        """List user's YouTube playlists."""
        youtube = self.get_youtube_client(credentials)
        response = youtube.playlists().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
        ).execute()

        return [
            {
                "playlist_id": item["id"],
                "title": item["snippet"]["title"],
                "item_count": item["contentDetails"]["itemCount"],
            }
            for item in response.get("items", [])
        ]


# Singleton instance
youtube_service = YouTubeService()
