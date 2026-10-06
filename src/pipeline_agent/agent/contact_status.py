"""`contact_status`: the workflow's contact and rot judgement, as one read-only
tool the model-driven loop can call (complex_jobs_plan.md item P3).

Why it exists. Given only the platform's tools, the loop answers "which deals
are rotting" from `_rot_days`, which resets whenever any Activity is linked to
a deal. On 2026-10-05 that gave 1 rotting deal where real customer contact gave
25 (job J1). The model read the data correctly; the data understates rot. The
judgement that corrects it - contact traced through the party, the agent's own
rows excluded, supplier and test-fixture deals left out - already lives in
`workflow.py`, so this tool returns that judgement rather than asking the
model to re-derive it from raw pages it cannot hold in its window.

Read-only by construction: it runs the canonical task in propose mode, which
never writes, plus plain reads for the per-deal rows.

The analysis runs once per loop run and is cached; each call returns one view
of it (a summary, or a page of one section). On Suryodaya (about 90 open deals,
200+ data issues) returning everything at once overran the result budget and
the model re-called the tool four times (job V5/H1, 2026-10-06).
"""
from __future__ import annotations

import os
from typing import Any

from pipeline_agent.agent import pipeline_checks as checks
from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer, ExecutionMode
from pipeline_agent.agent.workflow import (
    _rot_age, load_activity_index, load_deals, run_canonical_task,
)
from pipeline_agent.mcp.client import MCPClient

TOOL_NAME = "contact_status"

PAGE = 30          # default rows per page
MAX_PAGE = 60

DESCRIPTION = (
    f'- {TOOL_NAME}: read-only, the seat\'s own rot/contact/pipeline analysis, computed once '
    'per run. {} returns a summary (counts, threshold, uncalled customers, data-issue '
    'counts, late-order count) plus the first page of deal rows. Arguments: '
    '"section": "deals" | "data_issues" | "late_orders" | "uncalled"; "offset", "limit" '
    f'(max {MAX_PAGE}) to page a section; for deals also "rotting_only": true or '
    '"deal_id": id; for data_issues also "code": one issue code. A deal row has the last '
    "real customer contact (completed call/meeting/email, traced through the customer, "
    "this agent's own tasks excluded), days since contact, whether it is rotting and "
    "why_rotting, the open next action and whether it is overdue, the deal's quotes "
    "(status, expiry, expired) and orders made from them, and the customer's late orders. "
    "Data issues include stages the deal's pipeline does not define and activities linked "
    "to another customer's deal. Prefer it over _rot_days, which resets when tasks are "
    "logged or completed. Each page says total and next_offset.\n")

_CANONICAL = "Which deals are rotting, who has not been contacted, and what is the next action on each?"


def _day(value: Any) -> str | None:
    return str(value)[:10] if value else None


def _why(ev: dict) -> str:
    why = []
    if ev.get("independently_flags"):
        why.append(f"no real contact for {ev.get('independent_rot_days')} days "
                   f"(threshold {ev.get('rot_boundary_days')})")
    if ev.get("platform_flags"):
        why.append(f"the platform itself flags it (_rot_level={ev.get('_rot_level')})")
    if ev.get("agent_write_suppressed_platform_signal"):
        why.append("platform reads it fresh only because this agent wrote to it")
    return "; ".join(why)


