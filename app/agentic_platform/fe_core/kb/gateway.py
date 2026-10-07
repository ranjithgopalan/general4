"""KB access gateway — MCP and non-MCP, chosen by configuration.

The two architecture diagrams differ in exactly one band. In
`uw-cr-e2e-architecture-V1.drawio` the integration layer is a "KB MCP Server";
in `uw-cr-e2e-architecture-fastapi-V1.drawio` it is a "KB FastAPI Service /
REST over HTTPS against an OpenAPI contract / Any client can call it. No MCP
involved". Every other element on both pages is identical, so the transport is a
configuration choice, not an architecture rewrite.

PRD 1.1 resolves the apparent competition: "Use a FastAPI platform API for
browser clients and MCP for agent-to-KB tools. These are complementary
boundaries, not competing choices." Accordingly there are two independent
switches:

    FE_KB_TRANSPORT      how THIS SERVICE reads the KB   (rest | mcp | both)
    FE_KB_AGENT_TOOLS    whether the AGENT gets MCP tools (mcp | none)

`FE_KB_AGENT_TOOLS=none` is the non-MCP variant: the worker pre-assembles
approved context and injects it into the prompt, and the agent is granted no KB
tools at all. That is strictly more locked-down, and it is the only mode that
works when MCP is disallowed on the host.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from app.agentic_platform.fe_core.kb.models import ContextPackage, KbAnswer, KbApplication

logger = logging.getLogger(__name__)


class KbTransport(str, Enum):
    REST = "rest"
    MCP = "mcp"
    BOTH = "both"


class AgentKbTools(str, Enum):
    MCP = "mcp"    # grant the agent the KB MCP server
    NONE = "none"  # non-MCP: context is injected into the prompt instead


class KbGatewayError(RuntimeError):
    pass


@runtime_checkable
class KbGateway(Protocol):
    """What the pipeline needs from the KB, independent of transport."""

    transport: str

    async def resolve_application(self, name_or_id: str) -> KbApplication: ...
    async def assemble_context(
        self, question: str, *, app_id: str, policy: str = "modernization"
    ) -> ContextPackage: ...
    async def query(self, question: str, *, app_id: str | None = None) -> KbAnswer: ...
    async def ping(self) -> tuple[bool, str]: ...


class RestKbGateway:
    """Non-MCP path: the knowledge base's `/fabric/*` REST surface."""

    transport = "rest"

    def __init__(self, client: Any):
        self._client = client

    async def resolve_application(self, name_or_id: str) -> KbApplication:
        return await self._client.resolve_application(name_or_id)

    async def assemble_context(
        self, question: str, *, app_id: str, policy: str = "modernization"
    ) -> ContextPackage:
        return await self._client.assemble_context(question, app_id=app_id, policy=policy)

    async def query(self, question: str, *, app_id: str | None = None) -> KbAnswer:
        return await self._client.query(question, app_id=app_id)

    async def ping(self) -> tuple[bool, str]:
        ok, detail = await self._client.ping()
        return ok, f"rest: {detail}"


class McpKbGateway:
    """MCP path: the knowledge base's stdio KB server.

    Only read tools exist on that server today, so writes are not offered here;
    artefact persistence goes through the artefact store and the REST sync
    endpoint. Adding `kb_upsert_artifact` / `kb_approve` to the knowledge base's server is
    tracked as phase-1 the knowledge base work in the PRD.
    """

    transport = "mcp"

    def __init__(self, client: Any):
        self._client = client

    async def resolve_application(self, name_or_id: str) -> KbApplication:
        payload = await self._client.call("list_projects", {})
        rows = payload if isinstance(payload, list) else (payload or {}).get("items", [])
        lowered = name_or_id.strip().lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("id")) == name_or_id or \
                    str(row.get("name", "")).strip().lower() == lowered:
                return KbApplication.model_validate(row)
        raise KbGatewayError(
            f"no KB application named or id'd '{name_or_id}' over MCP "
            f"({len(rows)} returned)"
        )

    async def assemble_context(
        self, question: str, *, app_id: str, policy: str = "modernization"
    ) -> ContextPackage:
        payload = await self._client.call(
            "get_context_package",
            {"question": question, "kb_application_id": app_id, "policy": policy},
        )
        if isinstance(payload, dict):
            return ContextPackage.model_validate(payload)
        return ContextPackage(context_objects=[], policy=policy)

    async def query(self, question: str, *, app_id: str | None = None) -> KbAnswer:
        args: dict[str, Any] = {"question": question}
        if app_id:
            args["kb_application_id"] = app_id
        payload = await self._client.call("search_kb", args)
        if isinstance(payload, dict):
            return KbAnswer.model_validate(payload)
        return KbAnswer(answer=payload if isinstance(payload, str) else None)

    async def ping(self) -> tuple[bool, str]:
        ok, detail = await self._client.ping()
        return ok, f"mcp: {detail}"


class CompositeKbGateway:
    """`both`: prefer one transport, fall back to the other.

    Useful during migration and on hosts where MCP is intermittently blocked.
    A fallback is logged at WARNING so a silently-degraded deployment is visible.
    """

    transport = "both"

    def __init__(self, primary: KbGateway, secondary: KbGateway):
        self.primary = primary
        self.secondary = secondary

    async def _try(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return await getattr(self.primary, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "KB %s failed on %s (%s); falling back to %s",
                method, self.primary.transport, exc, self.secondary.transport,
            )
            return await getattr(self.secondary, method)(*args, **kwargs)

    async def resolve_application(self, name_or_id: str) -> KbApplication:
        return await self._try("resolve_application", name_or_id)

    async def assemble_context(
        self, question: str, *, app_id: str, policy: str = "modernization"
    ) -> ContextPackage:
        return await self._try("assemble_context", question, app_id=app_id, policy=policy)

    async def query(self, question: str, *, app_id: str | None = None) -> KbAnswer:
        return await self._try("query", question, app_id=app_id)

    async def ping(self) -> tuple[bool, str]:
        p_ok, p_detail = await self.primary.ping()
        s_ok, s_detail = await self.secondary.ping()
        return (p_ok or s_ok), f"{p_detail}; {s_detail}"


def build_gateway(settings: Any = None) -> KbGateway:
    """Construct the gateway named by `FE_KB_TRANSPORT`.

    REST transport has been removed (the KB REST write-back is no longer used).
    All transports now resolve to MCP; FE_KB_TRANSPORT is kept for compatibility
    but REST/BOTH behave identically to MCP.
    """
    from app.agentic_platform.fe_core.config import get_settings
    from app.agentic_platform.fe_core.kb.mcp_client import KbMcpClient

    settings = settings or get_settings()
    return McpKbGateway(KbMcpClient(**settings.kb_mcp_launch()))
