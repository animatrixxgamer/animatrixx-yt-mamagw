"""Unit tests for media service."""

import io
import pytest

from src.services.media import MediaService, LocalStorageBackend


class TestMediaValidation:
    """Test media file validation."""

    def setup_method(self):
        self.service = MediaService()

    def test_validate_valid_mp4(self):
        """Test validation of valid MP4 file."""
        assert self.service.validate_video_file(
            "video.mp4",
            1024 * 1024,  # 1MB
            "video/mp4",
        )

    def test_validate_valid_mov(self):
        """Test validation of valid MOV file."""
        assert self.service.validate_video_file(
            "video.mov",
            1024 * 1024,
            "video/quicktime",
        )

    def test_validate_invalid_extension(self):
        """Test validation of invalid file extension."""
        assert not self.service.validate_video_file(
            "video.txt",
            1024 * 1024,
            "video/mp4",
        )

    def test_validate_invalid_mime_type(self):
        """Test validation of invalid MIME type."""
        assert not self.service.validate_video_file(
            "video.mp4",
            1024 * 1024,
            "text/plain",
        )

    def test_validate_too_large(self):
        """Test validation of file that's too large."""
        assert not self.service.validate_video_file(
            "video.mp4",
            1024 * 1024 * 1024 * 3,  # 3GB (exceeds 2GB limit)
            "video/mp4",
        )

    def test_generate_storage_key(self):
        """Test storage key generation."""
        key = self.service.generate_storage_key(123, "test_video.mp4")
        assert "media/123/" in key
        assert key.endswith(".mp4")


class TestLocalStorageBackend:
    """Test local storage backend."""

    def setup_method(self):
        self.backend = LocalStorageBackend("/tmp/test_media")

    @pytest.mark.asyncio
    async def test_upload_and_download(self):
        """Test file upload and download."""
        data = io.BytesIO(b"test content")
        result = await self.backend.upload("test.txt", data, "text/plain")

        assert result["key"] == "test.txt"
        assert result["size"] == str(len(b"test content"))

        downloaded = await self.backend.download("test.txt")
        assert downloaded == b"test content"

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test file deletion."""
        data = io.BytesIO(b"test content")
        await self.backend.upload("test.txt", data, "text/plain")

        deleted = await self.backend.delete("test.txt")
        assert deleted is True

        # Try to download deleted file
        with pytest.raises(FileNotFoundError):
            await self.backend.download("test.txt")

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        """Test deletion of nonexistent file."""
        deleted = await self.backend.delete("nonexistent.txt")
        assert deleted is False
