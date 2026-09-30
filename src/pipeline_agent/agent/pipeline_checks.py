"""Pipeline checks beyond rot and uncalled - keystone_enhancement_items.md.

Everything the Keystone bug hunts showed the platform will accept without
complaint, and a sales manager would still want flagged: a deal whose contact
belongs to another customer (P2), a stage its pipeline does not define (PB2),
two deals from one lead (G6), a close date that slipped, an order that is late
although `make.orders` says it is not (K3), and so on.

Two rules hold throughout:

* Advisory only. Nothing here changes which deals are rotting or who is
  uncalled; each finding is a row with a code, the record and the evidence.
* Every source is optional. Pipeline, Party, PartyRelationship, Lead,
  Quotation, SalesOrder and the two endpoint tools are read if the seat can
  read them; if not, the check is skipped and `notes` says why. A narrower
  seat gets a narrower answer, never a crash or a refusal.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from typing import Any

from pipeline_agent.mcp.client import MCPClient, MCPToolError, PermissionDeniedError

# --- test data -------------------------------------------------------------

# Teams tag their probes and fixtures, e.g. "[team07-test KS-PA-20260929]",
# "[team10-probe] W9 todo probe", "[team11-test] schedule-probe". Such records
# are real rows in the shared book but not real pipeline (item 4.1).
TEST_TAG = re.compile(r"\[team\s*\d+[-_ ]?(test|probe)", re.IGNORECASE)


def is_test_fixture(*texts: Any) -> bool:
    return any(TEST_TAG.search(str(t or "")) for t in texts)


# --- stage tables ----------------------------------------------------------

# Default win probability per stage, used for the weighted pipeline because
# the platform's `probability` does not follow the stage (Bug 11: 0% on
# negotiation, 0% on won deals). Template values - confirm with the seat owner.
STAGE_DEFAULT_PROBABILITY = {"new": 10, "qualification": 25, "proposal": 50,
                             "negotiation": 75, "closed_won": 100, "closed_lost": 0}

# What kind of Activity the next action should be, by stage (item 7.1).
STAGE_ACTION_TYPE = {"new": "task", "qualification": "call", "proposal": "email",
                     "negotiation": "call"}

# An open deal with no contact for this long, a slipped close date and nothing
# open is a close-lost candidate (item 7.5). Proposed, never executed.
DEAD_DEAL_DAYS = 120
WORKLIST_TOP_N = 5

# US federal holidays (observed dates), for business-day due dates (item 7.2).
US_FEDERAL_HOLIDAYS = frozenset(dt.date.fromisoformat(d) for d in (
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-10-11", "2027-11-11", "2027-11-25", "2027-12-24",
))

# Company rows currently omit a timezone on both known tenants.  Falling back
# to UTC makes "today", overdue work, and 30-day windows wrong around each
# business day's boundary.  An explicit PIPELINE_TIMEZONE still wins.
TENANT_DEFAULT_TIMEZONES = {
    "keystone": "America/New_York",
    "suryodaya": "Asia/Kolkata",
}

UUID_TITLE = re.compile(r"^Deal from [0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)

# Relationship kinds that tie an individual to an organisation, in the
# direction person -> organisation, and their inverses.
_PERSON_TO_ORG = {"represents", "employee", "billing_contact"}
_ORG_TO_PERSON = {"represented_by", "employer"}

# The marker on an Activity that records a contact a human reported
# ("I called them yesterday"). The platform refuses a past due_date (PB1), so
# the real date is carried here and read back by the Activity index. It is
# deliberately NOT the agent's bookkeeping marker: this row is real contact.
LOGGED_CONTACT_MARKER = "logged_by=pipeline_agent"
ACTUAL_DATE = re.compile(r"actual_date=(\d{4}-\d{2}-\d{2})")


def actual_contact_date(activity: dict) -> str | None:
    """The real date of a reported contact, if the row carries one."""
    text = f"{activity.get('description') or ''} {activity.get('outcome') or ''}"
    if LOGGED_CONTACT_MARKER not in text:
        return None
    m = ACTUAL_DATE.search(text)
    return m.group(1) if m else None


def build_logged_contact(*, deal: dict, contact_type: str, actual_date: str, today: str,
                         outcome: str, run_id: str) -> dict:
    """Activity.create payload for a contact that already happened.

    due_date is today because the platform refuses an earlier one (PB1); the
    real date travels in the description and outcome, where the index reads it."""
    return {
        "subject": f"{contact_type.capitalize()} on {actual_date}: {display_title(deal)}",
        "type": contact_type,
        "due_date": today,
        "done": True,
        "deal_id": deal.get("id"),
        "party_id": deal.get("party_id"),
        "outcome": f"{outcome} (took place {actual_date})",
        "description": (f"Reported contact, logged after the fact. The platform cannot "
                        f"backdate an Activity, so due_date is the day it was logged. "
                        f"({LOGGED_CONTACT_MARKER} actual_date={actual_date} run={run_id})"),
    }


def display_title(deal: dict) -> str:
    """A title a person can read: 'Deal from <uuid>' (B4) shows the party."""
    title = str(deal.get("title") or "")
    if not title or UUID_TITLE.match(title):
        party = deal.get("_party_id_display") or "unknown customer"
        return f"{party} - (untitled deal)"
    return title


def add_business_days(day: dt.date, n: int,
                      holidays: frozenset[dt.date] = US_FEDERAL_HOLIDAYS) -> dt.date:
    while n > 0:
        day += dt.timedelta(days=1)
        if day.weekday() < 5 and day not in holidays:
            n -= 1
    return day


def _is_open(deal: dict) -> bool:
    return not str(deal.get("stage", "")).startswith("closed")


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            return 0.0


def _day(value: Any) -> str:
    day = str(value or "")[:10]
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        return ""
    return day


# --- context ---------------------------------------------------------------

@dataclass
class PipelineContext:
    """Optional sources, each None when unreadable."""

    today: str = ""
    tz_name: str = "UTC"
    currency_symbol: str = ""
    pipelines: dict[str, set[str]] | None = None
    parties: dict[str, dict] | None = None
    person_orgs: dict[str, set[str]] | None = None
    leads: dict[str, dict] | None = None
    quotations: list[dict] | None = None
    sales_orders: list[dict] | None = None
    account_plans: list[dict] | None = None
    known_people: set[str] | None = None
    notes: list[str] = field(default_factory=list)

    # Derived once, used by the next-action builder.
    expired_quotes_by_deal: dict[str, list[dict]] = field(default_factory=dict)
    overdue_milestones_by_deal: dict[str, list[dict]] = field(default_factory=dict)

    def money(self, value: float) -> str:
        return f"{self.currency_symbol}{value:,.0f}"


async def _read_all(client: MCPClient, entity: str, notes: list[str],
                    page_size: int = 1000) -> list[dict] | None:
    rows: list[dict] = []
    offset = 0
    try:
        while True:
            # No sort_order: on Item, Pipeline and AddressBook the entity's own
            # numeric `sort_order` column shadows the list's sort direction in
            # the MCP schema, so "desc" is rejected and a number crashes the
            # server (platform report, 2026-09-29). Default order is fine here.
            resp = await client.call_tool("list", {"entity": entity, "limit": page_size,
                                                   "offset": offset})
            records = (resp.get("records") or []) if isinstance(resp, dict) else []
            rows.extend(records)
            offset += len(records)
            total = resp.get("total") if isinstance(resp, dict) else None
            if not records or (isinstance(total, int) and offset >= total) or \
                    (not isinstance(total, int) and len(records) < page_size):
                return rows
    except PermissionDeniedError:
        notes.append(f"{entity} is not readable from this seat - checks that need it were skipped")
    except (MCPToolError, NotImplementedError) as e:
        notes.append(f"{entity} could not be read ({e}) - checks that need it were skipped")
    return None


async def _read_endpoint(client: MCPClient, name: str, notes: list[str]) -> Any:
    try:
        return await client.call_endpoint(name, {})
    except PermissionDeniedError:
        notes.append(f"{name} is not available to this seat - checks that need it were skipped")
    except (MCPToolError, NotImplementedError) as e:
        notes.append(f"{name} could not be read ({e}) - checks that need it were skipped")
    return None


async def load_company_clock(client: MCPClient, env_tz: str | None,
                              notes: list[str]) -> tuple[dt.datetime, str, str]:
    """(now in the company's timezone, tz name, currency symbol) - item 5.2/5.6.

    Keystone is in Ohio; the server stores UTC and the agent may run in IST.
    'Today', 'overdue' and '30 days' should mean the company's day. Order:
    Company.timezone, then PIPELINE_TIMEZONE, then a known tenant default, then UTC - and the run says which."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    company: dict = {}
    rows = await _read_all(client, "Company", [])
    if rows:
        company = rows[0]
    tenant_tz = TENANT_DEFAULT_TIMEZONES.get(
        os.environ.get("AGENTSWITCH_TENANT", "").strip().lower(), ""
    )
    tz_name = (company.get("timezone") or company.get("time_zone") or env_tz or tenant_tz or "UTC")
    source = ("Company.timezone" if company.get("timezone") or company.get("time_zone")
              else "PIPELINE_TIMEZONE" if env_tz
              else "tenant default" if tenant_tz else "default (no timezone configured)")
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        notes.append(f"timezone {tz_name!r} is not recognised - using UTC")
        tz, tz_name, source = dt.timezone.utc, "UTC", "fallback"
    currency = str(company.get("currency") or company.get("base_currency")
                   or company.get("default_currency") or "").upper()
    symbol = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}.get(currency, "")
    notes.append(f"dates are in {tz_name} ({source})")
    return dt.datetime.now(tz), str(tz_name), symbol


