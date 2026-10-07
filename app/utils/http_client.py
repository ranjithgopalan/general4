"""
Correlation-aware HTTP client utilities.

Provides a pre-configured httpx.AsyncClient factory that automatically
injects X-Correlation-ID into every outbound request from the FastAPI
process, propagating the current request's correlation ID to downstream
services (Spring Boot gateway, audit-data, etc.).

Usage:
    async with get_http_client() as client:
        resp = await client.get("http://gateway:8080/api/auth/me")

The correlation ID is read from the aidlc_correlation_id ContextVar set by
ActorContextMiddleware, so it always matches the current inbound request.
No credentials are injected here — callers must add Authorization headers
if downstream services require them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from app.utils.request_context import get_correlation_id

_CORRELATION_HEADER = "X-Correlation-ID"
_REQUEST_ID_HEADER = "X-Request-ID"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class CorrelationPropagatingTransport(httpx.AsyncHTTPTransport):
    """Injects the current correlation ID into every outbound request."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        cid = get_correlation_id()
        if cid:
            request.headers[_CORRELATION_HEADER] = cid
            request.headers[_REQUEST_ID_HEADER] = cid
        return await super().handle_async_request(request)


@asynccontextmanager
async def get_http_client(
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    base_url: str = "",
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a correlation-aware httpx.AsyncClient for downstream service calls."""
    transport = CorrelationPropagatingTransport()
    async with httpx.AsyncClient(
        transport=transport,
        base_url=base_url,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        yield client
