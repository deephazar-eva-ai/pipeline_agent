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
    CREATED = "created"                    # this run created one (mode=create-next-actions)
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
