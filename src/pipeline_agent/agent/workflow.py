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
from dataclasses import dataclass, field
from typing import Any

from pipeline_agent.agent.contract import (
    ActionStatus, AgentRequest, CanonicalAnswer, ContactStatus, DealResult, ExecutionMode,
    RefusalResult,
)
from pipeline_agent.agent.refusal import build_refusal
from pipeline_agent.mcp.client import MCPClient, PermissionDeniedError

DEFAULT_ROT_DAYS = 30  # schema default; overridden by the live CRMPreferences value when readable

# How far out a proposed next action is due, and what kind of Activity it is.
# Template defaults, not a Suryodaya business rule - nobody has confirmed
# either, so they are named constants rather than literals buried in the
# payload, and they are the first thing to check with the seat's owner.
NEXT_ACTION_DUE_DAYS = 3
NEXT_ACTION_TYPE = "task"

# Observed `_rot_level` values, measured across all 133 live deals on
# 2026-09-21: only `fresh`, `attention` and `none` occur. `none` is exactly
# the 49 closed deals - the level is not computed for closed pipeline.
ROT_LEVEL_FRESH = "fresh"
ROT_LEVEL_NOT_APPLICABLE = "none"

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


def is_rotting(deal: dict, threshold_days: int) -> bool:
    """Two independent signals, either of which makes a deal a candidate.

    Measured vocabulary, not assumed: across all 133 live deals on 2026-09-21
    `_rot_level` only ever takes the values `fresh`, `attention` and `none` -
    the `aging`/`stale` values this code previously filtered on do not occur,
    so it matched nothing and reported zero rotting deals on a book where the
    platform itself was flagging 81. `none` is exactly the closed deals
    (49/49), which is why closed state is read off the stage rather than
    inferred from the level.

    Rather than hard-code a level vocabulary that may grow again, anything the
    platform does not call `fresh` counts as a concern, and the numeric
    `_rot_days` is checked independently against the configured threshold.
    """
    if str(deal.get("stage", "")).startswith("closed"):
        return False
    if (deal.get("_rot_days") or 0) >= threshold_days:
        return True
    return deal.get("_rot_level") not in (ROT_LEVEL_FRESH, ROT_LEVEL_NOT_APPLICABLE)


async def load_candidate_deals(client: MCPClient, threshold_days: int) -> list[dict]:
    """Every open deal the platform or the threshold flags as rotting.

    `Deal.list` has no server-side filter on `_rot_level`/`_rot_days` (they are
    computed at read time and absent from the tool's inputSchema), so the
    selection happens client-side. Fine at 133 deals; `limit` maxes out at
    1000, which is the point where this needs paging.
    """
    resp = await client.list_("Deal", limit=1000)
    records = resp.get("records", []) if isinstance(resp, dict) else []
    return [d for d in records if is_rotting(d, threshold_days)]


@dataclass
class ActivityIndex:
    """One pass over Activity, indexed every way the workflow needs.

    Built in a single scan for two reasons. The obvious one is cost: the
    per-deal version issued one `Activity.list` per candidate deal, which on
    the live book meant 81 sequential round trips and a two-minute run.

    The load-bearing one is `by_party`. **Not one of the 175 live activities
    has a `deal_id`** (measured 2026-09-21); all 175 carry a `party_id`.
    Indexing only by deal therefore reports every deal as never-contacted -
    which is a statement about an unused foreign key, not about whether anyone
    called the customer. 35 parties are shared between deals and activities,
    so party is the linkage that actually carries contact history here.
    """

    last_done_by_deal: dict[str, str] = field(default_factory=dict)
    last_done_by_party: dict[str, str] = field(default_factory=dict)
    open_by_deal: dict[str, dict] = field(default_factory=dict)
    open_by_party: dict[str, dict] = field(default_factory=dict)
    scanned: int = 0

    def last_contacted(self, deal: dict) -> tuple[str | None, str]:
        """Most recent completed contact, and which linkage proved it."""
        deal_id, party_id = deal.get("id"), deal.get("party_id")
        if deal_id and deal_id in self.last_done_by_deal:
            return self.last_done_by_deal[deal_id], "deal"
        if party_id and party_id in self.last_done_by_party:
            return self.last_done_by_party[party_id], "party"
        return None, "none"

    def open_activity(self, deal: dict) -> tuple[dict | None, str]:
        deal_id, party_id = deal.get("id"), deal.get("party_id")
        if deal_id and deal_id in self.open_by_deal:
            return self.open_by_deal[deal_id], "deal"
        if party_id and party_id in self.open_by_party:
            return self.open_by_party[party_id], "party"
        return None, "none"


