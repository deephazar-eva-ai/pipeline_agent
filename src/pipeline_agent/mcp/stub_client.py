"""An in-memory fixture, not a mock of AgentSwitch's real behaviour.

Purpose: let the harness run end-to-end - model loop, tool-call trace, run
artifact - without live credentials, so Phase 0's exit condition ("a harmless
MCP read succeeds through the adapted harness") is checkable today instead of
blocked on Gate G1 or a credentialed live smoke test.

It reproduces exactly three things confirmed against the live platform earlier
in this project, and nothing else is asserted as realistic:
  1. CRMPreferences.deal_rot_days = 30, crm domain, readable.
  2. Deal/Activity are in the excluded `sales` domain -> PermissionDeniedError,
     matching the real, already-verified refusal this seat's policy produces.
  3. The tool catalogue is the 13 generic tools, verbatim from the captured
     EAG_V3_capstone/document/agent_tools.json ("total": 13) - a real capture
     of this seat's surface, not an invented catalogue.

Anything not listed here is out of scope for the stub; it exists to exercise
plumbing, not to stand in for the live database in a scored verifier. Human-
authored verifiers (Phase 5) must run against the real platform.
"""
from __future__ import annotations

import uuid
from typing import Any

from pipeline_agent.mcp.client import (
    ENTITY_DOMAIN, EXCLUDED_DOMAINS, KNOWN_TOOLS, MCPClient, MCPToolError, PermissionDeniedError,
)


class StubMCPClient(MCPClient):
    def __init__(self):
        self._crm_preferences = [{
            "id": "stub-crmprefs-0001",
            "deal_rot_days": 30,
            "lead_scoring_enabled": False,
            "lead_score_threshold": None,
        }]
        self._created: list[dict] = []

    async def list_tools(self) -> list[dict] | None:
        return [{"name": name} for name in sorted(KNOWN_TOOLS)]

    async def call_tool(self, name: str, arguments: dict) -> Any:
        entity = arguments.get("entity")
        domain = ENTITY_DOMAIN.get(entity)
        if domain in EXCLUDED_DOMAINS:
            raise PermissionDeniedError(tool=name, entity=entity, domain=domain,
                                         raw={"arguments": arguments})

        if name == "get_schema" and entity == "CRMPreferences":
            return {"entity": "CRMPreferences",
                    "fields": {"deal_rot_days": {"type": "integer", "default": 30}}}

        if name == "list" and entity == "CRMPreferences":
            return {"records": self._crm_preferences, "total": len(self._crm_preferences)}

        if name == "get" and entity == "CRMPreferences":
            rid = arguments.get("id")
            match = next((r for r in self._crm_preferences if r["id"] == rid), None)
            if match is None:
                raise MCPToolError("get", f"no CRMPreferences record {rid!r}", entity=entity)
            return match

        if name == "create":
            record = {"id": f"stub-{entity.lower() if entity else 'record'}-{uuid.uuid4().hex[:8]}",
                      **(arguments.get("data") or {})}
            self._created.append({"entity": entity, "record": record})
            return record

        raise NotImplementedError(
            f"StubMCPClient has no fixture for tool={name!r} entity={entity!r}. "
            "This stub only covers the CRMPreferences read and the sales-domain "
            "refusal used by the Phase 0/Phase 3 smoke test - extend it deliberately "
            "if a new dry-run scenario needs more fixture data."
        )

    @property
    def created_records(self) -> list[dict]:
        """What this stub session has created, for a dry-run to inspect/assert on."""
        return list(self._created)
