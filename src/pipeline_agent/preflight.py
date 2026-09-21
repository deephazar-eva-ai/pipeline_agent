"""Preflight: what can this credential actually see, and what can it reach?

`docs/open_items.md` items 1, 2 and 4 all reduce to three questions that
nothing in this repo could answer from code:

  1. Which tool catalogue does this seat's credential expose - the 13 generic
     tools, or the entity-scoped `Deal.list` catalogue? Until now that was a
     configured guess (`MCP_TOOL_SURFACE`), never a measurement.
  2. Is the aggregate `report` tool in it? The canonical task's
     "who has not been contacted" step cannot be expressed without it.
  3. Is the `sales` domain genuinely blocked for this seat? That claim (Gate
     G1) rests on one observation in a chat transcript dated 2026-09-18. An
     agent should not keep refusing on the strength of a remembered result.

This module answers all three and the answer lands in a run artifact, so G1
becomes a dated, re-runnable check rather than something someone remembers.

Read-only by construction: every probe is `list(entity, limit=1)`. No probe
creates, updates, or transitions anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline_agent.mcp.client import (
    ENTITY_DOMAIN, KNOWN_TOOLS, MCPClient, MCPToolError, PermissionDeniedError,
)
from pipeline_agent.mcp.tool_names import ENTITY_SCOPED, GENERIC

UNKNOWN = "unknown"

# Tools the canonical task cannot be completed without. `report` is the one
# that matters: the entity-scoped catalogue has no counterpart for it, so a
# credential on that surface cannot answer "who has not been contacted"
# at all - see tool_names._NO_ENTITY_SCOPED_COUNTERPART.
CANONICAL_TASK_TOOLS = ("list", "report", "create")

# (entity, what Gate G1 as recorded on 2026-09-18 says to expect). The
# expectation is recorded so the report can flag a CHANGE in the seat's policy
# rather than quietly re-confirming a stale belief.
PROBES: tuple[tuple[str, str], ...] = (
    ("CRMPreferences", "ok"),
    ("Deal", "refused"),
    ("Activity", "refused"),
)

SALES_ENTITIES = ("Deal", "Activity")


@dataclass
class PreflightReport:
    configured_surface: str
    detected_surface: str = UNKNOWN
    tool_count: int | None = None
    missing_canonical_tools: list[str] = field(default_factory=list)
    unexpected_generic_tools: list[str] = field(default_factory=list)
    probes: list[dict] = field(default_factory=list)
    gate_g1: str = "inconclusive"
    canonical_task_ready: bool = False
    changes_from_recorded_state: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        surface = (f"surface={self.detected_surface}"
                   if self.detected_surface == self.configured_surface
                   else f"surface={self.detected_surface} (configured: {self.configured_surface})")
        tools = "tools=?" if self.tool_count is None else f"tools={self.tool_count}"
        ready = "canonical task READY" if self.canonical_task_ready else "canonical task BLOCKED"
        return f"{surface}, {tools}, G1={self.gate_g1}, {ready}"


def detect_surface(tool_names: list[str]) -> str:
    """Which catalogue is this, judged by the names themselves.

    Generic tools are bare verbs (`list`, `report`); the entity-scoped
    catalogue is entirely `Entity.action`. A credential exposing neither is
    reported as unknown rather than forced into one of the two.
    """
    if any(name in KNOWN_TOOLS for name in tool_names):
        return GENERIC
    if tool_names and all("." in name for name in tool_names):
        return ENTITY_SCOPED
    return UNKNOWN


async def probe_entity(client: MCPClient, entity: str) -> dict:
    """One harmless read. Classifies the outcome without interpreting it."""
    probe: dict[str, Any] = {"entity": entity, "domain": ENTITY_DOMAIN.get(entity, UNKNOWN)}
    try:
        await client.list_(entity, limit=1)
    except PermissionDeniedError as exc:
        probe.update(outcome="refused", detail=exc.message)
    except MCPToolError as exc:
        probe.update(outcome="error", detail=exc.message)
    except Exception as exc:  # a transport fault is not a policy answer
        probe.update(outcome="error", detail=f"{type(exc).__name__}: {exc}")
    else:
        probe.update(outcome="ok", detail="readable")
    return probe


def _gate_g1_verdict(probes: list[dict]) -> str:
    outcomes = {p["entity"]: p["outcome"] for p in probes if p["entity"] in SALES_ENTITIES}
    if not outcomes:
        return "inconclusive"
    if any(outcome == "ok" for outcome in outcomes.values()):
        return "sales_reachable"
    if all(outcome == "refused" for outcome in outcomes.values()):
        return "sales_blocked"
    return "inconclusive"


async def run_preflight(client: MCPClient, *, configured_surface: str) -> PreflightReport:
    report = PreflightReport(configured_surface=configured_surface)

    try:
        tools = await client.list_tools()
    except MCPToolError as exc:
        tools = None
        report.notes.append(f"tools/list failed: {exc.message}")

    if tools is None:
        report.notes.append(
            "this client cannot enumerate a catalogue; surface left unverified")
    else:
        names = [t.get("name", "") for t in tools if isinstance(t, dict)]
        report.tool_count = len(names)
        report.detected_surface = detect_surface(names)
        if report.detected_surface == GENERIC:
            present = set(names) & KNOWN_TOOLS
            report.missing_canonical_tools = [t for t in CANONICAL_TASK_TOOLS if t not in present]
            report.unexpected_generic_tools = sorted(set(names) - KNOWN_TOOLS)
        elif report.detected_surface == ENTITY_SCOPED:
            # The aggregate report has no entity-scoped counterpart at all, so
            # this surface cannot express the canonical task however many tools
            # it carries.
            report.missing_canonical_tools = ["report"]
            report.notes.append(
                "entity-scoped catalogue: the aggregate `report` tool required by "
                "'who has not been contacted' is not expressible on this surface")
        if report.detected_surface != configured_surface and report.detected_surface != UNKNOWN:
            report.notes.append(
                f"MCP_TOOL_SURFACE is set to {configured_surface!r} but the credential "
                f"exposes {report.detected_surface!r} - the setting is wrong, not the server")

    for entity, expected in PROBES:
        probe = await probe_entity(client, entity)
        probe["expected"] = expected
        report.probes.append(probe)
        if probe["outcome"] != expected:
            report.changes_from_recorded_state.append(
                f"{entity}: expected {expected}, got {probe['outcome']} ({probe['detail']})")

    report.gate_g1 = _gate_g1_verdict(report.probes)
    report.canonical_task_ready = (
        report.gate_g1 == "sales_reachable" and not report.missing_canonical_tools
    )

    if report.gate_g1 == "sales_blocked":
        report.notes.append(
            "Gate G1 still unresolved: Deal and Activity both refused, so the canonical "
            "task's first two parts remain unanswerable from this seat. Escalate the "
            "policy/charter mismatch; do not work around it.")
    elif report.gate_g1 == "sales_reachable":
        report.notes.append(
            "Gate G1 appears RESOLVED: a sales-domain read succeeded. Re-run the canonical "
            "task and re-check docs/open_items.md item 1 before trusting this.")

    return report