async def load_context(client: MCPClient, *, today: str, tz_name: str = "UTC",
                       currency_symbol: str = "") -> PipelineContext:
    ctx = PipelineContext(today=today, tz_name=tz_name, currency_symbol=currency_symbol)
    n = ctx.notes

    pipelines = await _read_all(client, "Pipeline", n)
    if pipelines is not None:
        ctx.pipelines = {}
        for p in pipelines:
            stages = {str(s.get("key") or s.get("name")) for s in (p.get("stages") or [])
                      if isinstance(s, dict)}
            for key in (p.get("key"), p.get("name"), p.get("id")):
                if key:
                    ctx.pipelines[str(key)] = stages

    parties = await _read_all(client, "Party", n)
    if parties is not None:
        ctx.parties = {p.get("id"): p for p in parties if p.get("id")}

    rels = await _read_all(client, "PartyRelationship", n)
    if rels is not None:
        ctx.person_orgs = {}
        for r in rels:
            if not r.get("is_active", True) or r.get("until"):
                continue
            kind, a, b = r.get("relationship"), r.get("from_party_id"), r.get("to_party_id")
            if kind in _PERSON_TO_ORG:
                ctx.person_orgs.setdefault(a, set()).add(b)
            elif kind in _ORG_TO_PERSON:
                ctx.person_orgs.setdefault(b, set()).add(a)

    leads = await _read_all(client, "Lead", n)
    if leads is not None:
        ctx.leads = {lead.get("id"): lead for lead in leads if lead.get("id")}

    ctx.quotations = await _read_all(client, "Quotation", n)
    ctx.sales_orders = await _read_all(client, "SalesOrder", n)

    plans = await _read_endpoint(client, "endpoint.crm.account_plans", n)
    if isinstance(plans, dict):
        ctx.account_plans = [p for p in (plans.get("data") or []) if isinstance(p, dict)]

    people = await _read_endpoint(client, "endpoint.people_directory", n)
    if isinstance(people, dict):
        ctx.known_people = {str(p.get("name")).strip().lower()
                            for p in (people.get("items") or people.get("data") or [])
                            if isinstance(p, dict) and p.get("name")}

    for q in ctx.quotations or []:
        valid = _day(q.get("valid_till") or q.get("expiry_date"))
        if q.get("deal_id") and q.get("status") in ("sent", "viewed") and valid and valid < today:
            ctx.expired_quotes_by_deal.setdefault(q["deal_id"], []).append(q)
    for plan in ctx.account_plans or []:
        for task in plan.get("tasks") or []:
            due = _day(task.get("due_date"))
            if plan.get("deal_id") and due and due < today and \
                    task.get("status") not in ("done", "cancelled"):
                ctx.overdue_milestones_by_deal.setdefault(plan["deal_id"], []).append(
                    {**task, "plan": plan.get("name")})
    return ctx


