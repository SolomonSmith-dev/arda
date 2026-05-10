from __future__ import annotations

from fastapi import Header, HTTPException, status

from core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != settings.arda_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
