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

# Written into the description of every Activity this agent creates. Load
# bearing, not cosmetic: the rot recomputation below excludes rows carrying
# it, and that exclusion is the only thing stopping the agent from laundering
# its own bookkeeping into "fresh". Changing this string silently disarms
# `independent_rot_days`, so it lives here and is referenced, never retyped.
PROVENANCE_MARKER = "created_by=pipeline_agent"

# Observed `_rot_level` values, measured across all 133 live deals on
# 2026-09-21: only `fresh`, `attention` and `none` occur. `none` is exactly
# the 49 closed deals - the level is not computed for closed pipeline.
ROT_LEVEL_FRESH = "fresh"
ROT_LEVEL_NOT_APPLICABLE = "none"

# How the platform actually computes `_rot_days`, derived on 2026-09-22 by
# reproducing it rather than by reading any documentation:
#
#     _rot_days == (now - max(deal.updated_at,
#                             latest Activity.created_at linked to the deal)).days
#
# Zero mismatches across all 84 open deals. `Activity.due_date` is ruled out -
# it mismatches on exactly one deal, the one this agent wrote to. Closed deals
# are pinned at `_rot_days=0` / `_rot_level='none'` regardless of true age
# (theirs ranges 4..9 days on this book), which is why closed state is read off
# the stage and never inferred from the level.
#
# That formula is the whole reason this module recomputes rot instead of
# trusting the platform: an Activity the AGENT creates is a linked Activity,
# so the agent's own write resets the very signal that selected the deal.
# Measured across two days on deal 7de1dd77: `attention`/8 on 2026-09-21, one
# agent-logged task, then `fresh`/0 on 2026-09-22 while every untouched peer
# aged 8 -> 9. Nobody contacted that customer. Logging a task is not contact,
# and an agent that trusts `_rot_level` will quietly launder its whole book.

# Used only when the live book offers nothing to calibrate against (see
# `calibrate_rot_boundary`). Deliberately low: over-reporting a deal as
# needing attention is recoverable, hiding a rotting one is not.
ROT_BOUNDARY_FALLBACK = 5


def _last_page(resp: Any, offset: int, got: int, page_size: int) -> bool:
    """Has the scan reached the end of the collection?

    A missing `total` must mean "unknown", not "zero". The previous test was
    `offset >= (resp.get("total") or 0)`, which on a response without `total`
    evaluates to `1000 >= 0` and stops after the first page - a silently
    partial index that reads as a complete one. The live server does return
    `total`, so this was latent; any proxy, cache or future response shape
    that dropped the field turned it into a wrong answer.

    So: trust `total` when it is actually present, and otherwise keep paging
    until a short page proves the end.
    """
    total = resp.get("total") if isinstance(resp, dict) else None
    if isinstance(total, int):
        return offset >= total
    return got < page_size


