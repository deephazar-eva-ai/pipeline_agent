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

from pipeline_agent.agent import habit
from pipeline_agent.agent import snapshot as snap
from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer, ExecutionMode, RefusalResult
from pipeline_agent.agent.pipeline_checks import (
    build_logged_contact, load_company_clock, tag_call_outcome,
)
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
        os.environ.setdefault(key, value.strip())


PREFLIGHT_REQUEST = ("Preflight: enumerate this credential's tool catalogue and probe "
                     "read access to CRMPreferences, Deal and Activity.")


@dataclasses.dataclass
class LoggedContact:
    """Result of --task log-contact (item 5.1)."""

    written: bool
    payload: dict
    record: dict | None
    message: str
    # B10: keyword tags on the outcome (advisory), and the call-note draft
    # the platform generated from the logged call, if one was requested.
    outcome_tags: dict = dataclasses.field(default_factory=dict)
    call_note_draft: dict | None = None


@dataclasses.dataclass
class ScheduleResult:
    """Result of --task schedule (B7)."""

    payload: dict
    cron_line: str
    written: bool
    record: dict | None
    message: str
    caveat: str = ("An AgentTask runs the platform's own agent with this prompt; it does not "
                   "run this repository's code, so it has none of the independent rot "
                   "check, laundering defence or dedupe rules. The local cron line runs "
                   "this agent itself.")


# Tasks that can write to the shared book in create-next-actions mode.
WRITE_TASKS = ("canonical", "log-contact", "digest", "schedule")


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
    tags = tag_call_outcome(args.outcome or "")
    if args.exec_mode != ExecutionMode.CREATE_NEXT_ACTIONS.value:
        return LoggedContact(False, payload, None, "propose mode - not written",
                             outcome_tags=tags)
    record = await client.create("Activity", payload)
    message = (f"logged; due_date is {payload['due_date']} because the platform "
               f"cannot backdate, real date {actual} recorded in the row")
    draft = None
    if args.draft_call_note and isinstance(record, dict) and record.get("id"):
        if args.contact_type != "call":
            message += "; call-note draft skipped - only a call can be drafted from"
        else:
            # B10: the platform drafts a note from the logged call. The agent
            # never approves it - approval is a person's step.
            try:
                draft = await client.call_write_endpoint("endpoint.crm.call_notes.draft", {
                    "activity_id": record["id"], "operation_key": f"pa-{run_id[:32]}"})
                message += "; call-note draft requested - a person must review and approve it"
            except MCPToolError as e:
                message += f"; call-note draft not created ({e.message})"
    return LoggedContact(True, payload, record, message, outcome_tags=tags,
                         call_note_draft=draft if isinstance(draft, dict) else None)


async def schedule_digest(client: Any, args: argparse.Namespace, *, tz_name: str) -> Any:
    """B7: the AgentTask record for the daily digest, and the local cron line."""
    persona = args.persona_id or os.environ.get("PIPELINE_PERSONA_ID")
    payload = habit.agent_task_payload(persona)
    cron = habit.local_cron_line(str(Path.cwd()), tz_name)
    if args.exec_mode != ExecutionMode.CREATE_NEXT_ACTIONS.value:
        return ScheduleResult(payload, cron, False, None, "propose mode - not written")
    if not persona:
        return RefusalResult("", ["AgentTask"], [], (
            "schedule needs --persona-id (or PIPELINE_PERSONA_ID): half of this tenant's "
            "scheduled runs went to personas with filler prompts (bug 9), so the task is "
            "never created without naming the persona it must run under"))
    existing = await client.list_("AgentTask", search=habit.TASK_NAME, limit=50)
    for row in (existing.get("records") or []) if isinstance(existing, dict) else []:
        if habit.HABIT_MARKER in str(row.get("description") or ""):
            return ScheduleResult(payload, cron, False, row,
                                  f"already scheduled as AgentTask {row.get('id')} - not "
                                  f"creating a second one")
    record = await client.create("AgentTask", payload)
    back = await client.get("AgentTask", record["id"]) if isinstance(record, dict) \
        and record.get("id") else None
    ran_as = (back or {}).get("persona_id")
    note = ("persona confirmed on read-back" if ran_as == persona else
            f"WARNING: read back persona_id {ran_as!r}, asked for {persona!r}")
    return ScheduleResult(payload, cron, True, back or record,
                          f"created AgentTask {(back or record).get('id')}; {note}")


