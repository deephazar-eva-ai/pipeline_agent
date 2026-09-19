"""The real transport to AgentSwitch's MCP surface.

Deliberately unfinished. AgentSwitch's own release note ("the moment your
harness is ready we'll integrate it") implies there's a specific integration
contract - auth method, endpoint, MCP transport (stdio vs SSE vs streamable
HTTP) - that hasn't been confirmed against this repo yet. Shipping a guess
here risks exactly the "retrofit later" cost that note was warning about.

`call_tool` raises NotImplementedError with a pointer to what's missing rather
than silently doing nothing or faking a response. Once the transport contract
is known, only this one method needs a body - every workflow function in
agent/workflow.py already calls through MCPClient's typed helpers and doesn't
know or care which subclass is underneath.
"""
from __future__ import annotations

from typing import Any

from pipeline_agent.config import Settings
from pipeline_agent.mcp.client import MCPClient


class RealMCPClient(MCPClient):
    def __init__(self, settings: Settings):
        settings.require_mcp_credentials()
        self._settings = settings

    async def call_tool(self, name: str, arguments: dict) -> Any:
        raise NotImplementedError(
            "RealMCPClient.call_tool is unimplemented: AgentSwitch's MCP transport/auth "
            "contract is not yet confirmed for this repo (docs/open_items.md, item 4). "
            f"Would have called tool={name!r} args={arguments!r} against "
            f"{self._settings.mcp_url!r}. Use --mode dry-run (StubMCPClient) until this "
            "is filled in."
        )
