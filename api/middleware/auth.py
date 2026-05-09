from __future__ import annotations

from fastapi import Header, HTTPException, status

from core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Accept either the canonical arda key or the legacy earendil key.

    The legacy key is honored so the existing MCP server (which still
    sends earendil_api_key) keeps working through the cutover.
    """
    valid = {settings.arda_api_key, settings.earendil_api_key}
    if x_api_key not in valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