def _report_extras(result: CanonicalAnswer) -> None:
    """The crm_gap_fillup.md phase 2 sections of a canonical answer."""
    rs = result.rot_scores
    if rs:
        print(f"\nrot_scores: " + ", ".join(f"{k}={v}" for k, v in rs["bands"].items())
              + f" (history snapshots: {rs['history_snapshots']})")
        for r in rs["scored_but_not_flagged"]:
            print(f"  not flagged by the rot check: {r['title'][:40]:<40} "
                  f"{r['rot_band']} {r['rot_score']}  {r['rot_comment'][:90]}")
    sc = result.snapshot_changes
    if sc:
        if sc.get("previous_snapshot"):
            changed = {k: len(v) for k, v in (sc.get("changed") or {}).items()}
            print(f"\nsince snapshot {sc['previous_snapshot'][:19]}: "
                  f"{len(sc.get('new_deals', []))} new, {len(sc.get('gone_deals', []))} gone, "
                  f"changed {changed or 'nothing'}; regressions={len(sc['regressions'])}, "
                  f"close-date pushes={len(sc['close_date_pushes'])}")
        else:
            print(f"\nsnapshot: {sc.get('note')}")
    if result.owner_rollup:
        print(f"\nowner_rollup ({len(result.owner_rollup)}):")
        for owner, r in list(result.owner_rollup.items())[:10]:
            print(f"  {owner[:30]:<30} open {r['open_deals']:>3} ({r['open_value']:,.0f})  "
                  f"rotting {r['rotting_deals']:>3} ({r['rotting_value']:,.0f})  won "
                  f"{r['won_value']:,.0f}  customers called <=30d "
                  f"{r['customers_called_last_30d']}/{r['customers']}")
    fc = result.forecast_reconciliation
    if fc:
        if not fc.get("available"):
            print(f"\nforecast_reconciliation: not available ({fc.get('reason')})")
        else:
            print(f"\nforecast_reconciliation: platform weighted "
                  f"{fc['platform_weighted_total']:,.0f} vs ours "
                  f"{fc['our_weighted_total_same_deals']:,.0f} on the same deals; "
                  f"{len(fc['disagreements'])} disagreement(s), "
                  f"{len(fc['past_dated_buckets'])} past-dated bucket(s), "
                  f"{fc['open_deals_not_in_forecast']} open deal(s) not in the forecast")
            for d in fc["disagreements"][:10]:
                print(f"  {d['title'][:40]:<40} {d['period']}  platform "
                      f"{d['platform_weighted']:,.0f} vs ours {d['our_weighted']:,.0f}: "
                      f"{'; '.join(d['reasons'])[:100]}")


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
        _report_extras(result)
        for note in result.context_notes:
            print(f"note: {note}")

    elif isinstance(result, habit.HabitDigest):
        run.claimed_success = True
        run.ended = "done"
        run.created_record_ids = [w["id"] for w in result.written if w.get("id")]
        print(f"digest {result.today} ({result.mode})")
        print(f"\nstill flagged ({len(result.still_flagged)}):")
        for r in result.still_flagged:
            print(f"  day {r['day']:>3}  {str(r['title'])[:44]:<44} {r['rot_days']}d  "
                  f"band={r['rot_band']}")
        print(f"\nnewly flagged ({len(result.newly_flagged)}):")
        for r in result.newly_flagged:
            print(f"  {str(r['title'])[:50]:<50} {r['rot_days']}d  band={r['rot_band']}")
        if result.resolved:
            print(f"\nno longer rotting ({len(result.resolved)}): "
                  + ", ".join(str(r["deal_id"])[:8] for r in result.resolved))
        print(f"\ndecisions for a person ({len(result.todos)}):")
        for t in result.todos:
            print(f"  {'[open] ' if t['already_open'] else ''}{t['title'][:100]}")
        print(f"\nescalations ({len(result.escalations)}):")
        for e in result.escalations:
            print(f"  {'[open] ' if e['already_open'] else ''}{str(e['title'])[:50]}: "
                  f"{e['why'][:90]}")
        print(f"\ncross-seat requests ({len(result.cross_seat_requests)}):")
        for c in result.cross_seat_requests:
            print(f"  {c['seat']}: {c['question']} for {len(c['party_ids'])} customer(s) "
                  f"[{c['status']}]")
        for w in result.written:
            print(f"written: {w['entity']} {w.get('id')}")
        for w in result.not_written:
            print(f"not written: {w}")
        for note in result.notes:
            print(f"note: {note}")

    elif isinstance(result, ScheduleResult):
        run.claimed_success = True
        run.ended = "done"
        if result.written and isinstance(result.record, dict) and result.record.get("id"):
            run.created_record_ids = [result.record["id"]]
        print(f"schedule: {result.message}")
        print(f"  AgentTask: {result.payload}")
        print(f"  local cron (runs this agent):\n    "
              + result.cron_line.replace("\n", "\n    "))
        print(f"  caveat: {result.caveat}")

    elif isinstance(result, LoggedContact):
        run.claimed_success = True
        run.ended = "done"
        if result.written and isinstance(result.record, dict) and result.record.get("id"):
            run.created_record_ids = [result.record["id"]]
        print(f"log-contact: {result.message}")
        print(f"  {result.payload['type']} '{result.payload['subject']}' due_date="
              f"{result.payload['due_date']}")
        if result.outcome_tags.get("tags"):
            print(f"  outcome tags (keyword-inferred, advisory): "
                  f"{', '.join(result.outcome_tags['tags'])}")
        if result.call_note_draft and result.call_note_draft.get("id"):
            run.created_record_ids.append(result.call_note_draft["id"])

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
    if settings.tenant not in ("", "unknown"):
        # The tenant is derived from the URL; the company clock reads it from
        # the environment to pick the tenant's timezone. Without this every
        # live run fell back to UTC ("default (no timezone configured)").
        os.environ.setdefault("AGENTSWITCH_TENANT", settings.tenant)
    try:
        client = StubMCPClient() if args.mode == "dry-run" else RealMCPClient(settings)
        llm = build_llm(settings.model_name) if args.task == "loop" else None
    except (RuntimeError, ValueError, SurfaceError, LLMConfigError) as exc:
        # Misconfiguration, not a failed run: no artifact is written, because
        # nothing ran. A stack trace here would bury an actionable message.
        print(f"cannot start: {exc}")
        return 2

    writes = (args.task in WRITE_TASKS
              and args.exec_mode == ExecutionMode.CREATE_NEXT_ACTIONS.value)
    if writes and args.mode == "live" and not (args.consent_by or "").strip():
        # B9: a live write to a shared book needs a named person's go-ahead,
        # recorded in the run. Refused before any tool call.
        print("cannot start: a live write needs --consent-by NAME (who agreed to this run "
              "writing to the shared book); it is recorded in consent.json")
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
            elif args.task == "schedule":
                recorder = RecordingMCPClient(client, run.steps)
                _, tz_name, _ = await load_company_clock(
                    recorder, os.environ.get("PIPELINE_TIMEZONE"), [])
                result = await schedule_digest(recorder, args, tz_name=tz_name)
            elif args.task == "preflight":
                result = await run_preflight(RecordingMCPClient(client, run.steps),
                                              configured_surface=settings.mcp_tool_surface,
                                              tenant=settings.tenant)
            else:
                # canonical, or digest: the digest is the canonical answer in
                # propose mode, plus memory / to-dos / escalations (B7).
                digest = args.task == "digest"
                mode = ExecutionMode.PROPOSE if digest else ExecutionMode(args.exec_mode)
                request = AgentRequest(text=args.request, mode=mode,
                                        max_deals=args.limit, deal_ids=args.deal_id or None)
                snapshot_tenant = "stub" if args.mode == "dry-run" else settings.tenant
                history = snap.load_history(settings.output_dir, snapshot_tenant)
                rows: list[dict] = []
                recorder = RecordingMCPClient(client, run.steps)
                result = await run_canonical_task(recorder, request, run_id=run_id,
                                                   history=history, snapshot_sink=rows)
                if isinstance(result, CanonicalAnswer) and not request.deal_ids \
                        and request.max_deals is None:
                    # Only a full-book run is a snapshot: a capped run would
                    # read as every other deal having vanished.
                    snap.write_snapshot(writer.run_dir, snap.snapshot_document(
                        rows, tenant=snapshot_tenant, captured_at=result.analyzed_at,
                        run_id=run_id, reason="scheduled" if digest else "manual"))
                if digest and isinstance(result, CanonicalAnswer):
                    result = await habit.run_digest(
                        recorder, result, rows, today=result.analyzed_at[:10],
                        value_p90=result.rot_scores.get("value_p90"),
                        write=writes, run_id=run_id)
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
            if run.citation_check:
                c = run.citation_check
                print(f"citations: {'all numbers and dates backed by a tool result' if c['supported'] else 'UNSUPPORTED'}"
                      f" ({c['numbers_checked']} numbers, {c['dates_checked']} dates checked)"
                      + (f"; not found in any tool result: {c['unsupported_numbers']} "
                         f"{c['unsupported_dates']}" if not c["supported"] else ""))
        else:
            _report_result(run, result)
        if writes:
            writer.write_consent({
                "consent_by": (args.consent_by or "").strip()
                or "not given (dry-run against the stub; nothing reached a live book)",
                "mode": args.mode, "task": args.task, "exec_mode": args.exec_mode,
                "limit": args.limit, "deal_ids": args.deal_id,
                "created_record_ids": run.created_record_ids,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
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
    parser.add_argument("--consent-by", default=None,
                         help="who agreed to this run writing to the shared book. Required "
                              "for any live create-next-actions run; recorded in consent.json.")
    parser.add_argument("--draft-call-note", action="store_true",
                         help="log-contact: after logging a call, ask the platform to draft "
                              "a call note from it (never approved by the agent).")
    parser.add_argument("--persona-id", default=None,
                         help="schedule: the AgentPersona the digest task must run under.")
    parser.add_argument("--task", choices=["canonical", "preflight", "loop", "log-contact",
                                           "digest", "schedule"],
                         default="canonical",
                         help="canonical runs the three-part pipeline question through the "
                              "deterministic workflow; preflight is a read-only capability probe "
                              "(tool catalogue + entity access) that re-checks Gate G1 instead of "
                              "trusting the recorded result; loop hands --request to the "
                              "model-driven loop, which picks its own tool calls (needs MODEL_NAME); "
                              "digest runs canonical in propose mode and adds memory, to-dos and "
                              "escalations (written only with --exec-mode create-next-actions); "
                              "schedule proposes the daily AgentTask and a local cron line.")
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
