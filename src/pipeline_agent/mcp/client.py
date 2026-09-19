"""The MCP client interface every workflow function is written against.

Only `call_tool` is abstract. Every named helper (`list_`, `get`, `report`, ...)
is a thin, typed wrapper over it, so a real transport only has to implement
one method and every existing workflow call works unchanged.

The transport itself is intentionally NOT implemented here. AgentSwitch's MCP
auth/transport contract hasn't been confirmed against this codebase yet (see
docs/open_items.md, item 4) - guessing at a wire format and shipping it quietly
is exactly the kind of thing that gets retrofitted later at real cost. Two
implementations exist:

  RealMCPClient   raises clearly until the contract is filled in
  StubMCPClient   an in-memory fixture, used for the dry-run harness smoke test
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

# The 13 generic tools this seat's agent config exposes, per
# EAG_V3_capstone/document/agent_tools.json. Kept here as the single source of
# truth for "is this a real tool name" so a typo fails loudly, not silently.
KNOWN_TOOLS = frozenset({
    "get_schema", "list", "get", "create", "update", "delete", "transition",
    "search", "count", "make_from", "bulk_update", "report", "financial_report",
})

# Domains this seat's policy is known to exclude. `sales` is the one that
# actually blocks the canonical task (Deal/Activity live there) - see
# docs/open_items.md item 1 / capstone_scope.md Gate G1. Kept as data, not a
# hardcoded branch, so a policy change only requires updating this set.
EXCLUDED_DOMAINS = frozenset({"sales"})

# Which domain each entity this agent cares about lives in. Deliberately small
# and explicit rather than derived from schemas.json at runtime - this repo
# has no dependency on that document snapshot, and the set of entities this
# agent touches is short and known.
ENTITY_DOMAIN = {
    "Deal": "sales",
    "Activity": "sales",
    "CRMPreferences": "crm",
    "Pipeline": "crm",
    "Goal": "crm",
    "AccountPlan": "crm",
    "EmailMessage": "crm",
    "ChannelConversation": "crm",
}


class MCPToolError(Exception):
    """A tool call failed. Carries enough to log and to explain to a human."""

    def __init__(self, tool: str, message: str, *, entity: str | None = None, raw: Any = None):
        super().__init__(f"{tool}({entity or ''}): {message}")
        self.tool = tool
        self.entity = entity
        self.message = message
        self.raw = raw


class PermissionDeniedError(MCPToolError):
    """A clean policy refusal - the thing Phase 3's refusal task exists for.

    Never retry after catching this. The platform's own convention (seen
    across WebForm/Webhook, SalarySlip/Contract/EsignDocument, and now
    Deal/Activity) is: refuse once, say so, done - retrying is the thing that
    turns a correct refusal into a policy violation.
    """

    def __init__(self, tool: str, entity: str, domain: str, *, raw: Any = None):
        super().__init__(tool, f"entity '{entity}' is in domain '{domain}', "
                                f"outside this seat's policy scope", entity=entity, raw=raw)
        self.domain = domain


@dataclass
class ToolCall:
    """One call/response pair, exactly as it happened. This is what the run
    artifact's trace is built from - see harness/base.py Step."""

    tool: str
    arguments: dict
    ok: bool
    result: Any = None
    error: str | None = None


class MCPClient(abc.ABC):
    """Base class for anything that can execute AgentSwitch's generic tool set."""

    @abc.abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Execute one named tool call. Must raise PermissionDeniedError for a
        clean policy refusal, MCPToolError for anything else that isn't a
        normal result, so callers can tell the two apart."""
        raise NotImplementedError

    async def get_schema(self, entity: str) -> Any:
        return await self.call_tool("get_schema", {"entity": entity})

    async def list_(self, entity: str, *, filters: dict | None = None, limit: int = 20,
                     offset: int = 0, sort_by: str | None = None, sort_order: str = "desc",
                     search: str | None = None) -> Any:
        args: dict[str, Any] = {"entity": entity, "limit": limit, "offset": offset,
                                 "sort_order": sort_order}
        if filters:
            args["filters"] = filters
        if sort_by:
            args["sort_by"] = sort_by
        if search:
            args["search"] = search
        return await self.call_tool("list", args)

    async def get(self, entity: str, id: str) -> Any:
        return await self.call_tool("get", {"entity": entity, "id": id})

    async def create(self, entity: str, data: dict) -> Any:
        return await self.call_tool("create", {"entity": entity, "data": data})

    async def update(self, entity: str, id: str, data: dict) -> Any:
        return await self.call_tool("update", {"entity": entity, "id": id, "data": data})

    async def delete(self, entity: str, id: str) -> Any:
        return await self.call_tool("delete", {"entity": entity, "id": id})

    async def transition(self, entity: str, id: str, action: str, *,
                          target_state: str | None = None) -> Any:
        args = {"entity": entity, "id": id, "action": action}
        if target_state:
            args["target_state"] = target_state
        return await self.call_tool("transition", args)

    async def search(self, query: str, *, entity: str | None = None) -> Any:
        args: dict[str, Any] = {"search": query}
        if entity:
            args["entity"] = entity
        return await self.call_tool("search", args)

    async def count(self, entity: str, *, filters: dict | None = None) -> Any:
        args: dict[str, Any] = {"entity": entity}
        if filters:
            args["filters"] = filters
        return await self.call_tool("count", args)

    async def report(self, entity: str, *, aggregate: str, field: str | None = None,
                      group_by: str | None = None, filters: dict | None = None) -> Any:
        args: dict[str, Any] = {"entity": entity, "aggregate": aggregate}
        if field:
            args["field"] = field
        if group_by:
            args["group_by"] = group_by
        if filters:
            args["filters"] = filters
        return await self.call_tool("report", args)
