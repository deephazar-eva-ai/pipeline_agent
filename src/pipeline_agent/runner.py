"""Phase 4: the harness's run_local equivalent.

    PYTHONPATH=src python3 -m pipeline_agent.runner --mode dry-run
    PYTHONPATH=src python3 -m pipeline_agent.runner --mode live --exec-mode create-next-actions

--mode dry-run needs no credentials: it runs the exact same workflow code
against StubMCPClient, which is how Phase 0's exit condition ("a harmless MCP
read succeeds through the adapted harness, with a persisted run folder...")
is checkable before Gate G1 is resolved.
--mode live requires AGENTSWITCH_MCP_URL/AGENTSWITCH_MCP_TOKEN. It uses the
captured JSON-RPC-over-HTTP contract; a credentialed smoke test is still
required before treating that connection as proven.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer, ExecutionMode, RefusalResult
from pipeline_agent.agent.pipeline_checks import build_logged_contact, load_company_clock
from pipeline_agent.agent.request_guard import check_request
from pipeline_agent.agent.workflow import run_canonical_task
from pipeline_agent.mcp.client import MCPToolError
from pipeline_agent.config import Settings
from pipeline_agent.harness.artifacts import run_artifact
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.harness.loop import run_loop
from pipeline_agent.harness.recording import RecordingMCPClient
from pipeline_agent.llm import LLMConfigError, build_llm
from pipeline_agent.mcp.real_client import RealMCPClient
from pipeline_agent.mcp.stub_client import StubMCPClient
from pipeline_agent.mcp.tool_names import SurfaceError
from pipeline_agent.preflight import PreflightReport, run_preflight

CANONICAL_REQUEST = "Which deals are rotting, who has not been contacted, and what is the next action on each?"


# One connection = one URL, one token, one tenant. They are taken from the real
# environment or from .env as a set, never mixed: filling each missing one on
# its own paired a Keystone token with .env's Suryodaya URL (and the reverse),
# sending one instance's credential to the other instance's server
# (2026-09-28, catalogue cases N-01/N-02).
_CONNECTION_KEYS = ("AGENTSWITCH_MCP_URL", "AGENTSWITCH_MCP_TOKEN", "AGENTSWITCH_TENANT")


def _non_negative_int(text: str) -> int:
    """--limit must be >= 0: a negative value sliced the candidate list
    (`[:-1]`) and silently dropped the worst-rot deal."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def _load_dotenv(path: str = ".env") -> None:
    """No python-dotenv dependency for four lines of parsing. Never
    overrides a variable already set in the real environment, and never
    completes a half-set connection from the file (see `_CONNECTION_KEYS`)."""
    p = Path(path)
    if not p.is_file():
        return
    connection_from_env = any(os.environ.get(k) for k in _CONNECTION_KEYS)
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _CONNECTION_KEYS and connection_from_env:
            continue
        value = value.strip()
        # KEY="value" is ordinary .env syntax; passing the quotes through made
        # a quoted ANTHROPIC_API_KEY fail to authenticate (2026-10-05).
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


PREFLIGHT_REQUEST = ("Preflight: enumerate this credential's tool catalogue and probe "
                     "read access to CRMPreferences, Deal and Activity.")


@dataclasses.dataclass
class LoggedContact:
    """Result of --task log-contact (item 5.1)."""

    written: bool
    payload: dict
    record: dict | None
    message: str


async def log_contact(client: Any, args: argparse.Namespace, *, run_id: str) -> Any:
    """Record a contact a person reports ("I called them yesterday").

    The platform refuses a past due_date (PB1), so the row is dated today and
    carries the real date; the Activity index reads it back, so rot and the
    uncalled list use the real date. Writes only in create-next-actions mode."""
    import datetime as dt
    if len(args.deal_id) != 1 or not args.contact_date:
        return RefusalResult("", ["Deal"], [], "log-contact needs exactly one --deal-id and "
                                               "--contact-date YYYY-MM-DD")
    try:
        actual = dt.date.fromisoformat(args.contact_date)
    except ValueError:
        return RefusalResult("", ["Deal"], [], f"--contact-date {args.contact_date!r} is not "
                                               f"a date (YYYY-MM-DD)")
    notes: list[str] = []
    now, tz_name, _ = await load_company_clock(client, os.environ.get("PIPELINE_TIMEZONE"), notes)
    if actual > now.date():
        return RefusalResult("", ["Activity"], [], f"{actual} is in the future ({tz_name}); "
                                                   f"only a contact that happened can be logged")
    try:
        deal = await client.get("Deal", args.deal_id[0])
    except MCPToolError as e:
        return RefusalResult("", ["Deal"], ["get(Deal)"], f"deal not readable: {e.message}")
    payload = build_logged_contact(deal=deal, contact_type=args.contact_type,
                                   actual_date=actual.isoformat(), today=now.date().isoformat(),
                                   outcome=args.outcome or "contact reported by user",
                                   run_id=run_id)
    if args.exec_mode != ExecutionMode.CREATE_NEXT_ACTIONS.value:
        return LoggedContact(False, payload, None, "propose mode - not written")
    record = await client.create("Activity", payload)
    return LoggedContact(True, payload, record,
                         f"logged; due_date is {payload['due_date']} because the platform "
                         f"cannot backdate, real date {actual} recorded in the row")


