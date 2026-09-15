"""Media storage service for file management."""

import hashlib
import os
from datetime import datetime, timedelta
from typing import BinaryIO, Dict, Optional

import aiofiles
import boto3
from botocore.exceptions import ClientError

from src.config import get_settings

settings = get_settings()


class MediaStorageBackend:
    """Abstract storage backend interface."""

    async def upload(self, key: str, data: BinaryIO, content_type: str) -> Dict[str, str]:
        """Upload file to storage."""
        raise NotImplementedError

    async def download(self, key: str) -> bytes:
        """Download file from storage."""
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        """Delete file from storage."""
        raise NotImplementedError

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Get temporary download URL."""
        raise NotImplementedError


class LocalStorageBackend(MediaStorageBackend):
    """Local filesystem storage for development."""

    def __init__(self, base_path: str = "./media") -> None:
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    async def upload(self, key: str, data: BinaryIO, content_type: str) -> Dict[str, str]:
        """Upload file to local storage."""
        file_path = os.path.join(self.base_path, key)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        async with aiofiles.open(file_path, "wb") as f:
            content = data.read()
            await f.write(content)

        return {
            "key": key,
            "size": str(len(content)),
            "content_type": content_type,
        }

    async def download(self, key: str) -> bytes:
        """Download file from local storage."""
        file_path = os.path.join(self.base_path, key)
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def delete(self, key: str) -> bool:
        """Delete file from local storage."""
        file_path = os.path.join(self.base_path, key)
        try:
            os.remove(file_path)
            return True
        except FileNotFoundError:
            return False

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Get local file URL (not truly presigned)."""
        return f"file://{os.path.abspath(os.path.join(self.base_path, key))}"


class S3StorageBackend(MediaStorageBackend):
    """S3-compatible storage backend for production."""

    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        self.bucket = settings.storage_bucket

    async def upload(self, key: str, data: BinaryIO, content_type: str) -> Dict[str, str]:
        """Upload file to S3."""
        self.client.upload_fileobj(
            data,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return {
            "key": key,
            "size": str(head["ContentLength"]),
            "content_type": content_type,
        }

    async def download(self, key: str) -> bytes:
        """Download file from S3."""
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    async def delete(self, key: str) -> bool:
        """Delete file from S3."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Get presigned download URL."""
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


class MediaService:
    """Service for managing media files."""

    def __init__(self) -> None:
        if settings.storage_backend == "s3":
            self.storage = S3StorageBackend()
        else:
            self.storage = LocalStorageBackend()

    def generate_storage_key(self, user_id: int, filename: str) -> str:
        """Generate unique storage key for a file."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        random_hash = hashlib.md5(f"{user_id}_{filename}_{timestamp}".encode()).hexdigest()[:8]
        ext = os.path.splitext(filename)[1]
        return f"media/{user_id}/{timestamp}_{random_hash}{ext}"

    async def upload_file(
        self,
        user_id: int,
        filename: str,
        data: BinaryIO,
        content_type: str,
    ) -> Dict[str, str]:
        """Upload a file and return storage details."""
        key = self.generate_storage_key(user_id, filename)
        result = await self.storage.upload(key, data, content_type)
        return {
            **result,
            "filename": filename,
            "content_type": content_type,
        }

    async def download_file(self, key: str) -> bytes:
        """Download a file from storage."""
        return await self.storage.download(key)

    async def delete_file(self, key: str) -> bool:
        """Delete a file from storage."""
        return await self.storage.delete(key)

    async def get_file_url(self, key: str, expires_in: int = 3600) -> str:
        """Get a temporary URL for a file."""
        return await self.storage.get_presigned_url(key, expires_in)

    def validate_video_file(self, filename: str, file_size: int, mime_type: str) -> bool:
        """Validate that a file is a supported video format."""
        # Check file extension
        ext = os.path.splitext(filename)[1].lower()
        if ext not in settings.supported_video_formats_list:
            return False

        # Check file size
        if file_size > settings.max_file_size_bytes:
            return False

        # Check MIME type
        valid_mimes = [
            "video/mp4",
            "video/quicktime",
            "video/x-msvideo",
            "video/x-matroska",
            "video/webm",
        ]
        if mime_type not in valid_mimes:
            return False

        return True


# Singleton instance
media_service = MediaService()
