"""Unit tests for YouTube service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from src.services.youtube import YouTubeService


class TestYouTubeService:
    """Test YouTube service operations."""

    def setup_method(self):
        self.service = YouTubeService()

    def test_get_authorization_url(self):
        """Test OAuth authorization URL generation."""
        url = self.service.get_authorization_url("test-state")

        assert "accounts.google.com" in url
        assert "test-state" in url
        assert "client_id=" in url

    @pytest.mark.asyncio
    async def test_exchange_code(self):
        """Test OAuth code exchange."""
        mock_response = AsyncMock()
        # httpx's Response.json() is synchronous, so it must be mocked with a
        # plain Mock. An AsyncMock here makes .json() return an un-awaited
        # coroutine instead of the dict, which is what failed before.
        mock_response.json = MagicMock(return_value={
            "access_token": "ya29.test-token",
            "refresh_token": "1//test-refresh",
            "expires_in": 3600,
        })
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await self.service.exchange_code("test-code")

            assert result["access_token"] == "ya29.test-token"
            assert result["refresh_token"] == "1//test-refresh"

    @pytest.mark.asyncio
    async def test_refresh_token(self):
        """Test token refresh."""
        mock_response = AsyncMock()
        # See test_exchange_code: .json() is synchronous on httpx responses.
        mock_response.json = MagicMock(return_value={
            "access_token": "ya29.new-token",
            "expires_in": 3600,
        })
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await self.service.refresh_token("old-refresh-token")

            assert result["access_token"] == "ya29.new-token"

    def test_get_credentials_from_tokens(self):
        """Test credential creation from tokens."""
        expires_at = datetime.utcnow() + timedelta(hours=1)

        credentials = self.service.get_credentials_from_tokens(
            access_token="ya29.test-token",
            refresh_token="1//test-refresh",
            expires_at=expires_at,
        )

        assert credentials.token == "ya29.test-token"
        assert credentials.refresh_token == "1//test-refresh"
        assert credentials.expiry == expires_at

    @pytest.mark.asyncio
    async def test_get_channel_info(self):
        """Test getting channel information."""
        mock_youtube = MagicMock()
        mock_youtube.channels().list().execute.return_value = {
            "items": [
                {
                    "id": "UC1234567890",
                    "snippet": {
                        "title": "Test Channel",
                        "thumbnails": {
                            "default": {"url": "https://example.com/thumb.jpg"}
                        },
                    },
                    "statistics": {"subscriberCount": "1000"},
                }
            ]
        }

        with patch.object(self.service, "get_youtube_client", return_value=mock_youtube):
            credentials = MagicMock()
            result = await self.service.get_channel_info(credentials)

            assert result["channel_id"] == "UC1234567890"
            assert result["channel_name"] == "Test Channel"

    @pytest.mark.asyncio
    async def test_get_channel_info_no_channel(self):
        """Test getting channel info when no channel exists."""
        mock_youtube = MagicMock()
        mock_youtube.channels().list().execute.return_value = {"items": []}

        with patch.object(self.service, "get_youtube_client", return_value=mock_youtube):
            credentials = MagicMock()
            with pytest.raises(ValueError, match="No YouTube channel found"):
                await self.service.get_channel_info(credentials)

    @pytest.mark.asyncio
    async def test_list_playlists(self):
        """Test listing user playlists."""
        mock_youtube = MagicMock()
        mock_youtube.playlists().list().execute.return_value = {
            "items": [
                {
                    "id": "PL1234567890",
                    "snippet": {"title": "My Playlist"},
                    "contentDetails": {"itemCount": 10},
                }
            ]
        }

        with patch.object(self.service, "get_youtube_client", return_value=mock_youtube):
            credentials = MagicMock()
            result = await self.service.list_playlists(credentials)

            assert len(result) == 1
            assert result[0]["playlist_id"] == "PL1234567890"
            assert result[0]["title"] == "My Playlist"