def _report_result(run: TaskRun, result: Any) -> None:
    """Fold a task's return value into the run record and say so on stdout."""
    run.final_answer = dataclasses.asdict(result)

    if isinstance(result, CanonicalAnswer):
        run.claimed_success = True
        run.ended = "done"
        run.created_record_ids = [
            d.next_action["id"] for d in result.deals
            if d.action_status.value == "created" and d.next_action and "id" in d.next_action
        ]
        print(result.summary)
        # The two seat goals, one line per row - the summary gives counts only.
        print(f"\npipeline.rotting_deals ({len(result.deals)}):")
        for d in result.deals:
            ev = d.rot_evidence
            print(f"  {d.deal_id[:8]}  {ev.get('independent_rot_days')!s:>4}d  "
                  f"platform={ev.get('_rot_days')}/{ev.get('_rot_level')}  {d.stage:<13} "
                  f"last_contact={ev.get('last_contact')}  action={d.action_status.value}")
        print(f"\npipeline.uncalled_30_days ({len(result.uncalled)}):")
        for u in result.uncalled:
            since = "never called" if u.last_called is None else f"{u.days_since}d ({u.last_called})"
            print(f"  {u.party_name:<34} {since:<20} open deals={len(u.open_deal_ids)} "
                  f"value={u.open_deal_value:,.0f}")
        if result.never_called_new:
            print(f"\nnever_called_new ({len(result.never_called_new)}): never called, "
                  f"in the book under the threshold")
            for u in result.never_called_new:
                print(f"  {u.party_name:<34} in book {u.days_since}d  open deals="
                      f"{len(u.open_deal_ids)} value={u.open_deal_value:,.0f}")
        if result.excluded_deals:
            print(f"\nexcluded_deals ({len(result.excluded_deals)}): not in either answer")
            for x in result.excluded_deals:
                print(f"  {str(x['deal_id'])[:8]}  {x['party']}: {x['reason']}")
        # Customers the two lists above disagree about - uncalled, yet with
        # deals missing from the rot list. Each deal says why it looks fresh.
        print(f"\nhidden_silence ({len(result.hidden_silence)}): "
              f"not called, but deals read as fresh")
        for h in result.hidden_silence:
            call = "never called" if h.last_called is None else \
                f"last call {h.days_since_call}d ago ({h.last_called})"
            print(f"  {h.party_name}: {call}; {len(h.masked_deals)} deal(s) worth "
                  f"{h.masked_value:,.0f} look fresh")
            for m in h.masked_deals:
                contact = "no contact on record" if m.last_contact is None else \
                    f"real last contact {m.days_since_contact}d ago ({m.last_contact})"
                print(f"    {m.deal_id[:8]} {m.value:>11,.0f}  {m.stage:<13} looks "
                      f"{m.looks_fresh_days}d old because: {m.fresh_basis_detail} "
                      f"{m.fresh_basis_date}; {contact}")
        # keystone_enhancement_items.md: advisory sections.
        print(f"\ndata_issues ({len(result.data_issues)}):")
        for i in result.data_issues:
            print(f"  {str(i.get('deal_id'))[:8]}  {i['code']:<26} {i['detail']}")
        print(f"\nlate_orders ({len(result.late_orders)}):")
        for o in result.late_orders:
            print(f"  {o['order']:<16} {o['party']:<30} {o['days_late']}d late "
                  f"(due {o['delivery_date']}, {o['delivered_status']})")
        print(f"\nleads_needing_action ({len(result.leads_needing_action)}):")
        for lead in result.leads_needing_action:
            print(f"  {str(lead['lead_id'])[:8]}  {str(lead['party']):<30} {lead['status']:<10} "
                  f"{'; '.join(lead['reasons'])}")
        print(f"\nworklist_by_owner ({len(result.worklist_by_owner)} owner(s)):")
        for owner, rows in result.worklist_by_owner.items():
            print(f"  {owner}: " + "; ".join(f"{r['deal'][:30]} ({r['days_rotting']}d, "
                                            f"{r['value']:,.0f})" for r in rows))
        print(f"\nescalations_needed ({len(result.escalations_needed)}):")
        for e in result.escalations_needed:
            print(f"  {str(e.get('deal_id') or e.get('party'))[:30]}: {e['why'][:110]} -> "
                  f"decide {e['decide']}")
        for note in result.context_notes:
            print(f"note: {note}")

    elif isinstance(result, LoggedContact):
        run.claimed_success = True
        run.ended = "done"
        if result.written and isinstance(result.record, dict) and result.record.get("id"):
            run.created_record_ids = [result.record["id"]]
        print(f"log-contact: {result.message}")
        print(f"  {result.payload['type']} '{result.payload['subject']}' due_date="
              f"{result.payload['due_date']}")

    elif isinstance(result, RefusalResult):
        run.claimed_success = False
        run.ended = "refused"
        print(f"REFUSED: {result.message}")

    elif isinstance(result, PreflightReport):
        # claimed_success here means "this seat could run the canonical task",
        # not "the probe executed" - a preflight that cleanly proves the seat
        # is blocked has done its job, but must not read as a green run.
        run.claimed_success = result.canonical_task_ready
        run.ended = "done"
        print(result.summary())
        for probe in result.probes:
            flag = "  " if probe["outcome"] == probe["expected"] else "! "
            print(f"{flag}{probe['entity']:<16} {probe['domain']:<8} {probe['outcome']}: {probe['detail']}")
        for change in result.changes_from_recorded_state:
            print(f"CHANGED: {change}")
        for note in result.notes:
            print(f"note: {note}")


