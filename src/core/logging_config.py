"""Logging Configuration Module - Configures loguru for the application."""

import sys

from loguru import logger

from .config import settings


def setup_logging():
    """
    Configure loguru based on application settings.

    Configures:
    - Console logging with color formatting
    - Log level from settings.LOG_LEVEL
    - File logging (production only)
    - Log rotation and retention
    """
    # Remove default handler
    logger.remove()

    # Add console handler with color formatting
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # Add file logging in production
    if not settings.DEBUG:
        logger.add(
            "logs/app.log",
            level=settings.LOG_LEVEL,
            rotation="500 MB",
            retention="10 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        )
        logger.info("File logging enabled (production mode)")

    logger.info(f"Logging configured with level: {settings.LOG_LEVEL}")
