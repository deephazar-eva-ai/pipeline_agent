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

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline_agent.mcp.client import (
    ENTITY_DOMAIN, KNOWN_TOOLS, MCPClient, MCPToolError, PermissionDeniedError,
)
from pipeline_agent.mcp.tool_names import ENTITY_SCOPED, GENERIC

UNKNOWN = "unknown"

# Tools the canonical task cannot be completed without, per surface. The
# entity-scoped catalogue has no aggregate tool at all, so "who has not been
# contacted" is aggregated client-side from Activity.list instead of asking
# the server for a `report` - which is why `report` is not required here.
CANONICAL_TASK_TOOLS = {
    GENERIC: ("list", "create"),
    ENTITY_SCOPED: ("Deal.list", "Activity.list", "Activity.create"),
}

# (entity, expected outcome). Measured against the live seat 07 credential on
# 2026-09-21: all three are readable. This replaces the earlier expectation
# that Deal/Activity would be refused - that came from a 2026-09-18 observation
# through a different door and was wrong for this credential. The expectation
# is recorded so a future policy change shows up as a CHANGED line rather than
# quietly re-confirming a stale belief.
PROBES: tuple[tuple[str, str], ...] = (
    ("CRMPreferences", "ok"),
    ("Deal", "ok"),
    ("Activity", "ok"),
)

# Read by the canonical task but not required by it: without Party the
# supplier exclusion is skipped and the answer says so. Probed so a READY
# preflight covers every input the task reads (2026-09-28: Party was added to
# the task and not to preflight), but it never blocks readiness and never
# counts as drift.
OPTIONAL_PROBES: tuple[tuple[str, str], ...] = (
    ("Party", "ok"),
)

SALES_ENTITIES = ("Deal", "Activity")

# Size of the catalogue this credential last served. Recorded so the catalogue
# is *measured against a baseline* rather than merely printed - the number was
# 237 on 2026-09-21 and 238 on 2026-09-22, and nothing said a word, because
# nothing was comparing. A seat whose tool catalogue is its access-control
# boundary cannot have that boundary move unobserved; that is the same class of
# mistake as trusting the remembered Gate G1 result.
#
# Update this deliberately, with the date, when a change is understood.
#
# 2026-09-24: 238 -> 242. The four added tools are all `endpoint.*` tools for
# apps this seat is NOT entitled to (`allowed_apps: ["crm","agent"]`):
#   endpoint.accounting.supplier_scorecard
#   endpoint.inventory.shipping_board
#   endpoint.manufacturing.demand_forecast
#   endpoint.manufacturing.label
# Attributable only because a run artifact had kept the sorted names. The
# expansion is filed as a platform bug; the count is updated here so the next
# move is measured against today rather than against a stale number.
RECORDED_TOOL_COUNT = 242

# The names behind that count, per tenant, recorded 2026-09-30 from tools/list
# on both instances (identical today). The count alone cannot say WHICH tool
# moved; the 237 -> 238 change was never attributed for that reason.
CATALOGUE_BASELINE = Path(__file__).with_name("catalogue_baseline.json")

# The rot threshold as each source reported it, measured 2026-09-30 on both
# instances. The three sources disagreed (14 / 30 / measured 5-9) until
# 2026-09-25; a silent move back is exactly what this records.
RECORDED_ROT_CONFIG = {
    "suryodaya": {"default_rot_days": 30, "deal_rot_days": 30},
    "keystone": {"default_rot_days": 30, "deal_rot_days": 30},
}


def load_catalogue_baseline(tenant: str) -> list[str] | None:
    try:
        doc = json.loads(CATALOGUE_BASELINE.read_text())
    except (OSError, ValueError):
        return None
    names = (doc.get("tenants") or {}).get(tenant)
    return sorted(names) if isinstance(names, list) else None


def diff_catalogue(names: list[str], baseline: list[str]) -> tuple[list[str], list[str]]:
    """(added, removed) against the recorded names."""
    now, then = set(names), set(baseline)
    return sorted(now - then), sorted(then - now)


async def read_rot_config(client: MCPClient, notes: list[str]) -> dict[str, Any]:
    """The threshold as each source states it today. Either may be missing."""
    out: dict[str, Any] = {}
    try:
        cfg = await client.get_rest("/api/deal-rot-config")
        if isinstance(cfg, dict):
            out["default_rot_days"] = cfg.get("default_rot_days")
    except MCPToolError as exc:
        notes.append(f"deal-rot-config not read ({exc.message}) - rot config drift unchecked")
    try:
        resp = await client.list_("CRMPreferences", limit=1)
        rows = resp.get("records") if isinstance(resp, dict) else None
        if rows:
            out["deal_rot_days"] = rows[0].get("deal_rot_days")
    except MCPToolError as exc:
        notes.append(f"CRMPreferences not read ({exc.message})")
    return out


