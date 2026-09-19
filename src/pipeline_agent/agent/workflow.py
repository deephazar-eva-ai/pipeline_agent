"""Phase 2 of capstone_plan.md: the deterministic canonical workflow.

Deliberately NOT a model loop. The plan is explicit about why: "let the model
explain and resolve limited judgement, but do not leave required safety
checks to free-form prompting." Rot detection, contact classification, and -
above all - the re-read-before-write/idempotency guard around Activity.create
are plain Python, unconditionally applied, not something an LLM is trusted to
remember to do correctly every time.

Everything here runs against the abstract MCPClient, so it's exercised today
through StubMCPClient (Phase 0 smoke test) and unchanged once RealMCPClient
is wired up. Every branch that hits the sales-domain exclusion returns a
RefusalResult instead of raising past this module or fabricating a partial
answer - that's Track B, and it's live in this code today because Gate G1
(capstone_scope.md) has not been resolved yet.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from pipeline_agent.agent.contract import (
    ActionStatus, AgentRequest, CanonicalAnswer, ContactStatus, DealResult, ExecutionMode,
    RefusalResult,
)
from pipeline_agent.agent.refusal import build_refusal
from pipeline_agent.mcp.client import MCPClient, PermissionDeniedError

DEFAULT_ROT_DAYS = 30  # schema default; overridden by the live CRMPreferences value when readable

# Template only - NOT validated against live Suryodaya stage names yet
# (capstone_plan.md Phase 2, step 3: "Examples must remain templates until
# validated against live stages"). Any stage not in this map routes to
# NEEDS_HUMAN_REVIEW rather than guessing - this list is intentionally
# incomplete until someone checks it against a live DealFlow/Pipeline read.
STAGE_ACTION_TEMPLATES: dict[str, str] = {
    "new": "Qualify: confirm the lead's need and budget before moving to the next stage.",
    "qualification": "Schedule a discovery call to confirm fit before advancing to proposal.",
    "proposal": "Follow up on the sent proposal; confirm the prospect received and reviewed it.",
    "negotiation": "Re-confirm the expected close date and resolve any open commercial terms.",
}


async def load_rot_threshold(client: MCPClient) -> tuple[int, bool]:
    """Returns (deal_rot_days, used_live_value). CRMPreferences is in the crm
    domain - this call is expected to succeed under this seat's current
    policy, unlike the Deal/Activity calls below."""
    resp = await client.list_("CRMPreferences", limit=1)
    records = resp.get("records", []) if isinstance(resp, dict) else []
    if records and records[0].get("deal_rot_days") is not None:
        return int(records[0]["deal_rot_days"]), True
    return DEFAULT_ROT_DAYS, False


async def load_candidate_deals(client: MCPClient) -> list[dict]:
    """Deal is in the sales domain - excluded under this seat's current
    policy. This call is expected to raise PermissionDeniedError today; the
    caller (run_canonical_task) is responsible for turning that into a
    RefusalResult, not this function."""
    resp = await client.list_("Deal", limit=1000)
    records = resp.get("records", []) if isinstance(resp, dict) else []
    return [d for d in records if d.get("_rot_level") in ("aging", "stale")]


async def load_last_contacted_map(client: MCPClient) -> dict[str, str]:
    """Activity is in the sales domain, same exclusion as Deal. Kept as a
    separate call (rather than folded into load_candidate_deals) because the
    plan calls this out as its own smoke-test target: 'live-smoke-test the
    aggregate report(...) behavior' (open_items.md item 2) - once that's
    done, only this function's body needs revisiting."""
    resp = await client.report("Activity", aggregate="max", field="due_date",
                                group_by="deal_id", filters={"done": True})
    return resp.get("groups", {}) if isinstance(resp, dict) else {}


def classify_contact(now: dt.datetime, threshold_days: int, last_contacted: str | None) -> tuple[ContactStatus, dict]:
    """Pure function on purpose - no MCP calls, easy for a human-authored
    verifier to unit-test directly against known inputs."""
    if last_contacted is None:
        return ContactStatus.NEVER_CONTACTED, {"last_contacted": None}
    try:
        last = dt.datetime.fromisoformat(last_contacted.replace("Z", "+00:00"))
    except ValueError:
        return ContactStatus.INSUFFICIENT_EVIDENCE, {"last_contacted": last_contacted,
                                                       "reason": "unparseable date"}
    days_since = (now - last).days
    if days_since >= threshold_days:
        return ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD, {"last_contacted": last_contacted,
                                                              "days_since": days_since}
    return ContactStatus.RECENTLY_CONTACTED, {"last_contacted": last_contacted, "days_since": days_since}


