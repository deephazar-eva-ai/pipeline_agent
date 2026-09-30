"""The live AgentSwitch MCP client.

The captured OpenAPI contract specifies JSON-RPC 2.0 over POST ``/api/mcp``
and bearer authentication.  This class keeps the agent on its generic tool
interface while translating at the boundary when a credential exposes the
ordinary entity-scoped MCP catalogue instead.
"""
from __future__ import annotations

from typing import Any

from pipeline_agent.config import Settings
from pipeline_agent.mcp.client import ENTITY_DOMAIN, KNOWN_TOOLS, MCPClient, MCPToolError, PermissionDeniedError
from pipeline_agent.mcp.tool_names import SurfaceError, normalise_surface, to_wire
from pipeline_agent.mcp.transport import JsonRpcError, JsonRpcTransport, ToolResultError, TransportError


def _permission_message(exc: Exception) -> str:
    """Collect structured and textual server detail for permission detection."""
    parts = [str(exc)]
    for attr in ("data", "body"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def _is_permission_denied(exc: Exception) -> bool:
    message = _permission_message(exc)
    markers = (
        "permission denied", "access denied", "forbidden", "not authorized", "unauthorized",
        "outside this seat's policy", "outside policy scope",
        # What AgentSwitch actually says when a tool is outside the seat's
        # catalogue, measured 2026-09-21 against Invoice/SalarySlip/Contract:
        #   -32602 "This tool is not available to your seat; it is not in your
        #    tools/list. Re-fetch tools/list for the tools you may call."
        # Without this the platform's own refusal arrives as a generic
        # MCPToolError, the workflow never builds a RefusalResult, and the
        # loop's no-retry guard never engages - it keys on PermissionDeniedError.
        "not available to your seat",
    )
    # A 401 means a bad or expired credential, not a policy refusal. Treating
    # it as the latter would say Deal data is unavailable when the agent was
    # never authenticated at all.
    return any(marker in message for marker in markers) or (
        isinstance(exc, TransportError) and exc.status == 403
    )


def _normalise_result(tool: str, result: Any) -> Any:
    """Present both captured MCP catalogue shapes to the workflow uniformly."""
    if not isinstance(result, dict):
        return result
    if tool == "list" and "records" not in result and isinstance(result.get("data"), list):
        normalised = dict(result)
        normalised["records"] = result["data"]
        return normalised
    return result


# The only REST paths this agent reads, and the only endpoint tool it may
# write through. Allow-lists, not patterns: widening either is a code change
# someone has to review.
REST_READ_PATHS = frozenset({"/api/forecast", "/api/deal-rot-config"})
WRITE_ENDPOINTS = frozenset({"endpoint.crm.call_notes.draft",
                             "endpoint.agent_governance.escalations.raise"})


class RealMCPClient(MCPClient):
    def __init__(self, settings: Settings):
        settings.require_mcp_credentials()
        self._settings = settings
        self._surface = normalise_surface(settings.mcp_tool_surface)
        self._transport = JsonRpcTransport(settings.mcp_url, settings.mcp_token)

    async def list_tools(self) -> list[dict] | None:
        try:
            return await self._transport.list_tools()
        except (JsonRpcError, ToolResultError, TransportError) as exc:
            raise MCPToolError("tools/list", str(exc),
                               raw=getattr(exc, "data", None) or getattr(exc, "body", None)) from exc

    async def call_endpoint(self, name: str, arguments: dict | None = None) -> Any:
        """Read-only `endpoint.*` tools go on the wire under their own name.
        Anything else is refused here, so this can never become a side door
        around the generic surface's write tools."""
        if not name.startswith("endpoint."):
            raise MCPToolError(name, "call_endpoint only accepts endpoint.* tools")
        try:
            result = await self._transport.call_tool(name, dict(arguments or {}))
        except (JsonRpcError, ToolResultError, TransportError) as exc:
            if _is_permission_denied(exc):
                raise PermissionDeniedError(name, name, "unknown",
                                            raw=getattr(exc, "data", None)
                                            or getattr(exc, "body", None)) from exc
            raise MCPToolError(name, str(exc),
                               raw=getattr(exc, "data", None) or getattr(exc, "body", None)) from exc
        # Endpoint tools wrap their payload as {"status": "ok", "result": ...}.
        if isinstance(result, dict) and "result" in result and "status" in result:
            return result["result"]
        return result

    async def get_rest(self, path: str, params: dict | None = None) -> Any:
        if path not in REST_READ_PATHS:
            raise MCPToolError(f"GET {path}", "not an allow-listed REST read for this agent")
        try:
            return await self._transport.rest_get(path, params)
        except TransportError as exc:
            if _is_permission_denied(exc):
                raise PermissionDeniedError(f"GET {path}", path, "unknown",
                                            raw=getattr(exc, "body", None)) from exc
            raise MCPToolError(f"GET {path}", str(exc), raw=getattr(exc, "body", None)) from exc

    async def call_write_endpoint(self, name: str, arguments: dict) -> Any:
        if name not in WRITE_ENDPOINTS:
            raise MCPToolError(name, "not an allow-listed write endpoint for this agent")
        try:
            result = await self._transport.call_tool(name, dict(arguments))
        except (JsonRpcError, ToolResultError, TransportError) as exc:
            raw = getattr(exc, "data", None) or getattr(exc, "body", None)
            if _is_permission_denied(exc):
                raise PermissionDeniedError(name, name, "unknown", raw=raw) from exc
            raise MCPToolError(name, str(exc), raw=raw) from exc
        if isinstance(result, dict) and "result" in result and "status" in result:
            return result["result"]
        return result

    async def call_tool(self, name: str, arguments: dict) -> Any:
        if name not in KNOWN_TOOLS:
            raise MCPToolError(name, "not one of this agent's configured tools",
                               entity=arguments.get("entity"))
        entity = arguments.get("entity")
        try:
            wire_name, wire_arguments = to_wire(self._surface, name, arguments)
        except SurfaceError as exc:
            raise MCPToolError(name, str(exc), entity=entity) from exc
        try:
            result = await self._transport.call_tool(wire_name, wire_arguments)
        except (JsonRpcError, ToolResultError, TransportError) as exc:
            if entity and _is_permission_denied(exc):
                raise PermissionDeniedError(name, entity, ENTITY_DOMAIN.get(entity, "unknown"),
                                            raw=getattr(exc, "data", None) or getattr(exc, "body", None)) from exc
            raise MCPToolError(name, str(exc), entity=entity,
                               raw=getattr(exc, "data", None) or getattr(exc, "body", None)) from exc
        return _normalise_result(name, result)