async def main_async(args: argparse.Namespace) -> int:
    _load_dotenv()
    settings = Settings.from_env()
    try:
        client = StubMCPClient() if args.mode == "dry-run" else RealMCPClient(settings)
        llm = build_llm(settings.model_name) if args.task == "loop" else None
    except (RuntimeError, ValueError, SurfaceError, LLMConfigError) as exc:
        # Misconfiguration, not a failed run: no artifact is written, because
        # nothing ran. A stack trace here would bury an actionable message.
        print(f"cannot start: {exc}")
        return 2

    run_id = uuid.uuid4().hex
    prompt = PREFLIGHT_REQUEST if args.task == "preflight" else args.request
    task = {"id": f"{args.task}__{args.mode}", "prompt": prompt, "run_id": run_id}

    with run_artifact(settings.output_dir, task["id"], task=task, settings=settings) as writer:
        run = TaskRun(task_id=task["id"], model=settings.model_name or f"none ({args.mode})")
        result: Any = None
        t0 = time.time()
        try:
            guarded = (check_request(args.request)
                       if args.task in ("canonical", "loop") and args.request != CANONICAL_REQUEST
                       else None)
            if guarded is not None:
                # Refused before any tool call: nothing read, nothing written.
                result = guarded
            elif args.task == "log-contact":
                result = await log_contact(RecordingMCPClient(client, run.steps), args,
                                           run_id=run_id)
            elif args.task == "loop":
                # run_loop records its own steps and returns its own TaskRun;
                # handing it the recorder would log every call twice.
                run = await run_loop(task, client, llm, settings.model_name,
                                      max_steps=args.max_steps)
            elif args.task == "preflight":
                result = await run_preflight(RecordingMCPClient(client, run.steps),
                                              configured_surface=settings.mcp_tool_surface)
            else:
                request = AgentRequest(text=args.request, mode=ExecutionMode(args.exec_mode),
                                        max_deals=args.limit, deal_ids=args.deal_id or None)
                result = await run_canonical_task(RecordingMCPClient(client, run.steps),
                                                   request, run_id=run_id)
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            run.ended = "error"
            run.seconds = time.time() - t0
            writer.finalize(run)
            raise

        run.seconds = time.time() - t0
        if result is None:
            # The loop reports itself: what it claimed, and how it stopped.
            print(f"loop ended: {run.ended} after {run.calls} model call(s), "
                  f"claimed_success={run.claimed_success}")
            if run.error:
                print(f"error: {run.error}")
        else:
            _report_result(run, result)
        writer.finalize(run)
        print(f"run artifact: {writer.run_dir}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Seat 07 Pipeline agent once.")
    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run",
                         help="dry-run uses the in-memory stub client (no credentials needed); "
                              "live uses JSON-RPC MCP (requires AGENTSWITCH_MCP_URL/_TOKEN).")
    parser.add_argument("--contact-date", default=None,
                         help="log-contact: the date the contact actually happened (YYYY-MM-DD).")
    parser.add_argument("--contact-type", choices=["call", "meeting", "email"], default="call",
                         help="log-contact: what kind of contact it was.")
    parser.add_argument("--outcome", default=None, help="log-contact: what was agreed.")
    parser.add_argument("--task", choices=["canonical", "preflight", "loop", "log-contact"],
                         default="canonical",
                         help="canonical runs the three-part pipeline question through the "
                              "deterministic workflow; preflight is a read-only capability probe "
                              "(tool catalogue + entity access) that re-checks Gate G1 instead of "
                              "trusting the recorded result; loop hands --request to the "
                              "model-driven loop, which picks its own tool calls (needs MODEL_NAME).")
    parser.add_argument("--max-steps", type=int, default=12,
                         help="maximum model turns for --task loop.")
    parser.add_argument("--exec-mode", choices=["propose", "create-next-actions"], default="propose")
    parser.add_argument("--limit", type=_non_negative_int, default=None,
                         help="act on at most N candidate deals, worst-rot first. Use it to "
                              "smoke-test a write on one deal before letting it loose on a "
                              "shared book; the answer reports the run as PARTIAL.")
    parser.add_argument("--deal-id", action="append", default=[],
                         help="restrict the run to this deal id (repeatable). The precise "
                              "instrument for a targeted write; matches setup.deal_ids in "
                              "tasks/task_schema.json.")
    parser.add_argument("--request", default=CANONICAL_REQUEST)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