def _parse_ts(value: Any) -> dt.datetime | None:
    """Platform timestamps are naive ISO (`2026-09-12T17:19:56.312557`) while
    `now` is tz-aware UTC; subtracting the two raises rather than misreports,
    so naive values are read as UTC explicitly."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed

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


def is_agent_authored(activity: dict) -> bool:
    """Did this agent create this Activity? The provenance marker in
    `description` is the only handle available - `created_by` holds the seat's
    user id, which is shared with every human using the same credential."""
    return PROVENANCE_MARKER in str(activity.get("description") or "")


def independent_rot_days(deal: dict, index: ActivityIndex,
                          now: dt.datetime) -> int | None:
    """`_rot_days` recomputed from inputs this agent has not touched.

    Same formula the platform uses (derived above), with one change: Activity
    rows carrying the provenance marker are excluded. On an untouched deal
    this returns exactly the platform's number; on a deal the agent has
    written to it returns what the platform *would* have said had the agent
    kept its hands off. That difference is the laundering.
    """
    basis = max([ts for ts in (_parse_ts(deal.get("updated_at")),
                                _parse_ts(index.last_touch_by_deal.get(deal.get("id"))))
                 if ts is not None], default=None)
    return None if basis is None else (now - basis).days


@dataclass
class RotCalibration:
    """Where the platform draws the `fresh` -> `attention` line.

    Nothing exposes it. `GET /api/deal-rot-config` says `default_rot_days: 14`
    and `CRMPreferences.deal_rot_days` says `30`; the observed flip happens at
    neither, so both are reported as context and neither is used as the line.
    It is measured instead, from deals the agent has never written to - the
    only deals whose level is still trustworthy.
    """

    boundary_days: int
    lower: int | None = None
    upper: int | None = None
    exact: bool = False
    basis: str = ""

    def note(self) -> str:
        if self.exact:
            return (f"rot boundary measured exactly at {self.boundary_days} days "
                    f"({self.basis})")
        if self.lower is not None:
            return (f"rot boundary lies in [{self.lower}, {self.upper}] days and is not "
                    f"pinned down by this book; using the conservative lower bound "
                    f"{self.boundary_days} ({self.basis})")
        return f"rot boundary not measurable: using {self.boundary_days} ({self.basis})"


def calibrate_rot_boundary(deals: list[dict], index: ActivityIndex, now: dt.datetime,
                            fallback: int = ROT_BOUNDARY_FALLBACK) -> RotCalibration:
    """Bracket the boundary between the oldest `fresh` deal and the youngest
    flagged one, using only deals with no agent-authored activity.

    The bracket is usually a range, not a point - on 2026-09-22 the live book
    gives [5, 9], because no deal happens to sit in the gap. The lower bound is
    taken: at the boundary the choice is between showing a human a deal that
    turned out to be fine and hiding one that is rotting, and only one of those
    is recoverable. A run says which it used, so nobody has to guess.
    """
    fresh_days: list[int] = []
    flagged_days: list[int] = []
    for deal in deals:
        if deal.get("_rot_level") == ROT_LEVEL_NOT_APPLICABLE:
            continue
        if deal.get("id") in index.agent_written_deals:
            continue  # its level is exactly what we do not trust
        days = independent_rot_days(deal, index, now)
        if days is None:
            continue
        (fresh_days if deal.get("_rot_level") == ROT_LEVEL_FRESH
         else flagged_days).append(days)

    if not fresh_days or not flagged_days:
        return RotCalibration(boundary_days=fallback, basis=(
            f"no usable calibration pair (fresh n={len(fresh_days)}, "
            f"flagged n={len(flagged_days)})"))

    lower, upper = max(fresh_days) + 1, min(flagged_days)
    basis = (f"oldest fresh deal {max(fresh_days)}d, youngest flagged deal {upper}d, "
             f"over {len(fresh_days) + len(flagged_days)} agent-untouched deals")
    return RotCalibration(boundary_days=min(lower, upper), lower=lower, upper=upper,
                           exact=lower == upper, basis=basis)


def assess_rot(deal: dict, index: ActivityIndex, now: dt.datetime,
                calibration: RotCalibration) -> tuple[bool, dict]:
    """Is this deal rotting, and on what evidence?

    Two signals, reported side by side rather than collapsed: what the platform
    says, and what the platform would say if this agent had never written to
    the deal. Either one is enough to make it a candidate - which is the fix
    for the feedback loop, because the recomputed signal does not reset when
    the agent logs a task.
    """
    evidence: dict[str, Any] = {
        "_rot_level": deal.get("_rot_level"),
        "_rot_days": deal.get("_rot_days"),
    }
    if str(deal.get("stage", "")).startswith("closed"):
        # Closed deals read as `none`/0 no matter how old they are, so the
        # stage is the only honest test.
        evidence["excluded"] = "closed stage"
        return False, evidence

    days = independent_rot_days(deal, index, now)
    platform_flags = deal.get("_rot_level") not in (ROT_LEVEL_FRESH, ROT_LEVEL_NOT_APPLICABLE)
    independently_flags = days is not None and days >= calibration.boundary_days

    evidence.update({
        "independent_rot_days": days,
        "rot_boundary_days": calibration.boundary_days,
        "last_non_agent_touch": index.last_touch_by_deal.get(deal.get("id")),
        "platform_flags": platform_flags,
        "independently_flags": independently_flags,
    })
    # The two signals disagreeing is NOT by itself evidence of laundering: the
    # platform's boundary can simply be wider than ours, which is the normal
    # case on this book (2026-09-24: every open deal reads `fresh`, so all 83
    # candidates disagreed and only one had ever been written to). The claim
    # below names this agent as the cause, so it is only honest when this agent
    # actually wrote to this deal. Without the membership test it fired on 82
    # deals the agent had never touched - a false accusation on its own report,
    # and the fastest way to teach a reader to ignore the one warning that
    # matters.
    if independently_flags and not platform_flags and deal.get("id") in index.agent_written_deals:
        # The headline finding. Say it in the evidence, in words, on the row
        # it applies to - not only in an aggregate at the bottom of a report.
        evidence["agent_write_suppressed_platform_signal"] = True
        evidence["note"] = (
            f"the platform reads this deal as '{deal.get('_rot_level')}' only because this "
            f"agent logged an Activity against it; with the agent's own rows excluded it "
            f"has been untouched for {days} days and nobody has contacted the customer")
    return platform_flags or independently_flags, evidence


async def load_deals(client: MCPClient, *, page_size: int = 1000) -> list[dict]:
    """Every deal, unfiltered.

    `Deal.list` has no server-side filter on `_rot_level`/`_rot_days` (they are
    computed at read time and absent from the tool's inputSchema), so selection
    happens client-side - and it now has to, because the selection needs the
    Activity index to recompute rot, which is not loaded yet at this point.
    Fine at 133 deals; `limit` maxes out at 1000, which is where this needs
    paging.

    The full list is kept rather than filtered here: `calibrate_rot_boundary`
    needs the deals that are NOT rotting to find the boundary.

    Pages, because a single `limit=1000` read silently returned 1000 of a
    reported 2500 - a confident answer computed from 40% of the book, with no
    error and no warning. At 138 deals that was dormant; it wakes up at 1001.
    """
    deals: list[dict] = []
    offset = 0
    while True:
        resp = await client.list_("Deal", limit=page_size, offset=offset)
        records = (resp.get("records") or []) if isinstance(resp, dict) else []
        if not records:
            return deals
        deals.extend(records)
        offset += len(records)
        if _last_page(resp, offset, len(records), page_size):
            return deals


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
    # Latest `created_at` of a deal-linked Activity this agent did NOT write.
    # Mirrors the platform's own rot input with the agent's contribution
    # removed - see the derivation note at the top of this module.
    last_touch_by_deal: dict[str, str] = field(default_factory=dict)
    agent_written_deals: set[str] = field(default_factory=set)
    scanned: int = 0

    def last_contacted(self, deal: dict) -> tuple[str | None, str]:
        """Most recent completed contact, and which linkage proved it."""
        deal_id, party_id = deal.get("id"), deal.get("party_id")
        if deal_id and deal_id in self.last_done_by_deal:
            return self.last_done_by_deal[deal_id], "deal"
        if party_id and party_id in self.last_done_by_party:
            return self.last_done_by_party[party_id], "party"
        return None, "none"

    def open_activity(self, deal: dict) -> tuple[dict | None, str, bool]:
        """The open action, which linkage found it, and whether this agent
        wrote it. The third value exists because "somebody is on this" and
        "the agent left itself a note" look identical in the data and mean
        opposite things to the human reading the report."""
        deal_id, party_id = deal.get("id"), deal.get("party_id")
        if deal_id and deal_id in self.open_by_deal:
            activity = self.open_by_deal[deal_id]
            return activity, "deal", is_agent_authored(activity)
        if party_id and party_id in self.open_by_party:
            # Agent-created rows carry no party_id (measured: the one this
            # agent wrote has party_id=None), so this branch is always human.
            return self.open_by_party[party_id], "party", False
        return None, "none", False


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

            # Rot bookkeeping, kept separate from contact bookkeeping below.
            # Only deal-linked rows matter here: the platform's `_rot_days`
            # ignores party-linked activity entirely (81 deals sat at the full
            # age of their `updated_at` despite their parties having activity),
            # so mirroring it means mirroring that too.
            authored_by_agent = is_agent_authored(activity)
            if deal_id and authored_by_agent:
                index.agent_written_deals.add(deal_id)
            created = activity.get("created_at")
            if deal_id and created and not authored_by_agent:
                if str(created) > index.last_touch_by_deal.get(deal_id, ""):
                    index.last_touch_by_deal[deal_id] = str(created)

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
        if _last_page(resp, offset, len(records), page_size):
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
    if days_since < 0:
        # A completed activity dated in the future. Activity has no
        # `completed_at`, so `due_date` is the only date available and it is
        # a scheduling field, not a record of when anyone spoke to anyone.
        # What is actually known is "this was completed, when is unknown",
        # which is what INSUFFICIENT_EVIDENCE means - the same treatment an
        # unparseable date already gets above. Reporting `recently_contacted`
        # on a negative elapsed time drew a positive conclusion from an
        # impossible number, and dropped the deal from the report.
        evidence["reason"] = ("completion date is in the future - Activity has no "
                               "completed_at, so the date of contact is unknown")
        return ContactStatus.INSUFFICIENT_EVIDENCE, evidence
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

    existing, basis, agent_authored = index.open_activity(deal)
    if existing:
        if agent_authored:
            # Not creating a duplicate is still right. Calling it "already
            # handled" is not: this is the agent's own unactioned note, and
            # reporting it as an existing action is precisely how a rotting
            # deal disappears from everyone's view.
            reasons.append(
                f"the only open Activity on this deal was created by this agent "
                f"({PROVENANCE_MARKER}, due {existing.get('due_date')}) and is still not "
                f"done - that is bookkeeping, not evidence anyone contacted the customer. "
                f"Not creating a second one; a human needs to action the existing task.")
        elif basis == "party":
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
        "description": f"{template}\n\n({PROVENANCE_MARKER} run={run_id}; "
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
    try:
        threshold, used_live = await load_rot_threshold(client)
        threshold_note = ("" if used_live else
                          "CRMPreferences.deal_rot_days unavailable - used schema default")
    except PermissionDeniedError as e:
        # CRMPreferences is NOT a required entity: the contract defines a
        # refusal as "the moment a REQUIRED entity is inaccessible", and names
        # only Deal and Activity. Refusing here would discard a complete,
        # correct rotting-deals answer over a preferences lookup.
        #
        # It is also cheap to lose: `threshold` no longer selects rotting deals
        # at all - `calibrate_rot_boundary` measures that from live data - it
        # only feeds contact classification. So this degrades one column rather
        # than invalidating the answer, and the answer says so.
        #
        # Previously this call sat outside every guard, so a refusal escaped
        # `run_canonical_task` entirely and the run died with a traceback,
        # returning neither of the two results the contract allows.
        threshold, used_live = DEFAULT_ROT_DAYS, False
        threshold_note = (
            f"CRMPreferences is not readable from this seat ({e.message}) - used the "
            f"schema default of {DEFAULT_ROT_DAYS} days for contact classification. "
            f"Rot selection is unaffected: the boundary is measured from live data.")

    try:
        all_deals = await load_deals(client)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Deal"], tools_attempted=["list(Deal)"])

    try:
        index = await load_activity_index(client)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Activity"],
                              tools_attempted=["list(Deal)", "list(Activity)"])

    now = dt.datetime.now(dt.timezone.utc)

    # Selection needs the Activity index, so it happens here rather than in
    # the Deal read: rot is recomputed with this agent's own writes excluded,
    # and the boundary that decides "rotting" is measured off the deals the
    # agent has never touched.
    calibration = calibrate_rot_boundary(all_deals, index, now)
    assessed: list[tuple[dict, dict]] = []
    for deal in all_deals:
        candidate, evidence = assess_rot(deal, index, now, calibration)
        if candidate:
            assessed.append((deal, evidence))

    # Worst-rot first on the recomputed number, not the platform's - otherwise
    # every deal the agent has already written to sorts to the bottom at 0
    # days and a capped run never reaches the ones it has been hiding.
    assessed.sort(key=lambda pair: pair[1].get("independent_rot_days") or 0, reverse=True)
    candidates_found = len(assessed)
    scope_notes: list[str] = []

    if request.deal_ids:
        wanted = set(request.deal_ids)
        assessed = [pair for pair in assessed if pair[0].get("id") in wanted]
        missing = wanted - {pair[0].get("id") for pair in assessed}
        if missing:
            # Silence here would read as "these deals are fine". They were
            # asked for and are not in the candidate set - say which.
            scope_notes.append(f"requested deal(s) not among the rotting candidates: "
                                f"{', '.join(sorted(missing))}")
    if request.max_deals is not None:
        assessed = assessed[:request.max_deals]

    results: list[DealResult] = []
    for deal, rot_evidence in assessed:
        last_contacted, basis = index.last_contacted(deal)
        contact_status, contact_evidence = classify_contact(
            now, threshold, last_contacted, basis)
        action_status, next_action, reasons = await determine_next_action(
            client, deal, mode=request.mode, run_id=run_id, now=now, index=index)
        results.append(DealResult(
            deal_id=deal["id"], stage=deal.get("stage", ""),
            rot_evidence=rot_evidence,
            contact_status=contact_status, contact_evidence=contact_evidence,
            action_status=action_status, next_action=next_action, reasons=reasons,
        ))

    results.sort(key=lambda r: r.rot_evidence.get("independent_rot_days") or 0, reverse=True)

    scope = (f"{candidates_found} rotting deal(s) found, threshold={threshold} days."
             if len(results) == candidates_found else
             f"{candidates_found} rotting deal(s) found, threshold={threshold} days - "
             f"PARTIAL RUN: analyzed {len(results)} of them (--limit/--deal-id).")
    if scope_notes:
        scope += " " + "; ".join(scope_notes) + "."
    laundered = sum(1 for r in results
                    if r.rot_evidence.get("agent_write_suppressed_platform_signal"))
    summary = (f"{scope} "
               f"{sum(1 for r in results if r.action_status == ActionStatus.CREATED)} next-action(s) created, "
               f"{sum(1 for r in results if r.action_status == ActionStatus.EXISTING)} already had one open. "
               f"{calibration.note()}.")
    if laundered:
        # Loud, and in the one line a human is guaranteed to read.
        summary += (f" WARNING: {laundered} deal(s) are shown here only because rot was "
                    f"recomputed with this agent's own Activity rows excluded - the "
                    f"platform reports them as fresh because the agent wrote to them, "
                    f"not because anyone contacted the customer.")

    return CanonicalAnswer(deal_rot_days=threshold, analyzed_at=now.isoformat(),
                            deals=results, summary=summary, unavailable_note=threshold_note,
                            candidates_found=candidates_found)