# --- per-deal checks -------------------------------------------------------

def contact_problem(deal: dict, ctx: PipelineContext) -> dict | None:
    """Item 1.1: is the deal's contact a person who represents the deal's customer?"""
    contact_id = deal.get("contact_id")
    if not contact_id:
        return None
    if ctx.parties is None:
        return None
    person = ctx.parties.get(contact_id)
    if person is None:
        return {"code": "contact_not_found",
                "detail": f"contact_id {contact_id[:8]} is not a readable party"}
    name = person.get("name") or contact_id[:8]
    if person.get("type") == "organization":
        return {"code": "contact_is_organisation",
                "detail": f"the contact on record, {name}, is an organisation, not a person"}
    if ctx.person_orgs is None:
        return None
    orgs = ctx.person_orgs.get(contact_id, set())
    if deal.get("party_id") in orgs:
        return None
    others = [ctx.parties.get(o, {}).get("name") or o[:8] for o in orgs]
    return {"code": "contact_of_other_customer",
            "detail": (f"the contact on record, {name}, " +
                       (f"represents {', '.join(others)}" if others
                        else "has no active relationship to any customer") +
                       f", not {deal.get('_party_id_display') or 'this deal’s customer'}")}


def valid_contact_name(deal: dict, ctx: PipelineContext) -> str | None:
    if not deal.get("contact_id") or ctx.parties is None or contact_problem(deal, ctx):
        return None
    return ctx.parties.get(deal["contact_id"], {}).get("name")


