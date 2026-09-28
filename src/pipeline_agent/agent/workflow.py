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
    HiddenSilence, MaskedDeal, RefusalResult, UncalledParty,
)
from pipeline_agent.agent.refusal import build_refusal
from pipeline_agent.mcp.client import MCPClient, MCPToolError, PermissionDeniedError

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
# Keystone (2026-09-28) says `warning` where Suryodaya says `attention`, so
# only `fresh` and `none` are ever matched by name; anything else is flagged.
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
#
# Mirroring that formula (minus the agent's rows) was not enough. On Keystone
# (2026-09-28) all 177 activities share one `created_at` - the day the book was
# seeded - including calls dated a year earlier, so every deal with any
# activity read 11 days old and the agent found 1 rotting deal where 8 were.
# `created_at` says when a row was written, not when anyone spoke to the
# customer. Rot is therefore aged from the latest *completed* contact's
# `due_date` (never a future one, never an open task), and the platform's
# number is kept only as evidence alongside it.

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
    """Days since this deal last saw genuine customer contact.

    The latest completed, non-agent contact - linked by `deal_id`, or by
    `party_id` on rows that carry no `deal_id` - dated by `due_date`, not
    `created_at` (see the Keystone note above: an insert timestamp is not a
    contact date). Only a deal nobody has ever contacted ages from when it was
    opened (`deal.created_at`).

    `deal.updated_at` does NOT count - decided by the seat owner 2026-09-28.
    An edit to the record is not contact with the customer: on Keystone a
    bulk edit on 2026-09-16 made 13 deals worth $963,392 read 11 days old
    while their customers had gone 35-127 days without contact.

    An index built by hand without contact dates (`contact_dates_indexed`
    False) keeps the old platform-mirroring formula rather than reading every
    deal as untouched.
    """
    return _rot_age(deal, index, now)[0]


def _rot_age(deal: dict, index: ActivityIndex,
             now: dt.datetime) -> tuple[int | None, str | None]:
    """(age in days, what the age is measured from) - the basis travels into
    the evidence: "contact" or "deal_opened", or "legacy" on a hand-built index."""
    if not index.contact_dates_indexed:
        days = platform_mirror_days(deal, index, now)
        return days, None if days is None else "legacy"
    contact = _parse_ts(max((d for d in (index.last_contact_by_deal.get(deal.get("id")),
                                          index.last_unlinked_contact_by_party.get(
                                              deal.get("party_id"))) if d), default=None))
    if contact is not None:
        return (now - contact).days, "contact"
    # `created_at` only when there has never been contact. It is not a floor
    # under a contact date: on Keystone it is the seeding timestamp too
    # (2026-09-16) on deals with contact history back to May, and flooring
    # with it masked 9 deals worth $616,838 exactly as `updated_at` had.
    opened = _parse_ts(deal.get("created_at"))
    return (None, None) if opened is None else ((now - opened).days, "deal_opened")


