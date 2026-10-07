"""Common DTOs shared across the API surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    """The consistent error body rendered by the exception handlers in ``app.main``."""

    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class HealthResponse(BaseModel):
    """Liveness payload."""

    status: str
    service: str
    version: str


class PageParams(BaseModel):
    """Standard pagination query parameters."""

    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
