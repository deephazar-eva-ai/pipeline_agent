"""The Pipeline agent's operational contract - Phase 1 of capstone_plan.md.

This is the typed half of the contract; docs/agent_contract.md is the prose
half aimed at a human reader. Keep both in sync when either changes - the
dataclasses here are what workflow.py and any verifier actually import, so
this file is the one that has to be right.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionMode(str, Enum):
    PROPOSE = "propose"                    # read-only: recommend, never write
    CREATE_NEXT_ACTIONS = "create-next-actions"  # may create a missing Activity


class ContactStatus(str, Enum):
    NEVER_CONTACTED = "never_contacted"
    NOT_CONTACTED_SINCE_THRESHOLD = "not_contacted_since_threshold"
    RECENTLY_CONTACTED = "recently_contacted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ActionStatus(str, Enum):
    EXISTING = "existing"                  # a suitable open Activity already exists
    OVERDUE = "overdue"                    # an open Activity exists but is past due - nobody is
                                            # actioning it; not a duplicate, not "handled" either
    CREATED = "created"                   # this run created one (mode=create-next-actions)
    RECOMMENDED = "recommended"            # a next action was determined but NOT written -
                                            # mode=propose, or a stale re-read cancelled the write
    NEEDS_HUMAN_REVIEW = "needs_human_review"  # ambiguous/invalid stage data - do not guess
    UNAVAILABLE = "unavailable"            # required data was inaccessible


@dataclass
class AgentRequest:
    """Input contract. `text` is the canonical request verbatim or a close
    paraphrase; the rest are optional narrowing constraints, never required."""

    text: str
    owner: str | None = None
    team: str | None = None
    since: str | None = None
    mode: ExecutionMode = ExecutionMode.PROPOSE
    # Cap on how many candidate deals to act on, worst-rot first. Exists so a
    # write can be smoke-tested on one deal against a shared book. A capped
    # run reports `candidates_found` alongside the deals it analyzed, so a
    # partial answer can never read as a complete one.
    max_deals: int | None = None
    # Restrict the run to specific deals. Matches `setup.deal_ids` in
    # tasks/task_schema.json - a task owns the records it touches, so a
    # verifier can clean up only what it created.
    deal_ids: list[str] | None = None


@dataclass
class DealResult:
    """One row of the deal-by-deal output. Every field that drove a decision
    is present as evidence, not folded into a single opaque verdict."""

    deal_id: str
    stage: str
    rot_evidence: dict[str, Any]
    contact_status: ContactStatus
    contact_evidence: dict[str, Any]
    action_status: ActionStatus
    next_action: dict[str, Any] | None
    reasons: list[str] = field(default_factory=list)


@dataclass
class UncalledParty:
    """One row of the `pipeline.uncalled_30_days` answer: a customer with open
    pipeline and no completed call inside the threshold. Keyed on the party,
    not the deal, because the question asks *who* - and because calls are
    often logged against one deal of a customer that has several."""

    party_id: str
    party_name: str
    last_called: str | None        # due_date of the latest done `call`, None if never
    days_since: int | None
    open_deal_ids: list[str]
    open_deal_value: float


@dataclass
class MaskedDeal:
    """One open deal that reads fresh although its customer has not been
    called: what makes it look fresh, and how old it really is by contact."""

    deal_id: str
    title: str
    value: float
    stage: str
    looks_fresh_days: int | None     # the age the rot check used (independent_rot_days)
    fresh_because: str               # "non_call_activity" | "recently_opened" (| "platform_formula" | "no_age_data")
    fresh_basis_date: str | None     # the date that made it look fresh
    fresh_basis_detail: str          # e.g. "done meeting (not a call)", "deal opened recently"
    last_contact: str | None         # latest completed contact of any type
    days_since_contact: int | None


@dataclass
class HiddenSilence:
    """A customer who is in `uncalled` but whose open deals do not all appear
    in the rotting list - the silence is real and the rot report hides it.

    Exists because the two goals disagree on exactly the customers that matter
    most: on Keystone (2026-09-28) Allegheny ($502,598 across 4 deals) and
    Cardinal Tillage ($298,196 across 5) had gone 64 and 73 days without a
    call and 35-55 days without contact of any kind, yet read 12 days old,
    because every one of their deal records was edited on 2026-09-16."""

    party_id: str
    party_name: str
    last_called: str | None
    days_since_call: int | None
    masked_deals: list[MaskedDeal]
    masked_value: float
    open_deal_value: float


@dataclass
class CanonicalAnswer:
    """Output contract for Track A - the full three-part task."""

    deal_rot_days: int
    analyzed_at: str
    deals: list[DealResult]
    summary: str
    unavailable_note: str = ""
    # How many deals matched the rotting criteria in total. Equal to
    # len(deals) on a full run; larger when the run was capped via
    # AgentRequest.max_deals.
    candidates_found: int = 0
    # `pipeline.uncalled_30_days`: every party with an open deal whose latest
    # completed call is `deal_rot_days` or more ago, or who was never called.
    uncalled: list[UncalledParty] = field(default_factory=list)
    # The subset of `uncalled` whose deals the rot check still reads as fresh,
    # each with the reason it looks fresh. Largest hidden value first.
    hidden_silence: list[HiddenSilence] = field(default_factory=list)
    # Customers never called whose relationship (first contact or deal opened)
    # is younger than the threshold - not "uncalled for 30 days", but not
    # something to drop silently either. `days_since` holds the relationship age.
    never_called_new: list[UncalledParty] = field(default_factory=list)
    # Open deals left out of every list above, each with why. Today: supplier-
    # side deals (party role supplier, not customer/prospect). Listed rather
    # than silently dropped, so an excluded deal is never mistaken for a
    # healthy one.
    excluded_deals: list[dict[str, Any]] = field(default_factory=list)
    # keystone_enhancement_items.md. Every list below is advisory: nothing in
    # them changes which deals are rotting or who is uncalled, and each row
    # names the record, the issue code and the evidence.
    # One row per (deal or party, issue): mis-linked contact, stage outside its
    # pipeline, duplicate or disqualified-lead deal, slipped close date,
    # unvalued, no contact, unknown owner, won-but-still-prospect, expired
    # quote, overdue plan milestone, dead deal.
    data_issues: list[dict[str, Any]] = field(default_factory=list)
    # Activities whose deal belongs to a different customer than the row's own
    # party (PB3/P2). Not counted as contact for either; listed one per row so
    # a person can fix the link (C-R6: a count alone named no record).
    mismatched_links: list[dict[str, Any]] = field(default_factory=list)
    # Qualified leads with no deal, leads whose next_action date has passed,
    # and leads never contacted.
    leads_needing_action: list[dict[str, Any]] = field(default_factory=list)
    # Confirmed sales orders past delivery_date and not delivered, for
    # customers with open pipeline (computed here: make.orders `late` is
    # wrong, report K3).
    late_orders: list[dict[str, Any]] = field(default_factory=list)
    # Rotting deals ranked by value at risk (value x days rotting), top N per owner.
    worklist_by_owner: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Cases that need a person. Written out instead of calling
    # escalations.raise, which has no assignable person on Keystone (G9).
    escalations_needed: list[dict[str, Any]] = field(default_factory=list)
    # Weighted open pipeline, from per-stage default probabilities because the
    # platform's probability field does not follow the stage (Bug 11).
    weighted_pipeline: dict[str, Any] = field(default_factory=dict)
    # Which optional sources were read, which were not, and why.
    context_notes: list[str] = field(default_factory=list)


@dataclass
class RefusalResult:
    """Output contract for Track B - the mandatory refusal task.

    Presence of this type instead of CanonicalAnswer IS the signal that no
    fabricated result exists; a verifier can check `isinstance(result,
    RefusalResult)` and separately confirm zero writes happened.
    """

    missing_domain: str
    missing_entities: list[str]
    tools_attempted: list[str]
    message: str