async def load_activity_index(client: MCPClient, *, page_size: int = 1000) -> ActivityIndex:
    """Scan every Activity once and index it by deal and by party.

    Written against the generic surface's aggregate `report(Activity, max,
    group_by=deal_id)`, which does not exist: the live seat serves the
    entity-scoped catalogue, and that has no aggregate tool at all (measured
    2026-09-21). The aggregation happens here instead.

    `done` comes back as 0/1 rather than a JSON boolean, hence the truthiness
    check rather than an `is True` comparison.
    """
    index = ActivityIndex()
    offset = 0
    while True:
        resp = await client.list_("Activity", limit=page_size, offset=offset)
        records = (resp.get("records") or []) if isinstance(resp, dict) else []
        if not records:
            return index
        for activity in records:
            deal_id, party_id = activity.get("deal_id"), activity.get("party_id")
            due = activity.get("due_date")
            if activity.get("done"):
                # ISO dates (YYYY-MM-DD) compare correctly as strings.
                if deal_id and due and due > index.last_done_by_deal.get(deal_id, ""):
                    index.last_done_by_deal[deal_id] = due
                if party_id and due and due > index.last_done_by_party.get(party_id, ""):
                    index.last_done_by_party[party_id] = due
            else:
                if deal_id:
                    index.open_by_deal.setdefault(deal_id, activity)
                if party_id:
                    index.open_by_party.setdefault(party_id, activity)
        index.scanned += len(records)
        offset += len(records)
        if offset >= (resp.get("total") or 0):
            return index


def classify_contact(now: dt.datetime, threshold_days: int, last_contacted: str | None,
                      basis: str = "deal") -> tuple[ContactStatus, dict]:
    """Pure function on purpose - no MCP calls, easy for a human-authored
    verifier to unit-test directly against known inputs.

    `basis` records which linkage the date came from (`deal`, `party`, or
    `none`) and travels into the evidence, because "no activity links to this
    deal" and "this customer has never been contacted" are different claims
    and only one of them is about the customer.
    """
    if last_contacted is None:
        return ContactStatus.NEVER_CONTACTED, {"last_contacted": None, "basis": basis}
    try:
        last = dt.datetime.fromisoformat(last_contacted.replace("Z", "+00:00"))
        if last.tzinfo is None:
            # Activity.due_date is a plain date; `now` is tz-aware, and
            # subtracting the two would raise rather than misreport.
            last = last.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return ContactStatus.INSUFFICIENT_EVIDENCE, {"last_contacted": last_contacted,
                                                       "basis": basis,
                                                       "reason": "unparseable date"}
    days_since = (now - last).days
    evidence = {"last_contacted": last_contacted, "days_since": days_since, "basis": basis}
    if days_since >= threshold_days:
        return ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD, evidence
    return ContactStatus.RECENTLY_CONTACTED, evidence


