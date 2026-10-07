"""stdio MCP client for the knowledge base's KB server.

the knowledge base already ships `backend/mcp/kb_server.py` (FastMCP over stdio) exposing
list_projects, get_kb_health, query_claims, get_claim, get_context_package,
search_kb and list_sources -- all read-only. This is the client side, which the knowledge base
does not have (`create_sdk_mcp_server` and `mcp_servers=` appear nowhere in it).

Used for the *service-side* MCP path. Granting MCP tools to the agent itself is
a different concern -- see `fe_core.kb.grants`.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class McpUnavailableError(RuntimeError):
    """The MCP server could not be reached or the `mcp` package is missing."""


class KbMcpClient:
    """Short-lived stdio sessions, one per call.

    A long-lived session would be more efficient, but the KB server opens its
    own database pool per process; holding one open from a request-scoped API
    would leak connections under load. Stage runs are minutes long, so the
    subprocess spawn cost is irrelevant.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
    ):
        self.command = command
        self.args = args or []
        self.cwd = cwd
        self.env = env
        self.timeout = timeout

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:  # pragma: no cover
            raise McpUnavailableError(
                "the `mcp` package is not installed; `pip install mcp`"
            ) from exc

        params = StdioServerParameters(
            command=self.command, args=self.args, cwd=self.cwd, env=self.env
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except McpUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise McpUnavailableError(
                f"could not start the KB MCP server "
                f"(`{self.command} {' '.join(self.args)}` in {self.cwd or '.'}): {exc}"
            ) from exc

    async def list_tools(self) -> list[str]:
        async with self._session() as session:
            result = await session.list_tools()
            return [t.name for t in result.tools]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a tool and return its decoded payload."""
        async with self._session() as session:
            result = await session.call_tool(tool, arguments or {})
        return _decode(result)

    async def ping(self) -> tuple[bool, str]:
        try:
            tools = await self.list_tools()
        except McpUnavailableError as exc:
            return False, str(exc)
        return True, f"{len(tools)} tool(s): {', '.join(sorted(tools)[:6])}"


def _decode(result: Any) -> Any:
    """Unwrap an MCP CallToolResult into plain Python.

    Servers commonly return a JSON string inside a text content block; decode it
    when possible so callers do not each re-parse.
    """
    if getattr(result, "isError", False):
        raise McpUnavailableError(f"tool call failed: {_text_of(result)[:300]}")

    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    text = _text_of(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _text_of(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)
