"""An in-memory fixture, not a mock of AgentSwitch's real behaviour.

Purpose: let the harness run end-to-end - model loop, tool-call trace, run
artifact - without live credentials, so the plumbing is checkable offline.

Everything here is shaped from what the live seat 07 credential actually
returned on 2026-09-21, not from the captured document snapshot, which turned
out to describe a different tool surface:

  1. `CRMPreferences.deal_rot_days = 30` (record id matches the live one).
  2. Deal and Activity ARE readable. The earlier fixture refused them on the
     belief that the `sales` domain was excluded; the live credential reads
     both (Gate G1, resolved).
  3. Access is scoped by the seat's tool catalogue, not by domain, so an
     entity outside `SEAT_ENTITIES` refuses the way the platform does.
  4. `_rot_level` takes the values `fresh` / `attention` / `none`, `none`
     being closed deals - not the `aging` / `stale` this repo once assumed.
  5. `done` is 0/1, not a JSON boolean, and **no Activity carries a
     `deal_id`** - the live book links activities to parties only. The
     fixtures below keep that shape, because it is the thing most likely to
     break a change to the contact logic.

It exercises plumbing. A scored verifier must run against the real platform.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pipeline_agent.mcp.client import KNOWN_TOOLS, MCPClient, MCPToolError, PermissionDeniedError

# The entities this stub's "seat" can see. Anything else refuses, the way the
# live platform refuses Invoice / SalarySlip / Contract.
SEAT_ENTITIES = frozenset({"CRMPreferences", "Deal", "Activity"})

_PARTY_CONTACTED = "stub-party-contacted"
_PARTY_SILENT = "stub-party-silent"

# The agent recomputes rot from `updated_at`, so the fixtures have to carry one
# that agrees with the `_rot_days` beside it - otherwise the dry-run would
# exercise the new path against data the live platform could never produce.
# Generated relative to now rather than hard-coded, so the fixture does not
# silently rot into "everything is 400 days old" as wall-clock time passes.
_NOW = dt.datetime.utcnow()


def _days_ago(days: int) -> str:
    return (_NOW - dt.timedelta(days=days)).isoformat()


def _days_ahead(days: int) -> str:
    return (_NOW + dt.timedelta(days=days)).date().isoformat()


class StubMCPClient(MCPClient):
    def __init__(self):
        self._crm_preferences = [{
            "id": "10adde93-e1c7-47aa-9ffe-4bba0f9828b7",
            "deal_rot_days": 30,
            "lead_scoring_enabled": False,
            "lead_score_threshold": None,
        }]
        self._deals = [
            # Rotting; its party was contacted long ago and has an open action.
            {"id": "stub-deal-existing", "title": "Bench vice bulk order",
             "stage": "qualification", "party_id": _PARTY_CONTACTED,
             "_rot_level": "attention", "_rot_days": 8, "value": 264000.0,
             "updated_at": _days_ago(8)},
            # Rotting; nobody has ever contacted this party, nothing open.
            {"id": "stub-deal-needs-action", "title": "Lathe chuck enquiry",
             "stage": "new", "party_id": _PARTY_SILENT,
             "_rot_level": "attention", "_rot_days": 8, "value": 8000.0,
             "updated_at": _days_ago(8)},
            # The laundered case, and the reason this fixture exists at all.
            # The platform reads it as fresh/0 for exactly one reason: this
            # agent logged a task against it on an earlier run. Its customer
            # has never been contacted and its `updated_at` is 9 days old, so
            # a rot signal that excludes the agent's own rows must still flag
            # it. Mirrors live deal 7de1dd77 as of 2026-09-22.
            {"id": "stub-deal-laundered", "title": "Surface grinder quote",
             "stage": "new", "party_id": _PARTY_SILENT,
             "_rot_level": "fresh", "_rot_days": 0, "value": 47000.0,
             "updated_at": _days_ago(9)},
            # Rotting, but the stage is not in the validated action map.
            {"id": "stub-deal-odd-stage", "title": "Despatch week 42",
             "stage": "Bench Vice 7398", "party_id": _PARTY_SILENT,
             "_rot_level": "attention", "_rot_days": 8, "value": 1000.0,
             "updated_at": _days_ago(8)},
            # Fresh - must not be reported as rotting.
            {"id": "stub-deal-fresh", "title": "New web enquiry",
             "stage": "new", "party_id": _PARTY_SILENT,
             "_rot_level": "fresh", "_rot_days": 2, "value": 500.0,
             "updated_at": _days_ago(2)},
            # Closed - out of scope regardless of age.
            # Closed deals read as none/0 regardless of true age on the live
            # platform, so this one is deliberately old underneath.
            {"id": "stub-deal-closed", "title": "Won last quarter",
             "stage": "closed_won", "party_id": _PARTY_CONTACTED,
             "_rot_level": "none", "_rot_days": 0, "value": 99000.0,
             "updated_at": _days_ago(40)},
        ]
        self._activities = [
            {"id": "stub-act-done", "subject": "Trade call", "type": "call",
             "due_date": "2025-11-15", "done": 1, "created_at": _days_ago(320),
             "deal_id": None, "party_id": _PARTY_CONTACTED},
            {"id": "stub-act-open", "subject": "Site visit", "type": "meeting",
             "due_date": _days_ahead(1), "done": 0, "created_at": _days_ago(6),
             "deal_id": None, "party_id": _PARTY_CONTACTED},
            # Written by an earlier run of this agent: deal-linked, no
            # party_id, open, and carrying the provenance marker - the exact
            # shape of the one row the live book has.
            {"id": "stub-act-agent", "subject": "Follow up: Surface grinder quote",
             "type": "task", "due_date": _days_ahead(3), "done": 0,
             "created_at": _days_ago(0), "priority": "medium",
             "deal_id": "stub-deal-laundered", "party_id": None,
             "description": "Qualify: confirm the lead's need and budget before moving "
                            "to the next stage.\n\n(created_by=pipeline_agent "
                            "run=stub-earlier-run; stage at time of writing: new)"},
        ]
        self._created: list[dict] = []

    async def list_tools(self) -> list[dict] | None:
        return [{"name": name} for name in sorted(KNOWN_TOOLS)]

    def _page(self, records: list[dict], arguments: dict) -> dict:
        offset = int(arguments.get("offset") or 0)
        limit = int(arguments.get("limit") or 20)
        return {"records": records[offset:offset + limit], "total": len(records)}

    async def call_tool(self, name: str, arguments: dict) -> Any:
        entity = arguments.get("entity")
        if entity and entity not in SEAT_ENTITIES:
            raise PermissionDeniedError(tool=name, entity=entity, domain="unknown",
                                         raw={"arguments": arguments})

        if name == "get_schema" and entity == "CRMPreferences":
            return {"entity": "CRMPreferences",
                    "fields": {"deal_rot_days": {"type": "integer", "default": 30}}}

        if name == "list":
            if entity == "CRMPreferences":
                return self._page(self._crm_preferences, arguments)
            if entity == "Deal":
                return self._page(self._deals, arguments)
            if entity == "Activity":
                records = self._activities + [c["record"] for c in self._created
                                               if c["entity"] == "Activity"]
                for key, want in (arguments.get("filters") or {}).items():
                    if isinstance(want, bool):
                        # `done` is 0/1 on the live platform, so compare truthiness.
                        records = [r for r in records if bool(r.get(key)) is want]
                    else:
                        records = [r for r in records if r.get(key) == want]
                return self._page(records, arguments)

        if name == "get":
            pool = {"CRMPreferences": self._crm_preferences, "Deal": self._deals,
                    "Activity": self._activities}.get(entity or "", [])
            match = next((r for r in pool if r["id"] == arguments.get("id")), None)
            if match is None:
                raise MCPToolError("get", f"no {entity} record {arguments.get('id')!r}",
                                    entity=entity)
            return match

        if name == "create":
            record = {"id": f"stub-{(entity or 'record').lower()}-{uuid.uuid4().hex[:8]}",
                      **(arguments.get("data") or {})}
            self._created.append({"entity": entity, "record": record})
            return record

        raise NotImplementedError(
            f"StubMCPClient has no fixture for tool={name!r} entity={entity!r}. "
            "Extend it deliberately if a new dry-run scenario needs more fixture data.")

    @property
    def created_records(self) -> list[dict]:
        """What this stub session has created, for a dry-run to inspect/assert on."""
        return list(self._created)