async def determine_next_action(client: MCPClient, deal: dict, *, mode: ExecutionMode,
                                 run_id: str, now: dt.datetime,
                                 index: ActivityIndex) -> tuple[ActionStatus, dict | None, list[str]]:
    """The idempotency-guarded write path. Every branch that can create a
    record re-reads open activities immediately beforehand - never trusts a
    list fetched earlier in the run, per the shared-book concurrency rule
    (capstone_scope.md section 3: 're-read before acting, never assume a
    stale read is still current')."""
    deal_id = deal["id"]
    reasons: list[str] = []

    existing, basis = index.open_activity(deal)
    if existing:
        if basis == "party":
            # Deliberately conservative on a shared book: a party-level match
            # may belong to another deal with the same customer. Skipping is
            # recoverable, a duplicate nudge to a customer is not - and the
            # basis is reported so a human can overrule it.
            reasons.append("an open Activity already exists for this deal's party "
                            f"(no deal-linked activity exists) - not creating a duplicate")
        else:
            reasons.append("an open Activity already exists for this deal - not creating a duplicate")
        return ActionStatus.EXISTING, existing, reasons

    stage = deal.get("stage", "")
    template = STAGE_ACTION_TEMPLATES.get(stage)
    if template is None:
        reasons.append(f"stage '{stage}' is not in the validated stage-action map - "
                        f"flagging for human review rather than inventing an action")
        return ActionStatus.NEEDS_HUMAN_REVIEW, None, reasons

    # Shaped to Activity.create's real schema: subject/type/due_date are
    # required, and additionalProperties is false - the earlier payload's
    # `stage` and `notes` keys are not Activity fields and would have been
    # rejected outright. Provenance goes in `description` because there is
    # nowhere else for it, and it is what makes an agent-created row
    # identifiable afterwards.
    due = (now + dt.timedelta(days=NEXT_ACTION_DUE_DAYS)).date().isoformat()
    proposed = {
        "subject": f"Follow up: {deal.get('title') or deal_id}",
        "type": NEXT_ACTION_TYPE,
        "due_date": due,
        "deal_id": deal_id,
        "priority": "medium",
        "description": f"{template}\n\n(created_by=pipeline_agent run={run_id}; "
                        f"stage at time of writing: {stage})",
    }

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
        deals = await load_candidate_deals(client, threshold)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Deal"], tools_attempted=["list(Deal)"])

    try:
        index = await load_activity_index(client)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Activity"],
                              tools_attempted=["list(Deal)", "list(Activity)"])

    now = dt.datetime.now(dt.timezone.utc)

    # Worst-rot first, so a capped run acts on the deals that matter most
    # rather than whatever the server happened to return first.
    deals.sort(key=lambda d: d.get("_rot_days") or 0, reverse=True)
    candidates_found = len(deals)
    scope_notes: list[str] = []

    if request.deal_ids:
        wanted = set(request.deal_ids)
        deals = [d for d in deals if d.get("id") in wanted]
        missing = wanted - {d.get("id") for d in deals}
        if missing:
            # Silence here would read as "these deals are fine". They were
            # asked for and are not in the candidate set - say which.
            scope_notes.append(f"requested deal(s) not among the rotting candidates: "
                                f"{', '.join(sorted(missing))}")
    if request.max_deals is not None:
        deals = deals[:request.max_deals]

    results: list[DealResult] = []
    for deal in deals:
        last_contacted, basis = index.last_contacted(deal)
        contact_status, contact_evidence = classify_contact(
            now, threshold, last_contacted, basis)
        action_status, next_action, reasons = await determine_next_action(
            client, deal, mode=request.mode, run_id=run_id, now=now, index=index)
        results.append(DealResult(
            deal_id=deal["id"], stage=deal.get("stage", ""),
            rot_evidence={"_rot_level": deal.get("_rot_level"), "_rot_days": deal.get("_rot_days")},
            contact_status=contact_status, contact_evidence=contact_evidence,
            action_status=action_status, next_action=next_action, reasons=reasons,
        ))

    results.sort(key=lambda r: r.rot_evidence.get("_rot_days") or 0, reverse=True)
    threshold_note = "" if used_live else "CRMPreferences.deal_rot_days unavailable - used schema default"

    scope = (f"{candidates_found} rotting deal(s) found, threshold={threshold} days."
             if len(results) == candidates_found else
             f"{candidates_found} rotting deal(s) found, threshold={threshold} days - "
             f"PARTIAL RUN: analyzed {len(results)} of them (--limit/--deal-id).")
    if scope_notes:
        scope += " " + "; ".join(scope_notes) + "."
    summary = (f"{scope} "
               f"{sum(1 for r in results if r.action_status == ActionStatus.CREATED)} next-action(s) created, "
               f"{sum(1 for r in results if r.action_status == ActionStatus.EXISTING)} already had one open.")

    return CanonicalAnswer(deal_rot_days=threshold, analyzed_at=now.isoformat(),
                            deals=results, summary=summary, unavailable_note=threshold_note,
                            candidates_found=candidates_found)