def stage_problem(deal: dict, ctx: PipelineContext) -> dict | None:
    """Item 1.3: a stage the deal's own pipeline does not define (PB2)."""
    if ctx.pipelines is None or not deal.get("pipeline"):
        return None
    stages = ctx.pipelines.get(str(deal["pipeline"]))
    if stages is None:
        return {"code": "unknown_pipeline",
                "detail": f"pipeline '{deal['pipeline']}' does not exist"}
    if deal.get("stage") not in stages:
        return {"code": "stage_not_in_pipeline",
                "detail": (f"stage '{deal.get('stage')}' is not a stage of pipeline "
                           f"'{deal['pipeline']}' ({', '.join(sorted(stages))})")}
    return None


def collect_data_issues(deals: list[dict], ctx: PipelineContext, *,
                        rot_age: dict[str, int | None],
                        has_open_action: set[str]) -> list[dict]:
    """Every advisory finding on in-scope deals, one row per (record, code)."""
    today = ctx.today
    issues: list[dict] = []

    def add(deal: dict, code: str, detail: str, **extra: Any) -> None:
        issues.append({"deal_id": deal.get("id"), "deal": display_title(deal),
                       "party": deal.get("_party_id_display") or deal.get("party_id"),
                       "value": _money(deal.get("value")), "code": code, "detail": detail,
                       **extra})

    open_deals = [d for d in deals if _is_open(d)]
    for d in open_deals:
        if (p := contact_problem(d, ctx)):
            add(d, p["code"], p["detail"])
        if (p := stage_problem(d, ctx)):
            add(d, p["code"], p["detail"])
        close = _day(d.get("expected_close_date"))
        if close and close < today:
            slipped = (dt.date.fromisoformat(today) - dt.date.fromisoformat(close)).days
            add(d, "slipped_close_date",
                f"expected_close_date {close} passed {slipped} day(s) ago", days=slipped)
        if _money(d.get("value")) <= 0:
            add(d, "unvalued", "open deal with no value - excluded from value totals")
        if not d.get("contact_id"):
            add(d, "no_contact", "no buyer contact on record")
        owner = str(d.get("owner") or "").strip()
        if not owner:
            add(d, "no_owner", "no owner")
        elif ctx.known_people is not None and owner.lower() not in ctx.known_people:
            add(d, "unknown_owner", f"owner '{owner}' is not in the people directory")
        lead = (ctx.leads or {}).get(d.get("lead_id")) if d.get("lead_id") else None
        if lead and lead.get("status") == "disqualified":
            add(d, "disqualified_lead", "opened from a lead that is disqualified")
        for q in ctx.expired_quotes_by_deal.get(d.get("id"), []):
            add(d, "expired_quote",
                f"{q.get('number')} is still '{q.get('status')}' but expired "
                f"{_day(q.get('valid_till') or q.get('expiry_date'))}")
        for m in ctx.overdue_milestones_by_deal.get(d.get("id"), []):
            add(d, "overdue_plan_milestone",
                f"account plan '{m.get('plan')}': '{m.get('title')}' due {m.get('due_date')}")
        age = rot_age.get(d.get("id"))
        if (age is not None and age >= DEAD_DEAL_DAYS and close and close < today
                and d.get("id") not in has_open_action):
            add(d, "close_lost_candidate",
                f"no contact for {age} days, close date passed, nothing open - propose "
                f"close-lost (not done automatically)")

    # Item 1.4: several open deals from one lead.
    by_lead: dict[str, list[dict]] = {}
    for d in open_deals:
        if d.get("lead_id"):
            by_lead.setdefault(d["lead_id"], []).append(d)
    for lead_id, group in by_lead.items():
        if len(group) > 1:
            ids = ", ".join(str(g.get("id"))[:8] for g in group)
            for d in group:
                add(d, "duplicate_from_lead",
                    f"{len(group)} open deals come from the same lead ({ids}) - possible "
                    f"double counting of {ctx.money(sum(_money(g.get('value')) for g in group))}")

    # Item 2.8: won, but the customer is still only a prospect.
    if ctx.parties is not None:
        for d in deals:
            if d.get("stage") != "closed_won":
                continue
            roles = {r.get("role") for r in (ctx.parties.get(d.get("party_id"), {})
                                              .get("roles") or []) if isinstance(r, dict)
                     and r.get("active", True)}
            if roles and "customer" not in roles and "prospect" in roles:
                add(d, "won_but_prospect",
                    "deal is won but the party's role is still prospect (not changed by the agent)")
    return issues