async def determine_next_action(client: MCPClient, deal: dict, *, mode: ExecutionMode,
                                 run_id: str) -> tuple[ActionStatus, dict | None, list[str]]:
    """The idempotency-guarded write path. Every branch that can create a
    record re-reads open activities immediately beforehand - never trusts a
    list fetched earlier in the run, per the shared-book concurrency rule
    (capstone_scope.md section 3: 're-read before acting, never assume a
    stale read is still current')."""
    deal_id = deal["id"]
    reasons: list[str] = []

    open_activities = await client.list_("Activity", filters={"deal_id": deal_id, "done": False},
                                          sort_by="due_date", sort_order="asc", limit=1)
    existing = (open_activities.get("records") or [None])[0] if isinstance(open_activities, dict) else None
    if existing:
        reasons.append("an open Activity already exists for this deal - not creating a duplicate")
        return ActionStatus.EXISTING, existing, reasons

    stage = deal.get("stage", "")
    template = STAGE_ACTION_TEMPLATES.get(stage)
    if template is None:
        reasons.append(f"stage '{stage}' is not in the validated stage-action map - "
                        f"flagging for human review rather than inventing an action")
        return ActionStatus.NEEDS_HUMAN_REVIEW, None, reasons

    proposed = {"deal_id": deal_id, "stage": stage, "description": template,
                "notes": f"created_by=pipeline_agent run={run_id}"}

    if mode is not ExecutionMode.CREATE_NEXT_ACTIONS:
        reasons.append("propose-only mode - recommendation is not written")
        return ActionStatus.RECOMMENDED, proposed, reasons

    # Re-read immediately before the write - a duplicate may have appeared
    # since the check above, from this run's own earlier deals or another
    # team sharing the same book.
    recheck = await client.list_("Activity", filters={"deal_id": deal_id, "done": False}, limit=1)
    if (recheck.get("records") if isinstance(recheck, dict) else None):
        reasons.append("a matching open Activity appeared between the check and the write - "
                        "not creating a duplicate")
        return ActionStatus.EXISTING, recheck["records"][0], reasons

    created = await client.create("Activity", proposed)
    reasons.append("created after a fresh re-read found no existing open action")
    return ActionStatus.CREATED, created, reasons


async def run_canonical_task(client: MCPClient, request: AgentRequest, *,
                              run_id: str) -> CanonicalAnswer | RefusalResult:
    threshold, used_live = await load_rot_threshold(client)

    try:
        deals = await load_candidate_deals(client)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Deal"], tools_attempted=["list(Deal)"])

    try:
        last_contacted_map = await load_last_contacted_map(client)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Activity"],
                              tools_attempted=["list(Deal)", "report(Activity)"])

    now = dt.datetime.now(dt.timezone.utc)
    results: list[DealResult] = []
    for deal in deals:
        contact_status, contact_evidence = classify_contact(
            now, threshold, last_contacted_map.get(deal["id"]))
        action_status, next_action, reasons = await determine_next_action(
            client, deal, mode=request.mode, run_id=run_id)
        results.append(DealResult(
            deal_id=deal["id"], stage=deal.get("stage", ""),
            rot_evidence={"_rot_level": deal.get("_rot_level"), "_rot_days": deal.get("_rot_days")},
            contact_status=contact_status, contact_evidence=contact_evidence,
            action_status=action_status, next_action=next_action, reasons=reasons,
        ))

    results.sort(key=lambda r: r.rot_evidence.get("_rot_days") or 0, reverse=True)
    threshold_note = "" if used_live else "CRMPreferences.deal_rot_days unavailable - used schema default"
    summary = (f"{len(results)} rotting deal(s) found, threshold={threshold} days. "
               f"{sum(1 for r in results if r.action_status == ActionStatus.CREATED)} next-action(s) created, "
               f"{sum(1 for r in results if r.action_status == ActionStatus.EXISTING)} already had one open.")

    return CanonicalAnswer(deal_rot_days=threshold, analyzed_at=now.isoformat(),
                            deals=results, summary=summary, unavailable_note=threshold_note)
