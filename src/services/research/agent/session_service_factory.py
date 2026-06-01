"""Backward-compatible import path for runtime session service factory."""

from ..runtime.session_service import build_session_service

__all__ = ["build_session_service"]