def find_late_orders(deals: list[dict], ctx: PipelineContext) -> list[dict]:
    """Item 3.1: confirmed orders past delivery_date and not delivered, for
    customers with open pipeline. Computed from SalesOrder, never from
    make.orders' `late` flag (K3)."""
    if ctx.sales_orders is None:
        return []
    today = ctx.today
    open_by_party: dict[str, list[dict]] = {}
    for d in deals:
        if _is_open(d) and d.get("party_id"):
            open_by_party.setdefault(d["party_id"], []).append(d)
    rows = []
    for so in ctx.sales_orders:
        due = _day(so.get("delivery_date") or so.get("expected_shipment_date"))
        if so.get("status") != "confirmed" or not due or due >= today:
            continue
        if so.get("delivered_status") in ("delivered", "fully_delivered"):
            continue
        party = so.get("party_id")
        if party not in open_by_party:
            continue
        rows.append({"order": so.get("number"), "party_id": party,
                     "party": so.get("_party_id_display") or party,
                     "delivery_date": due,
                     "days_late": (dt.date.fromisoformat(today) - dt.date.fromisoformat(due)).days,
                     "delivered_status": so.get("delivered_status"),
                     "net_total": _money(so.get("net_total")),
                     "open_deal_ids": [d.get("id") for d in open_by_party[party]]})
    rows.sort(key=lambda r: -r["days_late"])
    return rows


