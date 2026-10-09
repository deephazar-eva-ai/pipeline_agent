#!/usr/bin/env python3
"""Operator CLI for IT-01 setup, direct snapshots, adversary changes and cleanup.

Read-only commands never mutate AgentSwitch. Every mutation needs ``--apply``
and writes only records carrying the IT-01 tag or IDs recorded in fixtures.json.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pipeline_agent.agent.pipeline_checks import load_company_clock
from pipeline_agent.config import Settings
from pipeline_agent.mcp.real_client import RealMCPClient
from pipeline_agent.mcp.client import MCPToolError
from scenario import DEALS, PARTIES, TAG, contact_payload, concurrent_d10_payload, deal_payload, expired_quote_payload, seed_adversary_payloads

SNAPSHOT_ENTITIES = ("Deal", "Activity", "Party", "Pipeline", "Quotation", "SalesOrder", "CRMPreferences", "Company")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


async def all_rows(client: RealMCPClient, entity: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        # Some entity-scoped list tools (Pipeline on Keystone) reject the
        # generic ``sort_order`` field. A snapshot needs completeness, not a
        # particular order, so call the common interface without sorting.
        page = await client.call_tool("list", {"entity": entity, "limit": 1000, "offset": offset})
        batch = page.get("records", []) if isinstance(page, dict) else []
        if not isinstance(batch, list):
            raise RuntimeError(f"{entity}.list returned no records list")
        rows.extend(row for row in batch if isinstance(row, dict))
        total = page.get("total") if isinstance(page, dict) else None
        if not batch or len(batch) < 1000 or (isinstance(total, int) and len(rows) >= total):
            return rows
        offset += len(batch)


async def snapshot(client: RealMCPClient, run_dir: Path, label: str) -> None:
    target = run_dir / label
    index: dict[str, Any] = {"taken_at": dt.datetime.now(dt.timezone.utc).isoformat(), "entities": {}}
    for entity in SNAPSHOT_ENTITIES:
        try:
            rows = await all_rows(client, entity)
            write_json(target / f"{entity}.json", rows)
            index["entities"][entity] = {"count": len(rows), "complete": True}
        except MCPToolError as exc:
            index["entities"][entity] = {"complete": False, "error": str(exc)}
    write_json(target / "manifest.json", index)


def fixture_path(run_dir: Path) -> Path:
    return run_dir / "fixtures.json"


async def require_cleanup_transitions(client: RealMCPClient) -> None:
    """Refuse to seed a stage this seat cannot later close safely."""
    names = {str(item.get("name")) for item in (await client.list_tools() or [])
             if isinstance(item, dict)}
    missing = sorted({spec.stage for spec in DEALS.values()
                      if f"Deal.mark_lost.{spec.stage}.closed_lost" not in names})
    if missing:
        raise SystemExit("refusing to seed stages without a close-lost transition: " + ", ".join(missing))


async def seed(client: RealMCPClient, run_dir: Path, *, apply: bool) -> None:
    if not apply:
        raise SystemExit("seed is a live mutation; repeat with --apply after the shared-book announcement")
    path = fixture_path(run_dir)
    if path.exists():
        raise SystemExit(f"refusing to overwrite existing fixture ledger: {path}")
    await require_cleanup_transitions(client)
    company_page = await client.call_tool("list", {"entity": "Company", "limit": 1, "offset": 0})
    company_rows = company_page.get("records", []) if isinstance(company_page, dict) else []
    company_id = company_rows[0].get("id") if company_rows and isinstance(company_rows[0], dict) else None
    if not company_id:
        raise SystemExit("refusing to seed: Company.list returned no company id for the required Quotation fixture")
    notes: list[str] = []
    now, tz_name, _ = await load_company_clock(client, os.environ.get("PIPELINE_TIMEZONE"), notes)
    company_today = now.date()
    # The company evening is already the next UTC date. Keystone validates an
    # Activity's due_date against its server-side creation date, not Company.
    server_today = dt.datetime.now(dt.timezone.utc).date()
    ids: dict[str, Any] = {"tag": TAG, "company_id": company_id,
                           "company_today": company_today.isoformat(), "timezone": tz_name,
                           "parties": {}, "deals": {}, "activities": {}}
    # Persist after every create: a failed seed is still cleanable.
    write_json(path, ids)
    for key, (name, role) in PARTIES.items():
        record = await client.create("Party", {"name": name, "roles": [{"role": role, "active": True}]})
        ids["parties"][key] = record["id"]
        write_json(path, ids)
    for key in DEALS:
        record = await client.create("Deal", deal_payload(key, ids["parties"][DEALS[key].party], now.date()))
        ids["deals"][key] = record["id"]
        write_json(path, ids)
        activity = contact_payload(key, record["id"], ids["parties"][DEALS[key].party], company_today,
                                   server_today=server_today)
        if activity:
            made = await client.create("Activity", activity)
            ids["activities"][f"{key}-contact"] = made["id"]
            write_json(path, ids)
    for key, payload in seed_adversary_payloads(ids, company_today, server_today=server_today):
        made = await client.create("Activity", payload)
        ids["activities"][key] = made["id"]
        write_json(path, ids)
    quote = await client.create("Quotation", expired_quote_payload(ids, now.date()))
    ids["quotation_id"] = quote["id"]
    write_json(path, ids)
    # Quotation.create makes a draft; the fixture needs the real sent state.
    await client._transport.call_tool("Quotation.send", {"id": quote["id"]})
    print(f"seeded {len(ids['parties'])} parties, {len(ids['deals'])} deals; ledger: {path}")


async def adversary(client: RealMCPClient, run_dir: Path, action: str, *, apply: bool) -> None:
    if not apply:
        raise SystemExit(f"adversary {action} is a live mutation; repeat with --apply")
    ids = read_json(fixture_path(run_dir))
    today = dt.date.fromisoformat(ids["company_today"])
    if action == "d10-party-action":
        made = await client.create(
            "Activity", concurrent_d10_payload(ids, today, server_today=dt.datetime.now(dt.timezone.utc).date()))
        ids["activities"]["D10-concurrent"] = made["id"]
    elif action in ("d15-close", "d16-close"):
        key = "D15" if action.startswith("d15") else "D16"
        await mark_lost(client, ids["deals"][key])
        ids.setdefault("adversary", {})[action] = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        raise SystemExit(f"unknown adversary action {action}")
    write_json(fixture_path(run_dir), ids)


async def mark_lost(client: RealMCPClient, deal_id: str) -> bool:
    """Use Keystone's state-specific Deal transition, never an invalid update.

    The entity-scoped catalogue exposes e.g.
    ``Deal.mark_lost.proposal.closed_lost`` rather than accepting ``stage`` in
    Deal.update. A deliberately malformed stage has no matching transition;
    the caller records it for UI cleanup instead of guessing a state change.
    """
    deal = await client.get("Deal", deal_id)
    stage = str(deal.get("stage") or "") if isinstance(deal, dict) else ""
    # A concurrent adversary may already have closed the fixture. That is the
    # desired terminal cleanup state, not a manual-cleanup failure.
    if stage == "closed_lost":
        return True
    tool = f"Deal.mark_lost.{stage}.closed_lost"
    tools = await client.list_tools() or []
    if tool not in {str(item.get("name")) for item in tools if isinstance(item, dict)}:
        return False
    # This is an entity-scoped transition outside the generic MCPClient
    # vocabulary. Its input schema was inspected and contains only ``id``.
    await client._transport.call_tool(tool, {"id": deal_id})
    return True


async def cleanup(client: RealMCPClient, run_dir: Path, *, apply: bool) -> None:
    if not apply:
        raise SystemExit("cleanup is a live mutation; repeat with --apply")
    ids = read_json(fixture_path(run_dir))
    for activity_id in ids.get("activities", {}).values():
        await client.update("Activity", activity_id, {"done": True, "type": "task", "outcome": "Cancelled",
                                                        "description": f"{TAG} cleanup"})
    pending: list[str] = []
    for key, deal_id in ids.get("deals", {}).items():
        if not await mark_lost(client, deal_id):
            pending.append(key)
    quote_id = ids.get("quotation_id")
    if quote_id:
        quote = await client.get("Quotation", quote_id)
        quote_stage = str(quote.get("status") or "") if isinstance(quote, dict) else ""
        quote_tool = f"Quotation.decline.{quote_stage}.declined"
        quote_tools = {str(item.get("name")) for item in (await client.list_tools() or [])
                       if isinstance(item, dict)}
        if quote_tool in quote_tools:
            await client._transport.call_tool(quote_tool, {"id": quote_id})
        else:
            pending.append("D14-quotation")
    if pending:
        ids.setdefault("cleanup", {})["manual_close_required"] = pending
        write_json(fixture_path(run_dir), ids)
        print("manual UI close still required for custom-stage fixture(s): " + ", ".join(pending))
    elif ids.get("cleanup", {}).pop("manual_close_required", None) is not None:
        write_json(fixture_path(run_dir), ids)


def approve(run_dir: Path, run_b_artifact: str, approver: str, keys: list[str]) -> None:
    expected = {"D3", "D8", "D9a", "D10", "D14", "D15"}
    if set(keys) != expected or len(keys) != len(expected):
        raise SystemExit("approval must name exactly D3,D8,D9a,D10,D14,D15")
    ids = read_json(fixture_path(run_dir))
    proposal = Path(run_b_artifact)
    if not (proposal / "input.json").is_file() or not (proposal / "result.json").is_file():
        raise SystemExit("--run-b must be a completed runner artifact with input.json and result.json")
    write_json(run_dir / "approval.json", {
        "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "approver": approver,
        "run_b_artifact": str(proposal), "approved_keys": keys,
        "approved_deal_ids": [ids["deals"][key] for key in keys],
    })


async def main_async(args: argparse.Namespace) -> None:
    client = RealMCPClient(Settings.from_env())
    run_dir = Path(args.run_dir)
    if args.command == "snapshot":
        if not re.fullmatch(r"S[0-7](?:-[a-z0-9][a-z0-9-]*)?", args.label):
            raise SystemExit("snapshot label must be S0 through S7, optionally suffixed for an interrupted attempt")
        await snapshot(client, run_dir, args.label)
    elif args.command == "seed":
        await seed(client, run_dir, apply=args.apply)
    elif args.command == "adversary":
        await adversary(client, run_dir, args.action, apply=args.apply)
    elif args.command == "cleanup":
        await cleanup(client, run_dir, apply=args.apply)
    else:
        approve(run_dir, args.run_b, args.approver, args.deal_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="runs/IT-01/<run-id>")
    parser.add_argument("--apply", action="store_true", help="permit a mutation command")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("label", help="S0..S7, or e.g. S1-partial for interrupted evidence")
    sub.add_parser("seed")
    adv = sub.add_parser("adversary")
    adv.add_argument("action", choices=("d10-party-action", "d15-close", "d16-close"))
    sub.add_parser("cleanup")
    approval = sub.add_parser("approve")
    approval.add_argument("--run-b", required=True)
    approval.add_argument("--approver", required=True)
    approval.add_argument("--deal-key", action="append", required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
