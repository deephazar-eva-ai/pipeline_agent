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
from pipeline_agent.agent.workflow import run_canonical_task
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


def _load_dotenv(path: str = ".env") -> None:
    """No python-dotenv dependency for four lines of parsing. Never
    overrides a variable already set in the real environment."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


PREFLIGHT_REQUEST = ("Preflight: enumerate this credential's tool catalogue and probe "
                     "read access to CRMPreferences, Deal and Activity.")


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
            if args.task == "loop":
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
    parser.add_argument("--task", choices=["canonical", "preflight", "loop"], default="canonical",
                         help="canonical runs the three-part pipeline question through the "
                              "deterministic workflow; preflight is a read-only capability probe "
                              "(tool catalogue + entity access) that re-checks Gate G1 instead of "
                              "trusting the recorded result; loop hands --request to the "
                              "model-driven loop, which picks its own tool calls (needs MODEL_NAME).")
    parser.add_argument("--max-steps", type=int, default=12,
                         help="maximum model turns for --task loop.")
    parser.add_argument("--exec-mode", choices=["propose", "create-next-actions"], default="propose")
    parser.add_argument("--limit", type=int, default=None,
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
