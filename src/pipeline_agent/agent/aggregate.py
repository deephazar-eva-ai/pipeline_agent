"""`aggregate`: count and sum over a whole entity, computed by the harness.

Why it exists. Per-stage totals (job M1) need every Deal read and summed. A
model given only `list` pages the book, loses earlier pages out of its history
window, and pages again: Qwen ran out of steps on M1 on both tenants on
2026-10-06 (13 and 16 Deal pages, no answer), and Sonnet volunteered a
per-stage breakdown tallied by eye that was wrong (job E2). Paging and adding
are not judgement, so they move into code, for any model: the tool pages the
entity to the end, groups, counts and sums, and says how many records it read
against the total the platform reported.

Read-only by construction: it only calls `list`. Results are cached per
(entity, filters) for the run, so asking for a different grouping of the same
records costs no further reads.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from pipeline_agent.agent.workflow import _last_page
from pipeline_agent.mcp.client import MCPClient

TOOL_NAME = "aggregate"

PAGE_SIZE = 1000
MAX_GROUPS = 60
CLOSED_STAGES = ("closed_won", "closed_lost")

# Keys that shape the aggregation; every other argument is an exact-match filter
# passed through to `list`.
_CONTROL = frozenset({"entity", "group_by", "sum", "open_only", "limit", "offset",
                      "sort_by", "sort_order",
                      # a reply-level key Qwen put inside arguments (Suryodaya M1,
                      # 2026-10-07); passed on as a filter, the server rejected it
                      "note"})

DESCRIPTION = (
    f'- {TOOL_NAME}: read-only, counts and sums over EVERY record of an entity; the harness '
    'pages to the end for you. Arguments: "entity": E (required); "group_by": field '
    '(optional, e.g. "stage", "owner"); "sum": numeric field or list of fields (optional, '
    'e.g. "value"); "open_only": true (Deal only: leaves out closed_won and closed_lost); '
    'any other <field>:<value> is an exact-match filter. Returns records_read, '
    "total_reported and complete (true when every record was read), then per group: count, "
    "sums (split by currency when the records carry more than one), and missing (records "
    "with no number in that field). Use it for any count or total by group instead of "
    "paging list and adding up yourself.\n")


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def _plain(d: Decimal) -> int | float:
    return int(d) if d == d.to_integral_value() else float(d)


async def _read_all(client: MCPClient, entity: str, filters: dict) -> tuple[list[dict], Any]:
    records: list[dict] = []
    total = None
    offset = 0
    while True:
        resp = await client.list_(entity, filters=filters or None, limit=PAGE_SIZE,
                                  offset=offset)
        page = (resp.get("records") or []) if isinstance(resp, dict) else []
        if isinstance(resp, dict) and isinstance(resp.get("total"), int):
            total = resp["total"]
        if not page:
            return records, total
        records.extend(page)
        offset += len(page)
        if _last_page(resp, offset, len(page), PAGE_SIZE):
            return records, total


def summarise(records: list[dict], group_by: str | None, sum_fields: list[str]) -> dict:
    """Group, count and sum. Sums split by currency only when there is more than one."""
    currencies = {r.get("currency") for r in records if r.get("currency")}
    by_currency = len(currencies) > 1

    def fold(rows: list[dict]) -> dict:
        out: dict[str, Any] = {"count": len(rows)}
        for name in sum_fields:
            sums: dict[str, Decimal] = {}
            missing = 0
            for r in rows:
                n = _number(r.get(name))
                if n is None:
                    missing += 1
                    continue
                key = str(r.get("currency") or "") if by_currency else ""
                sums[key] = sums.get(key, Decimal(0)) + n
            if by_currency:
                out[f"sum_{name}"] = {k or "(no currency)": _plain(v) for k, v in sorted(sums.items())}
            else:
                out[f"sum_{name}"] = _plain(sums.get("", Decimal(0)))
            if missing:
                out[f"missing_{name}"] = missing
        return out

    result: dict[str, Any] = {"overall": fold(records)}
    if len(currencies) == 1:
        result["currency"] = next(iter(currencies))
    if group_by:
        groups: dict[str, list[dict]] = {}
        for r in records:
            value = r.get(group_by)
            groups.setdefault("(none)" if value in (None, "") else str(value), []).append(r)
        rows = [{"key": key, **fold(rows)} for key, rows in groups.items()]
        rows.sort(key=lambda g: (-g["count"], g["key"]))
        result["group_by"] = group_by
        result["group_count"] = len(rows)
        result["groups"] = rows[:MAX_GROUPS]
        if len(rows) > MAX_GROUPS:
            result["note"] = (f"only the {MAX_GROUPS} largest of {len(rows)} groups are "
                              "shown; filter to narrow it")
    return result


async def aggregate(client: MCPClient, arguments: dict | None = None, *,
                    cache: dict | None = None) -> dict:
    arguments = arguments or {}
    entity = arguments.get("entity")
    if not entity:
        return {"error": "aggregate needs arguments.entity"}
    group_by = arguments.get("group_by") or None
    raw_sum = arguments.get("sum") or []
    sum_fields = [raw_sum] if isinstance(raw_sum, str) else [str(f) for f in raw_sum]
    filters = {k: v for k, v in arguments.items() if k not in _CONTROL}
    if isinstance(arguments.get("filters"), dict):
        filters.pop("filters")
        filters.update(arguments["filters"])
    open_only = bool(arguments.get("open_only"))
    if open_only and entity != "Deal":
        return {"error": "open_only applies to Deal only"}

    key = json.dumps([entity, filters], sort_keys=True, default=str)
    store = cache.setdefault("aggregate", {}) if cache is not None else {}
    if key not in store:
        store[key] = await _read_all(client, entity, filters)
    records, total = store[key]

    read = len(records)
    if open_only:
        records = [r for r in records if r.get("stage") not in CLOSED_STAGES]
    out: dict[str, Any] = {"entity": entity, "filters": filters, "records_read": read,
                           "total_reported": total,
                           # With no total, _read_all paged until an empty or short page.
                           "complete": total is None or read >= total}
    if total is None:
        out["note_total"] = "the platform reported no total; read until a short page"
    if open_only:
        out["open_only"] = True
        out["records_used"] = len(records)
    out.update(summarise(records, group_by, sum_fields))
    return out
