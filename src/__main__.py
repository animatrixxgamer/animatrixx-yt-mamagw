"""Main module entry point for running the application."""

import uvicorn

from src.config import get_settings

settings = get_settings()


def run():
    """Run the application."""
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.app_debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