async def analyse(client: MCPClient, *, run_id: str = "loop") -> dict:
    """The whole analysis, every section in full. `view` pages it."""
    answer = await run_canonical_task(
        client, AgentRequest(text=_CANONICAL, mode=ExecutionMode.PROPOSE), run_id=run_id)
    if not isinstance(answer, CanonicalAnswer):
        return {"error": getattr(answer, "message", "the analysis was refused")}

    now, tz_name, symbol = await checks.load_company_clock(
        client, os.environ.get("PIPELINE_TIMEZONE"), [])
    today = now.date().isoformat()
    deals = {d.get("id"): d for d in await load_deals(client) if d.get("id")}
    index = await load_activity_index(
        client, now=now, deal_party={i: d.get("party_id") for i, d in deals.items()})
    ctx = await checks.load_context(client, today=today, tz_name=tz_name,
                                    currency_symbol=symbol)

    quotes_by_deal: dict[str, list[dict]] = {}
    for q in ctx.quotations or []:
        if q.get("deal_id"):
            expiry = _day(q.get("valid_till") or q.get("expiry_date"))
            quotes_by_deal.setdefault(q["deal_id"], []).append({
                "id": q.get("id"), "number": q.get("number") or q.get("name"),
                "status": q.get("status"), "expiry": expiry,
                "expired": bool(expiry and expiry < today
                                and q.get("status") in ("sent", "viewed")),
                "grand_total": q.get("grand_total")})
    quote_deal = {q["id"]: d for d, qs in quotes_by_deal.items() for q in qs if q.get("id")}
    orders_by_deal: dict[str, list[dict]] = {}
    late_by_party: dict[str, int] = {}
    for so in ctx.sales_orders or []:
        due = _day(so.get("delivery_date") or so.get("expected_shipment_date"))
        late = bool(so.get("status") == "confirmed" and due and due < today
                    and so.get("delivered_status") not in ("delivered", "fully_delivered"))
        if late and so.get("party_id"):
            late_by_party[so["party_id"]] = late_by_party.get(so["party_id"], 0) + 1
        deal_id = quote_deal.get(so.get("quotation_id"))
        if deal_id:
            orders_by_deal.setdefault(deal_id, []).append({
                "number": so.get("number"), "status": so.get("status"),
                "delivered_status": so.get("delivered_status"), "delivery_date": due,
                "late": late})

    rotting = {r.deal_id: r for r in answer.deals}
    excluded = {e["deal_id"]: e["reason"] for e in answer.excluded_deals}
    rows = []
    for deal_id, deal in deals.items():
        if str(deal.get("stage", "")).startswith("closed"):
            continue
        last_contact, basis = index.last_contacted(deal)
        days, age_basis = _rot_age(deal, index, now)
        existing, _, agent_authored = index.open_activity(deal)
        row = {
            "deal_id": deal_id, "title": deal.get("title"),
            "customer": deal.get("_party_id_display") or deal.get("party_id"),
            "owner": deal.get("owner"), "stage": deal.get("stage"),
            "value": deal.get("value"), "currency": deal.get("currency"),
            "expected_close_date": _day(deal.get("expected_close_date")),
            "last_contact": _day(last_contact), "contact_link": basis,
            "days_since_contact": days, "age_measured_from": age_basis,
            "rotting": deal_id in rotting, "platform_rot_days": deal.get("_rot_days"),
        }
        if existing:
            due = _day(existing.get("due_date"))
            row["open_next_action"] = {"subject": existing.get("subject"), "due": due,
                                       "overdue": bool(due and due < today),
                                       "written_by_this_agent": agent_authored}
        if deal_id in rotting:
            row["proposed_action"] = rotting[deal_id].action_status.value
            row["why_rotting"] = _why(rotting[deal_id].rot_evidence)
        row["quotes"] = quotes_by_deal.get(deal_id, [])
        row["orders"] = orders_by_deal.get(deal_id, [])
        row["customer_late_orders"] = late_by_party.get(deal.get("party_id"), 0)
        if deal_id in excluded:
            row["excluded"] = excluded[deal_id]
        rows.append(row)
    rows.sort(key=lambda r: (r.get("excluded") is not None, -(r["days_since_contact"] or 0)))

    issues = [{"code": i["code"], "deal_id": i.get("deal_id"), "deal": i.get("deal"),
               "detail": str(i.get("detail", ""))[:160]} for i in answer.data_issues]
    # Two checks the canonical answer does not list for every open deal:
    # stage_problem runs only on in-scope deals there (test fixtures are
    # excluded), and mismatched links are only counted.
    listed = {(i["code"], i["deal_id"]) for i in issues}
    for deal in deals.values():
        if str(deal.get("stage", "")).startswith("closed"):
            continue
        problem = checks.stage_problem(deal, ctx)
        if problem and (problem["code"], deal.get("id")) not in listed:
            issues.append({"code": problem["code"], "deal_id": deal.get("id"),
                           "deal": deal.get("title"), "detail": problem["detail"],
                           **({"excluded": excluded[deal["id"]]} if deal.get("id") in excluded
                              else {})})
    for link in index.mismatched_links:
        issues.append({"code": "activity_linked_to_other_customers_deal",
                       "deal_id": link.get("deal_id"),
                       "deal": (deals.get(link.get("deal_id")) or {}).get("title"),
                       "detail": f"activity '{link.get('subject')}' has party "
                                 f"{link.get('activity_party_id')} but its deal belongs to "
                                 f"{link.get('deal_party_id')}"})
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["code"]] = counts.get(issue["code"], 0) + 1

    return {
        "summary": {
            "analyzed_at": answer.analyzed_at, "today": today, "timezone": tz_name,
            "rot_threshold_days": answer.deal_rot_days, "open_deals": len(rows),
            "rotting_deals": answer.candidates_found, "excluded_open_deals": len(excluded),
            "uncalled_customers": len(answer.uncalled),
            "data_issue_counts": counts, "late_orders": len(answer.late_orders),
        },
        "deals": rows,
        "data_issues": issues,
        "late_orders": [{"customer": o.get("party"), "order": o.get("order"),
                         "delivery_date": o.get("delivery_date"),
                         "days_late": o.get("days_late"),
                         "open_deal_ids": o.get("open_deal_ids")} for o in answer.late_orders],
        "uncalled": [{"customer": u.party_name, "last_called": u.last_called,
                      "days_since": u.days_since, "open_deal_ids": u.open_deal_ids,
                      "open_deal_value": u.open_deal_value} for u in answer.uncalled],
    }