def find_leads_needing_action(deals: list[dict], ctx: PipelineContext,
                              threshold_days: int) -> list[dict]:
    """Item 2.7: qualified with no deal, next action overdue, never contacted."""
    if ctx.leads is None:
        return []
    today = dt.date.fromisoformat(ctx.today)
    with_deal = {d.get("lead_id") for d in deals if d.get("lead_id")}
    rows = []
    for lead in ctx.leads.values():
        if is_test_fixture(lead.get("notes")):
            continue
        status = lead.get("status")
        if status in ("converted", "disqualified"):
            continue
        # Same scope rule as deals: a supplier-only party is not a sales lead.
        roles = {r.get("role") for r in ((ctx.parties or {}).get(lead.get("party_id"), {})
                                         .get("roles") or [])
                 if isinstance(r, dict) and r.get("active", True)}
        if roles and "supplier" in roles and not roles & {"customer", "prospect"}:
            continue
        party = (ctx.parties or {}).get(lead.get("party_id"), {}).get("name") or lead.get("party_id")
        reasons = []
        if status == "qualified" and lead.get("id") not in with_deal:
            reasons.append("qualified but no deal opened")
        nxt = _day(lead.get("next_action"))
        if nxt and nxt < ctx.today:
            reasons.append(f"next action was due {nxt}")
        created = _day(lead.get("created_at"))
        if status == "new" and created and (today - dt.date.fromisoformat(created)).days >= \
                threshold_days:
            reasons.append(f"still 'new' {(today - dt.date.fromisoformat(created)).days} days "
                           f"after it came in - never contacted")
        if reasons:
            rows.append({"lead_id": lead.get("id"), "party": party, "status": status,
                         "value": _money(lead.get("value")), "source": lead.get("source"),
                         "reasons": reasons})
    rows.sort(key=lambda r: -r["value"])
    return rows


def weighted_pipeline(deals: list[dict], ctx: PipelineContext) -> dict:
    """Item 1.6: weighted open value from stage defaults, next to the field value."""
    by_stage: dict[str, dict] = {}
    total_default = total_field = 0.0
    for d in deals:
        if not _is_open(d):
            continue
        value = _money(d.get("value"))
        stage = str(d.get("stage") or "")
        prob = STAGE_DEFAULT_PROBABILITY.get(stage)
        field_prob = _money(d.get("probability"))
        row = by_stage.setdefault(stage, {"deals": 0, "value": 0.0, "weighted": 0.0,
                                          "default_probability": prob})
        row["deals"] += 1
        row["value"] += value
        if prob is not None:
            row["weighted"] += value * prob / 100
            total_default += value * prob / 100
        total_field += value * field_prob / 100
    return {"open_value": sum(r["value"] for r in by_stage.values()),
            "weighted_by_stage_default": round(total_default, 2),
            "weighted_by_probability_field": round(total_field, 2),
            "by_stage": by_stage,
            "basis": "stage defaults " + ", ".join(f"{k} {v}%" for k, v in
                                                   STAGE_DEFAULT_PROBABILITY.items())}


def worklist_by_owner(rotting: list[tuple[dict, dict]], top_n: int = WORKLIST_TOP_N
                      ) -> dict[str, list[dict]]:
    """Item 7.4: each owner's rotting deals ranked by value x days rotting."""
    lists: dict[str, list[dict]] = {}
    for deal, ev in rotting:
        days = ev.get("independent_rot_days") or 0
        owner = str(deal.get("owner") or "").strip() or "(no owner)"
        lists.setdefault(owner, []).append({
            "deal_id": deal.get("id"), "deal": display_title(deal),
            "value": _money(deal.get("value")), "days_rotting": days,
            "value_at_risk": round(_money(deal.get("value")) * days, 2)})
    return {o: sorted(rows, key=lambda r: -r["value_at_risk"])[:top_n]
            for o, rows in sorted(lists.items())}


def escalations_needed(results: list, issues: list[dict], late_orders: list[dict]) -> list[dict]:
    """Item 5.4: what a person has to decide, written out rather than raised
    through escalations.raise (no assignable person on Keystone, G9)."""
    out = []
    for r in results:
        if getattr(r, "action_status", None) and r.action_status.value == "needs_human_review":
            out.append({"deal_id": r.deal_id, "why": "; ".join(r.reasons),
                        "decide": "which stage this deal is really in, and its next action"})
    for i in issues:
        if i["code"] in ("contact_of_other_customer", "contact_is_organisation",
                         "duplicate_from_lead"):
            out.append({"deal_id": i["deal_id"], "why": i["detail"],
                        "decide": ("the right buyer contact" if i["code"].startswith("contact")
                                   else "which of the duplicate deals is real")})
    for o in late_orders:
        out.append({"party": o["party"], "why": f"order {o['order']} is {o['days_late']} days late "
                                                f"while the customer has open deals",
                    "decide": "who tells the customer, before the next sales touch"})
    return out
