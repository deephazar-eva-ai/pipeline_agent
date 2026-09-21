"""Bridges the deterministic workflow (agent/workflow.py) to the harness's
run artifact.

workflow.py calls MCPClient directly and doesn't know a TaskRun exists - it
shouldn't have to, since it must work identically against StubMCPClient,
RealMCPClient, or any future test double. This wrapper is how its tool
activity still ends up in the persisted trace: every call, success or
refusal or error, is appended to the same `steps` list the run artifact
writes to disk. Without this, "tool activity ... written to disk before
scoring" would be true for the free-form loop.py path and silently false for
the canonical task's deterministic path - the one that matters most.
"""
from __future__ import annotations

from typing import Any

from pipeline_agent.harness.base import Step
from pipeline_agent.mcp.client import MCPClient, MCPToolError, PermissionDeniedError


class RecordingMCPClient(MCPClient):
    def __init__(self, inner: MCPClient, steps: list[Step]):
        self._inner = inner
        self._steps = steps

    async def list_tools(self) -> list[dict] | None:
        try:
            tools = await self._inner.list_tools()
        except MCPToolError as e:
            self._steps.append(Step("error", "tools/list", "", False, str(e)))
            raise
        detail = "not enumerable by this client" if tools is None else f"{len(tools)} tool(s)"
        self._steps.append(Step("tool_call", "tools/list", "", True, detail))
        return tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        entity = arguments.get("entity", "")
        try:
            result = await self._inner.call_tool(name, arguments)
        except PermissionDeniedError as e:
            self._steps.append(Step("refused", name, entity, False, str(e), arguments=arguments))
            raise
        except MCPToolError as e:
            self._steps.append(Step("error", name, entity, False, str(e), arguments=arguments))
            raise
        self._steps.append(Step("tool_call", name, entity, True, arguments=arguments))
        return result