def rot_config_changes(current: dict[str, Any], tenant: str | None) -> list[str]:
    """Compared per tenant: the recorded values were measured on each
    instance, and an unknown tenant has nothing to be compared against."""
    changes = []
    for key, recorded in RECORDED_ROT_CONFIG.get(tenant or "", {}).items():
        if key not in current:
            continue
        try:
            same = float(current[key]) == float(recorded)
        except (TypeError, ValueError):
            same = False
        if not same:
            changes.append(f"rot threshold {key} is {current[key]!r}, recorded {recorded} - "
                           f"the rot boundary moved; re-check calibration before trusting "
                           f"'rotting' counts")
    return changes


@dataclass
class PreflightReport:
    configured_surface: str
    detected_surface: str = UNKNOWN
    tool_count: int | None = None
    # The names themselves, persisted into the run artifact so the NEXT run can
    # say which tool appeared or vanished, not just that the count moved. The
    # 237 -> 238 change could not be attributed after the fact because no run
    # had kept the list.
    tool_names: list[str] = field(default_factory=list)
    missing_canonical_tools: list[str] = field(default_factory=list)
    unexpected_generic_tools: list[str] = field(default_factory=list)
    probes: list[dict] = field(default_factory=list)
    optional_probes: list[dict] = field(default_factory=list)
    gate_g1: str = "inconclusive"
    canonical_task_ready: bool = False
    changes_from_recorded_state: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Which tools appeared / vanished against catalogue_baseline.json.
    tools_added: list[str] = field(default_factory=list)
    tools_removed: list[str] = field(default_factory=list)
    rot_config: dict[str, Any] = field(default_factory=dict)

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


async def run_preflight(client: MCPClient, *, configured_surface: str,
                        tenant: str | None = None) -> PreflightReport:
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
        report.tool_names = sorted(names)
        # Detect the surface BEFORE the drift check reads it. Until 2026-09-24
        # these two statements were the other way round, so the check ran
        # against the dataclass default (`UNKNOWN`), never matched
        # ENTITY_SCOPED, and was unreachable code. The catalogue moved
        # 238 -> 242 under it without a word - exactly the silent boundary
        # change the check exists to make impossible.
        report.detected_surface = detect_surface(names)
        # Scoped to the surface the baseline was taken on. The stub serves 13
        # generic tools by design, and reporting that as "the access boundary
        # moved" would train a reader to ignore the one line that matters.
        if report.detected_surface == ENTITY_SCOPED and report.tool_count != RECORDED_TOOL_COUNT:
            report.changes_from_recorded_state.append(
                f"tool catalogue size {report.tool_count}, recorded {RECORDED_TOOL_COUNT} - "
                f"this seat's access boundary moved; diff tool_names against an earlier "
                f"run artifact and update RECORDED_TOOL_COUNT once the change is understood")
        baseline = (load_catalogue_baseline(tenant)
                    if tenant and report.detected_surface == ENTITY_SCOPED else None)
        if baseline is not None:
            report.tools_added, report.tools_removed = diff_catalogue(names, baseline)
            for name in report.tools_added:
                report.changes_from_recorded_state.append(f"tool added since baseline: {name}")
            for name in report.tools_removed:
                report.changes_from_recorded_state.append(f"tool removed since baseline: {name}")
        present = set(names)
        required = CANONICAL_TASK_TOOLS.get(report.detected_surface, ())
        report.missing_canonical_tools = [t for t in required if t not in present]
        if report.detected_surface == GENERIC:
            report.unexpected_generic_tools = sorted(present - KNOWN_TOOLS)
        elif report.detected_surface == ENTITY_SCOPED:
            report.notes.append(
                "entity-scoped catalogue: no aggregate tool exists on this surface, so "
                "'who has not been contacted' is aggregated client-side from Activity.list")
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

    for entity, expected in OPTIONAL_PROBES:
        probe = await probe_entity(client, entity)
        probe["expected"] = expected
        probe["optional"] = True
        report.optional_probes.append(probe)
        if probe["outcome"] != expected:
            report.notes.append(
                f"optional {entity}: {probe['outcome']} ({probe['detail']}) - the canonical "
                f"task will run without it (supplier-side deals will NOT be excluded)")

    if report.detected_surface == ENTITY_SCOPED:
        report.rot_config = await read_rot_config(client, report.notes)
        report.changes_from_recorded_state.extend(rot_config_changes(report.rot_config, tenant))

    report.gate_g1 = _gate_g1_verdict(report.probes)
    # Readiness needs EVERY probe green, not just the Gate G1 verdict.
    # `_gate_g1_verdict` answers "is the sales domain reachable at all", and
    # returns `sales_reachable` on any single `ok` - correct for that question,
    # and wrong as a readiness test: with Deal readable and Activity refused it
    # reported `canonical task READY` and `claimed_success: true` for a seat that
    # cannot run the task. architecture.md is explicit that a blocked seat must
    # not look like a green run, so the probes decide readiness.
    blocked = [p["entity"] for p in report.probes if p["outcome"] != "ok"]
    report.canonical_task_ready = (
        report.gate_g1 == "sales_reachable"
        and not report.missing_canonical_tools
        and not blocked
    )
    if blocked:
        report.notes.append(
            "canonical task BLOCKED: " + ", ".join(blocked) + " not readable. Every "
            "probed entity must be readable; a partially reachable seat cannot answer "
            "the canonical question and must not report as ready.")

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
