"""User model with role-based access control."""

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class UserRole(str, enum.Enum):
    """User roles for authorization."""
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """Telegram user with authorization role."""
    
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(default=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    channels: Mapped[List["YouTubeChannel"]] = relationship(
        "YouTubeChannel", back_populates="user", cascade="all, delete-orphan"
    )
    media_assets: Mapped[List["MediaAsset"]] = relationship(
        "MediaAsset", back_populates="user", cascade="all, delete-orphan"
    )
    upload_jobs: Mapped[List["UploadJob"]] = relationship(
        "UploadJob", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.telegram_id} ({self.role.value})>"