def _page(rows: list, arguments: dict) -> dict:
    try:
        offset = max(0, int(arguments.get("offset") or 0))
        limit = min(MAX_PAGE, max(1, int(arguments.get("limit") or PAGE)))
    except (TypeError, ValueError):
        offset, limit = 0, PAGE
    chunk = rows[offset:offset + limit]
    out = {"total": len(rows), "offset": offset, "returned": len(chunk), "records": chunk}
    if offset + len(chunk) < len(rows):
        out["next_offset"] = offset + len(chunk)
    return out


def view(analysis: dict, arguments: dict | None = None) -> dict:
    """One view of a cached analysis: the summary, or a page of one section."""
    if "error" in analysis:
        return analysis
    arguments = arguments or {}
    section = arguments.get("section") or "summary"
    if section == "summary":
        first = _page(analysis["deals"], {"limit": arguments.get("limit")})
        return {**analysis["summary"], "deals_page": first,
                "uncalled": analysis["uncalled"][:PAGE]}
    if section not in ("deals", "data_issues", "late_orders", "uncalled"):
        return {"error": f"unknown section {section!r}; use summary, deals, data_issues, "
                         "late_orders or uncalled"}
    rows = analysis[section]
    if section == "deals":
        if arguments.get("deal_id"):
            rows = [r for r in rows if r["deal_id"] == arguments["deal_id"]]
        if arguments.get("rotting_only"):
            rows = [r for r in rows if r["rotting"]]
    if section == "data_issues" and arguments.get("code"):
        rows = [r for r in rows if r["code"] == arguments["code"]]
    return {"section": section, **_page(rows, arguments)}


async def contact_status(client: MCPClient, arguments: dict | None = None, *,
                         run_id: str = "loop", cache: dict | None = None) -> dict:
    """Compute once per run (when given a `cache` dict), then return the view asked for."""
    if cache is not None and "analysis" in cache:
        analysis = cache["analysis"]
    else:
        analysis = await analyse(client, run_id=run_id)
        if cache is not None and "error" not in analysis:
            cache["analysis"] = analysis
    return view(analysis, arguments)
