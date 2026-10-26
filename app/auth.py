"""API key authentication dependency."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def verify_api_key(x_api_key: str = Header("", alias="X-API-Key")) -> str:
    """
    Validate the X-API-Key header against the configured API key.

    Raises HTTP 401 if the key is missing or invalid.
    """
    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"success": False, "error": "Unauthorized"},
        )
    return x_api_key