def platform_mirror_days(deal: dict, index: ActivityIndex, now: dt.datetime) -> int | None:
    """The platform's own `_rot_days` formula with this agent's rows removed:
    what the platform *would* say had the agent kept its hands off.

    Only meaningful against the platform's own boundary, and only on deals the
    agent has written to - that comparison is the laundering defence. It is
    not a contact age (see `independent_rot_days`)."""
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
        # The platform's age, not ours: this measures the platform's line.
        days = platform_mirror_days(deal, index, now)
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
                calibration: RotCalibration, *,
                threshold_days: int | None = None) -> tuple[bool, dict]:
    """Is this deal rotting, and on what evidence?

    Three signals, reported side by side rather than collapsed; any one makes
    the deal a candidate:

    1. the platform flags it (`_rot_level` other than fresh/none);
    2. days since the last genuine contact reach `threshold_days` - the
       configured `deal_rot_days`. This is what catches Keystone, where the
       platform ages from insert timestamps and calls everything fresh;
    3. on a deal this agent has written to, the platform's own formula with
       the agent's rows removed reaches the calibrated platform boundary. This
       is the laundering defence: the agent's task must not be what makes a
       deal look fresh. It is judged on the platform's line because it asks
       what the platform itself would have said.

    Callers that pass no `threshold_days` get the calibrated boundary for
    signal 2 as well - the behaviour before contact dates were indexed.
    """
    boundary = calibration.boundary_days if threshold_days is None else threshold_days
    evidence: dict[str, Any] = {
        "_rot_level": deal.get("_rot_level"),
        "_rot_days": deal.get("_rot_days"),
    }
    if str(deal.get("stage", "")).startswith("closed"):
        # Closed deals read as `none`/0 no matter how old they are, so the
        # stage is the only honest test.
        evidence["excluded"] = "closed stage"
        return False, evidence

    days, age_basis = _rot_age(deal, index, now)
    platform_flags = deal.get("_rot_level") not in (ROT_LEVEL_FRESH, ROT_LEVEL_NOT_APPLICABLE)
    independently_flags = days is not None and days >= boundary
    agent_wrote = deal.get("id") in index.agent_written_deals
    mirror = platform_mirror_days(deal, index, now) if agent_wrote else None
    mirror_flags = mirror is not None and mirror >= calibration.boundary_days

    evidence.update({
        "independent_rot_days": days,
        "age_basis": age_basis,
        "rot_boundary_days": boundary,
        "platform_mirror_days": mirror,
        "platform_boundary_days": calibration.boundary_days,
        "last_non_agent_touch": index.last_touch_by_deal.get(deal.get("id")),
        "last_contact": max((d for d in (index.last_contact_by_deal.get(deal.get("id")),
                                          index.last_unlinked_contact_by_party.get(
                                              deal.get("party_id"))) if d), default=None),
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
    if mirror_flags and not platform_flags:
        # The headline finding. Say it in the evidence, in words, on the row
        # it applies to - not only in an aggregate at the bottom of a report.
        evidence["agent_write_suppressed_platform_signal"] = True
        evidence["note"] = (
            f"the platform reads this deal as '{deal.get('_rot_level')}' only because this "
            f"agent logged an Activity against it; with the agent's own rows excluded it "
            f"has been untouched for {mirror} days and nobody has contacted the customer")
    return platform_flags or independently_flags or mirror_flags, evidence


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
    # Latest `due_date` of a completed, non-agent Activity dated on or before
    # the scan - i.e. a contact that actually happened. `by_deal` holds
    # deal-linked rows; `unlinked_by_party` holds rows with a party but no
    # deal, which on a book that rarely fills `deal_id` is most contact.
    last_contact_by_deal: dict[str, str] = field(default_factory=dict)
    last_unlinked_contact_by_party: dict[str, str] = field(default_factory=dict)
    # The Activity `type` of each of the two contacts above (call, email,
    # meeting, task, ...), so a report can say *what* kept a deal fresh.
    last_contact_type_by_deal: dict[str, str] = field(default_factory=dict)
    last_unlinked_contact_type_by_party: dict[str, str] = field(default_factory=dict)
    # Same, restricted to `type == "call"`, for `pipeline.uncalled_30_days`.
    # Keyed by party whatever the linkage: a call logged against one deal is
    # still a call to that customer.
    last_call_by_party: dict[str, str] = field(default_factory=dict)
    last_call_by_deal: dict[str, str] = field(default_factory=dict)
    # Earliest completed contact of any type, by party and by deal: evidence
    # of how long a customer relationship has existed (see `find_uncalled`).
    first_contact_by_party: dict[str, str] = field(default_factory=dict)
    first_contact_by_deal: dict[str, str] = field(default_factory=dict)
    # Done activities whose `due_date` is not a date. Skipped, and counted so
    # the summary can say so rather than crash or silently drop them.
    unparseable_dates: int = 0
    # False on an index built by hand without the contact fields; rot then
    # falls back to `last_touch_by_deal` rather than reading "never touched".
    contact_dates_indexed: bool = False
    scanned: int = 0

    def last_called(self, party_id: str | None, deal_ids: list[str]) -> str | None:
        """Latest completed call to this customer, via the party or any of its deals."""
        dates = [self.last_call_by_party.get(party_id or "")]
        dates += [self.last_call_by_deal.get(d) for d in deal_ids]
        return max((d for d in dates if d), default=None)

    def last_contacted(self, deal: dict) -> tuple[str | None, str]:
        """Most recent completed contact, and which linkage proved it.

        The same definition rot uses (`last_contact_by_deal`, else contact
        with the party on rows carrying no `deal_id`; done, dated, not in the
        future, not the agent's own). It used to read `last_done_by_*`, which
        keep the agent's rows and future-dated rows and prefer an older
        deal-linked date over a newer party contact: on Keystone (2026-09-28)
        18 of 23 rotting rows carried two different "last contact" dates, and
        3 said `recently_contacted` on deals rotting at 33-34 days.

        A hand-built index without contact dates keeps the old reading.
        """
        deal_id, party_id = deal.get("id"), deal.get("party_id")
        if self.contact_dates_indexed:
            by_deal = self.last_contact_by_deal.get(deal_id or "")
            by_party = self.last_unlinked_contact_by_party.get(party_id or "")
            if by_deal and (not by_party or by_deal >= by_party):
                return by_deal, "deal"
            if by_party:
                return by_party, "party"
            return None, "none"
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


def _iso_day(value: Any) -> str:
    """`YYYY-MM-DD` if `value` starts with a real ISO date, else "". A string
    compare alone let '13/40/2026' through as a date that sorts before 2026,
    and `date.fromisoformat` then crashed the whole run downstream."""
    day = str(value or "")[:10]
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        return ""
    return day


def _later(current: str | None, candidate: str) -> bool:
    return candidate > (current or "")


def _prefer_open(current: dict | None, candidate: dict, today: str) -> bool:
    """Which open Activity represents "the next action" on a deal or party?

    A still-current one beats an overdue one, and among the same kind the
    earliest upcoming (or latest overdue) wins. First-seen, the old rule, let
    an item forgotten in August stand in for a call booked next week - or the
    reverse - depending on page order.
    """
    if current is None:
        return True
    cur, new = str(current.get("due_date") or ""), str(candidate.get("due_date") or "")
    cur_live, new_live = cur >= today, new >= today
    if cur_live != new_live:
        return new_live
    return new < cur if new_live else new > cur


async def load_activity_index(client: MCPClient, *, page_size: int = 1000,
                               now: dt.datetime | None = None) -> ActivityIndex:
    """Scan every Activity once and index it by deal and by party.

    Written against the generic surface's aggregate `report(Activity, max,
    group_by=deal_id)`, which does not exist: the live seat serves the
    entity-scoped catalogue, and that has no aggregate tool at all (measured
    2026-09-21). The aggregation happens here instead.

    `done` comes back as 0/1 rather than a JSON boolean, hence the truthiness
    check rather than an `is True` comparison.
    """
    index = ActivityIndex(contact_dates_indexed=True)
    today = (now or dt.datetime.now(dt.timezone.utc)).date().isoformat()
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
                # A completed contact that has actually happened: dated, not
                # in the future, and not the agent's own bookkeeping.
                day = _iso_day(due)
                if due and not day:
                    index.unparseable_dates += 1
                if day and day <= today and not authored_by_agent:
                    kind = str(activity.get("type") or "activity")
                    if party_id and not (index.first_contact_by_party.get(party_id, "9999")
                                         <= day):
                        index.first_contact_by_party[party_id] = day
                    if deal_id and not (index.first_contact_by_deal.get(deal_id, "9999") <= day):
                        index.first_contact_by_deal[deal_id] = day
                    if deal_id and _later(index.last_contact_by_deal.get(deal_id), day):
                        index.last_contact_by_deal[deal_id] = day
                        index.last_contact_type_by_deal[deal_id] = kind
                    if party_id and not deal_id and _later(
                            index.last_unlinked_contact_by_party.get(party_id), day):
                        index.last_unlinked_contact_by_party[party_id] = day
                        index.last_unlinked_contact_type_by_party[party_id] = kind
                    if activity.get("type") == "call":
                        if party_id and _later(index.last_call_by_party.get(party_id), day):
                            index.last_call_by_party[party_id] = day
                        if deal_id and _later(index.last_call_by_deal.get(deal_id), day):
                            index.last_call_by_deal[deal_id] = day
            else:
                if deal_id and _prefer_open(index.open_by_deal.get(deal_id), activity, today):
                    index.open_by_deal[deal_id] = activity
                # Party-level only when the row names no deal: an open task on a
                # sibling deal is that deal's action, not this one's. Indexing it
                # here reported deal A "overdue" on the strength of deal B's task.
                if party_id and not deal_id and _prefer_open(
                        index.open_by_party.get(party_id), activity, today):
                    index.open_by_party[party_id] = activity
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
        due = str(existing.get("due_date") or "")[:10]
        try:
            overdue_days = (now.date() - dt.date.fromisoformat(due)).days if due else 0
        except ValueError:
            overdue_days = 0  # unparseable: not provably overdue, fall through
        if overdue_days > 0:
            # An open task nobody finished is evidence the deal IS rotting,
            # not that someone is on it. Reporting it as `existing` is how
            # deal 29662773 read as handled on Keystone with its follow-up
            # 32 days overdue. Still no duplicate: a second task beside an
            # ignored first one helps nobody - a human has to action it.
            who = ("this agent" if agent_authored
                   else f"someone other than this agent (found via the {basis} link)")
            reasons.append(
                f"the open Activity '{existing.get('subject')}' was due {due} and is "
                f"{overdue_days} day(s) overdue, created by {who} - nobody has actioned it. "
                f"Not creating a duplicate; the existing one needs a human to action or "
                f"reschedule it.")
            return ActionStatus.OVERDUE, existing, reasons
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


def relationship_days(party_id: str, party_deals: list[dict], index: ActivityIndex,
                      now: dt.datetime) -> int:
    """How long this customer has demonstrably been in the book: from the
    earliest of its first completed contact (by party or on any of its open
    deals) and its open deals' `created_at`. No evidence at all reads as 0,
    so an undated customer is never reported as uncalled for 30 days."""
    dates = [index.first_contact_by_party.get(party_id)]
    dates += [index.first_contact_by_deal.get(d.get("id")) for d in party_deals]
    dates += [_iso_day(d.get("created_at")) for d in party_deals]
    dates = [d for d in dates if d]
    return (now.date() - dt.date.fromisoformat(min(dates))).days if dates else 0


def find_uncalled(deals: list[dict], index: ActivityIndex, now: dt.datetime,
                  threshold_days: int, *,
                  too_new: list[UncalledParty] | None = None) -> list[UncalledParty]:
    """`pipeline.uncalled_30_days`: who, among customers with open pipeline,
    has had no completed call in `threshold_days`.

    Pure, like `classify_contact`. One row per party, not per deal: the
    question asks who, and on Keystone two customers called 14 days ago read
    as 44-48 days uncalled per-deal because the call was logged against a
    sibling deal. Only `type == "call"` counts - an email or a meeting is
    contact, but it is not what was asked. Never-called parties sort first,
    then longest silence.
    """
    by_party: dict[str, list[dict]] = {}
    for deal in deals:
        if str(deal.get("stage", "")).startswith("closed") or not deal.get("party_id"):
            continue
        by_party.setdefault(deal["party_id"], []).append(deal)

    rows: list[UncalledParty] = []
    for party_id, party_deals in by_party.items():
        deal_ids = [d["id"] for d in party_deals]
        last = index.last_called(party_id, deal_ids)
        days = None if last is None else (now.date() - dt.date.fromisoformat(last)).days
        if days is not None and days < threshold_days:
            continue
        age = relationship_days(party_id, party_deals, index, now) if last is None else None
        if age is not None and age < threshold_days:
            # Never called, but the relationship is younger than the threshold:
            # a customer whose first deal opened today has not gone 30 days
            # without a call. Listing it (as every fresh fixture customer was
            # on 2026-09-28) buries the real silences under new names. Not
            # dropped either - collected into `too_new` so the caller can
            # report it (on Suryodaya, whose book is 16 days old, that is 35
            # never-called customers).
            if too_new is not None:
                too_new.append(UncalledParty(
                    party_id=party_id,
                    party_name=str(party_deals[0].get("_party_id_display") or party_id),
                    last_called=None, days_since=age, open_deal_ids=deal_ids,
                    open_deal_value=float(sum(d.get("value") or 0 for d in party_deals))))
            continue
        rows.append(UncalledParty(
            party_id=party_id,
            party_name=str(party_deals[0].get("_party_id_display") or party_id),
            last_called=last, days_since=days, open_deal_ids=deal_ids,
            open_deal_value=float(sum(d.get("value") or 0 for d in party_deals))))
    rows.sort(key=lambda r: (r.days_since is not None, -(r.days_since or 0)))
    return rows


# A deal is supplier-side when its party is a supplier and nothing that makes
# it a sales counterparty. Both goals are about selling; the seat owner ruled
# on 2026-09-28 that supplier deals are out of both answers. On Keystone that
# is Apex Metals (consignment stock) and Tuscarawas Machining (overflow
# capacity) - $0 purchase-side agreements tracked in the CRM on purpose.
SUPPLIER_ROLE = "supplier"
SALES_ROLES = frozenset({"customer", "prospect"})


def is_supplier_only(roles: set[str] | None) -> bool:
    """None (roles unknown) is never supplier-only: unknown is not excluded."""
    return bool(roles) and SUPPLIER_ROLE in roles and not (roles & SALES_ROLES)


async def load_party_roles(client: MCPClient, *, page_size: int = 1000
                           ) -> tuple[dict[str, set[str]] | None, str]:
    """(active roles per party id, "") - or (None, why) when Party could not
    be read.

    Party is not a required entity - the answer is still correct without it,
    only the supplier exclusion cannot be applied - so ANY tool failure
    degrades to None and the caller says why, rather than refusing or
    crashing the whole task. Catching only the permission case let a
    transient `list(Party)` error kill a complete rot answer.
    """
    roles: dict[str, set[str]] = {}
    offset = 0
    try:
        while True:
            resp = await client.list_("Party", limit=page_size, offset=offset)
            records = (resp.get("records") or []) if isinstance(resp, dict) else []
            if not records:
                return roles, ""
            for party in records:
                roles[party.get("id")] = {
                    str(r.get("role")) for r in (party.get("roles") or [])
                    if isinstance(r, dict) and r.get("active", True)}
            offset += len(records)
            if _last_page(resp, offset, len(records), page_size):
                return roles, ""
    except PermissionDeniedError:
        return None, "Party is not readable from this seat"
    except MCPToolError as e:
        return None, f"Party could not be read ({e})"


def _explain_fresh(deal: dict, index: ActivityIndex, now: dt.datetime) -> MaskedDeal:
    """Why does the rot check read this deal as fresh although its customer
    has not been called? Whichever input set its age, named with its date:
    a recent completed contact that was not a call, or a recently opened
    deal. (A record edit no longer can - `updated_at` does not count.)"""
    deal_id, party_id = deal.get("id"), deal.get("party_id")
    by_deal = index.last_contact_by_deal.get(deal_id)
    by_party = index.last_unlinked_contact_by_party.get(party_id)
    if by_deal and (not by_party or by_deal >= by_party):
        contact, kind = by_deal, index.last_contact_type_by_deal.get(deal_id, "activity")
    else:
        contact, kind = by_party, index.last_unlinked_contact_type_by_party.get(
            party_id, "activity")

    age, age_basis = _rot_age(deal, index, now)
    if age_basis == "contact":
        because, basis, detail = "non_call_activity", contact, f"done {kind} (not a call)"
    elif age_basis == "deal_opened":
        because, basis, detail = ("recently_opened", str(deal.get("created_at") or "")[:10],
                                  "deal opened recently")
    else:  # hand-built index, or no date at all
        because, basis, detail = ("platform_formula" if age_basis else "no_age_data",
                                  None, "age taken from the platform formula"
                                  if age_basis else "no usable date on the deal")
    return MaskedDeal(
        deal_id=deal_id, title=str(deal.get("title") or deal_id),
        value=float(deal.get("value") or 0), stage=str(deal.get("stage") or ""),
        looks_fresh_days=age,
        fresh_because=because, fresh_basis_date=basis, fresh_basis_detail=detail,
        last_contact=contact,
        days_since_contact=(None if not contact
                            else (now.date() - dt.date.fromisoformat(contact)).days))


def find_hidden_silence(deals: list[dict], index: ActivityIndex, now: dt.datetime,
                        uncalled: list[UncalledParty],
                        rotting_ids: set[str]) -> list[HiddenSilence]:
    """Customers the two goals disagree about: not called in the threshold
    (they are in `uncalled`), yet at least one of their open deals is absent
    from the rotting list.

    That disagreement is the finding. A reader of the rot report alone sees
    those deals as healthy; a reader of the uncalled list alone sees a name
    with no hint that the rot report vouched for it. Each masked deal says
    what made it look fresh, so nobody has to re-derive it. (On Keystone on
    2026-09-28 every masked deal was a bulk record edit on 2026-09-16 - which
    is why record edits stopped counting toward rot the same day.)

    Pure. Largest hidden value first: the question is where money is going
    quiet, and a $0 deal going quiet is not the same news.
    """
    by_id = {d.get("id"): d for d in deals}
    rows: list[HiddenSilence] = []
    for party in uncalled:
        masked = [_explain_fresh(by_id[i], index, now)
                  for i in party.open_deal_ids if i in by_id and i not in rotting_ids]
        if not masked:
            continue
        rows.append(HiddenSilence(
            party_id=party.party_id, party_name=party.party_name,
            last_called=party.last_called, days_since_call=party.days_since,
            masked_deals=sorted(masked, key=lambda m: -m.value),
            masked_value=sum(m.value for m in masked),
            open_deal_value=party.open_deal_value))
    rows.sort(key=lambda r: -r.masked_value)
    return rows


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

    now = dt.datetime.now(dt.timezone.utc)
    try:
        index = await load_activity_index(client, now=now)
    except PermissionDeniedError as e:
        return build_refusal(e, required_entities=["Activity"],
                              tools_attempted=["list(Deal)", "list(Activity)"])

    # Supplier-side deals leave both answers. Calibration below still sees
    # every deal: it measures the platform's own line, which supplier deals
    # are as good evidence for as any.
    party_roles, party_error = await load_party_roles(client)
    if party_roles is None:
        excluded: list[dict] = []
        exclusion_note = (f"{party_error} - supplier-side deals could not be identified "
                          f"and are NOT excluded.")
    else:
        excluded = [d for d in all_deals if not str(d.get("stage", "")).startswith("closed")
                    and is_supplier_only(party_roles.get(d.get("party_id")))]
        exclusion_note = ""
    excluded_ids = {d.get("id") for d in excluded}
    in_scope = [d for d in all_deals if d.get("id") not in excluded_ids]

    # Selection needs the Activity index, so it happens here rather than in
    # the Deal read. Rot is aged from genuine contact and judged against the
    # configured threshold; the calibrated platform boundary is reported as
    # context only (see `assess_rot`).
    calibration = calibrate_rot_boundary(all_deals, index, now)
    assessed: list[tuple[dict, dict]] = []
    for deal in in_scope:
        candidate, evidence = assess_rot(deal, index, now, calibration,
                                          threshold_days=threshold)
        if candidate:
            assessed.append((deal, evidence))

    # Worst-rot first on the recomputed number, not the platform's - otherwise
    # every deal the agent has already written to sorts to the bottom at 0
    # days and a capped run never reaches the ones it has been hiding.
    assessed.sort(key=lambda pair: pair[1].get("independent_rot_days") or 0, reverse=True)
    candidates_found = len(assessed)
    # Taken before --limit/--deal-id narrow the list: a deal cut from a capped
    # run is still rotting, and must not be reported as hidden silence.
    rotting_ids = {deal.get("id") for deal, _ in assessed}
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
    never_called_new: list[UncalledParty] = []
    uncalled = find_uncalled(in_scope, index, now, threshold, too_new=never_called_new)
    hidden = find_hidden_silence(in_scope, index, now, uncalled, rotting_ids)
    summary = (f"{scope} "
               f"{sum(1 for r in results if r.action_status == ActionStatus.CREATED)} next-action(s) created, "
               f"{sum(1 for r in results if r.action_status == ActionStatus.EXISTING)} already had one open, "
               f"{sum(1 for r in results if r.action_status == ActionStatus.OVERDUE)} have an open "
               f"action that is overdue. "
               f"{len(uncalled)} customer(s) with open deals not called in {threshold} days "
               f"({sum(1 for u in uncalled if u.last_called is None)} never called)"
               + (f"; {len(never_called_new)} more never called but in the book under "
                  f"{threshold} days - see never_called_new" if never_called_new else "")
               + ". "
               f"Platform context: {calibration.note()}.")
    if excluded:
        summary += (f" {len(excluded)} open supplier-side deal(s) excluded from both answers "
                    f"(party role supplier, not customer/prospect) - see excluded_deals.")
    if exclusion_note:
        summary += f" {exclusion_note}"
    no_party = [d for d in in_scope if not d.get("party_id")
                and not str(d.get("stage", "")).startswith("closed")]
    if no_party:
        # `uncalled` answers "who"; a deal with no party has no who, so it
        # cannot appear there. Say so instead of dropping it without a word.
        summary += (f" {len(no_party)} open deal(s) have no party and cannot appear in the "
                    f"uncalled list: {', '.join(str(d.get('id'))[:8] for d in no_party)}.")
    if index.unparseable_dates:
        summary += (f" {index.unparseable_dates} completed activit(ies) have an unreadable "
                    f"due_date and were not counted as contact.")
    if hidden:
        # Stated in the summary, not only in `hidden_silence`: these deals are
        # absent from the rot list above, so a reader of that list alone would
        # never learn they exist.
        summary += (
            f" HIDDEN SILENCE: {len(hidden)} customer(s) not called in {threshold}+ days "
            f"have {sum(len(h.masked_deals) for h in hidden)} open deal(s) worth "
            f"{sum(h.masked_value for h in hidden):,.0f} that the rot check reads as fresh "
            f"({sum(1 for h in hidden for m in h.masked_deals if m.fresh_because == 'non_call_activity')}"
            f" kept fresh by non-call contact, "
            f"{sum(1 for h in hidden for m in h.masked_deals if m.fresh_because == 'recently_opened')}"
            f" recently opened) - see hidden_silence.")
    if laundered:
        # Loud, and in the one line a human is guaranteed to read.
        summary += (f" WARNING: {laundered} deal(s) are shown here only because rot was "
                    f"recomputed with this agent's own Activity rows excluded - the "
                    f"platform reports them as fresh because the agent wrote to them, "
                    f"not because anyone contacted the customer.")

    return CanonicalAnswer(deal_rot_days=threshold, analyzed_at=now.isoformat(),
                            deals=results, summary=summary, unavailable_note=threshold_note,
                            candidates_found=candidates_found, uncalled=uncalled,
                            hidden_silence=hidden, never_called_new=never_called_new,
                            excluded_deals=[{
                                "deal_id": d.get("id"), "title": d.get("title"),
                                "party": d.get("_party_id_display") or d.get("party_id"),
                                "value": d.get("value"),
                                "reason": "supplier-side: party role supplier, "
                                          "not customer/prospect"} for d in excluded])
