"""Bot command and message handlers."""

from src.bot.handlers.start import router as start_router
from src.bot.handlers.youtube import router as youtube_router
from src.bot.handlers.upload import router as upload_router
from src.bot.handlers.metadata import router as metadata_router
from src.bot.handlers.status import router as status_router
from src.bot.handlers.admin import router as admin_router

__all__ = [
    "start_router",
    "youtube_router",
    "upload_router",
    "metadata_router",
    "status_router",
    "admin_router",
]
