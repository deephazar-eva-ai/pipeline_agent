"""
Entry point for the central AgentSwitch evaluator.

agentswitch-harness.toml points at this module. The evaluator checks out the
submitted commit, installs it, and runs it once per instance against a fresh,
throwaway copy of that instance, with no internet access beyond the endpoints
it supplies. Only results.json is scored.

All configuration comes from the evaluator's environment:

    AGENTSWITCH_BASE_URL, AGENTSWITCH_TOKEN, AGENTSWITCH_INSTANCE
    OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL

We deliberately don't load .env here (no runner._load_dotenv), so a
developer's local credentials can't end up standing in for the evaluator's.

Two things to keep in mind when editing this file:

  - results.json must always exist. It's rewritten after every task, so if a
    task hangs past the evaluator's timeout the finished rows are still
    there, and a crash leaves a failed row explaining what happened.
  - Every task is read-only. The canonical task runs in PROPOSE mode and the
    loop uses the default WritePolicy (no writes). Both check the recorded
    steps afterwards and fail if anything was written.

To try it locally without the evaluator:

    python -m pipeline_agent.central_runner --dry-run

which uses the stub MCP client and skips the model tasks.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer, ExecutionMode, RefusalResult
from pipeline_agent.agent.request_guard import check_request
from pipeline_agent.agent.workflow import run_canonical_task
from pipeline_agent.config import Settings

from pipeline_agent.harness.loop import WritePolicy, run_loop
from pipeline_agent.harness.recording import RecordingMCPClient
from pipeline_agent.llm import build_llm
from pipeline_agent.mcp.client import MCPClient
from pipeline_agent.mcp.real_client import RealMCPClient
from pipeline_agent.mcp.stub_client import StubMCPClient
from pipeline_agent.preflight import run_preflight

LLM = Callable[[str, str], Awaitable[str]]
TaskResult = tuple[bool, str]

CANONICAL_REQUEST = (
    "Which deals are rotting, who has not been contacted, "
    "and what is the next action on each?"
)

# The guard has to refuse these before making any tool call: one asks for a
# write this seat shouldn't make, the other for data this seat can't answer
# truthfully. CANONICAL_REQUEST must *not* be refused.
REFUSE_REQUESTS = (
    "Approve quotation QTN-0001 so it can go to the customer today.",
    "What is our revenue attainment against quota this quarter?",
)

REQUIRED_ENV = (
    "AGENTSWITCH_BASE_URL",
    "AGENTSWITCH_TOKEN",
    "AGENTSWITCH_INSTANCE",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)
# Values scrubbed from evidence before it's written or printed.
SECRET_ENV = ("AGENTSWITCH_TOKEN", "OPENAI_API_KEY", "AGENTSWITCH_BASE_URL", "OPENAI_BASE_URL")

WRITE_TOOLS = {"create", "update", "delete", "transition", "make_from", "bulk_update"}

# Per-task time limits in seconds. They add up to well under the evaluator's
# 20-minute timeout, which leaves room for the install and for the
# transport's own request timeout if a call gets cancelled mid-flight.
PREFLIGHT_TIMEOUT = 120
CANONICAL_TIMEOUT = 240
MODEL_TIMEOUT = 120
LOOP_TIMEOUT = 420
REFUSAL_TIMEOUT = 10
SKIPPED_TIMEOUT = 5

LOOP_MAX_STEPS = 12
EVIDENCE_LIMIT = 500
CONSOLE_EVIDENCE_LIMIT = 200

# Task ids and titles, since several are recorded from more than one place.
PREFLIGHT = ("mcp-preflight", "MCP catalogue and read access for the canonical task")
CANONICAL = ("canonical-read-only", "Canonical task answers in PROPOSE mode with zero writes")
REFUSAL = ("refusal-guard", "Out-of-policy requests are refused before any tool call; the canonical one is not")
MODEL = ("model-reachable", "Platform model answers a probe")
LOOP = ("agent-loop-read-only", "Agent loop on the platform model answers the canonical request without writing")


class Results:
    """The rows of results.json. Saved to disk after every new row."""

    def __init__(self, path: Path, instance: str):
        self.path = path
        self.instance = instance or "unknown"
        self.tasks = []

    def record(self, task_id: str, title: str, passed: bool, evidence: str):
        evidence = scrub(str(evidence))
        self.tasks.append({
            "id": f"{self.instance}:{task_id}",
            "title": title,
            "passed": bool(passed),
            "score": 1.0 if passed else 0.0,
            "evidence": evidence[:EVIDENCE_LIMIT],
        })
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {task_id}: {evidence[:CONSOLE_EVIDENCE_LIMIT]}", flush=True)
        self.save()

    def summary(self) -> str:
        passed = sum(t["passed"] for t in self.tasks)
        return f"{self.instance}: {passed}/{len(self.tasks)} tasks passed"

    def save(self):
        # Write to a temp file and swap it in, so the evaluator never reads a half-written file.
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"tasks": self.tasks, "summary": self.summary()}, indent=2))
        os.replace(tmp, self.path)


def scrub(text: str) -> str:
    """Replace any credential or endpoint URL in `text` with a placeholder."""
    for name in SECRET_ENV:
        value = os.environ.get(name, "")
        # Skip very short values so we don't mangle unrelated text.
        if len(value) >= 6:
            text = text.replace(value, f"<{name}>")
    return text


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timed out"
    return f"{type(exc).__name__}: {exc}"


def tool_calls(steps: list) -> list:
    return [s for s in steps if s.kind == "tool_call"]


def find_writes(steps: list) -> list:
    return [f"{s.tool}({s.entity})" for s in tool_calls(steps) if s.tool in WRITE_TOOLS]


async def run_task(results: Results, task: tuple, timeout: float,
                   body: Callable[[], Awaitable[TaskResult]]) -> bool:
    """Run one task and record it. Exceptions and timeouts become a failed row."""
    task_id, title = task
    started = time.time()
    try:
        passed, evidence = await asyncio.wait_for(body(), timeout=timeout)
    except Exception as e:  # noqa: BLE001 - every failure has to end up as a row
        passed, evidence = False, describe_error(e)
    elapsed = time.time() - started
    results.record(task_id, title, passed, f"{evidence} [{elapsed:.1f}s]")
    return passed


async def record_failure(results: Results, task: tuple, reason: str):
    """Record a task we couldn't run at all."""
    async def fail() -> TaskResult:
        return False, reason
    await run_task(results, task, SKIPPED_TIMEOUT, fail)


# --- checks -----------------------------------------------------------------

def check_env() -> TaskResult:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, f"all {len(REQUIRED_ENV)} evaluator variables present"


async def check_preflight(client: MCPClient, settings: Settings) -> TaskResult:
    report = await run_preflight(RecordingMCPClient(client, []), configured_surface=settings.mcp_tool_surface)
    evidence = report.summary()
    if not report.canonical_task_ready and report.notes:
        evidence += "; " + report.notes[0]
    return report.canonical_task_ready, evidence


async def check_canonical(client: MCPClient) -> TaskResult:
    steps = []
    request = AgentRequest(text=CANONICAL_REQUEST, mode=ExecutionMode.PROPOSE)
    result = await run_canonical_task(RecordingMCPClient(client, steps), request, run_id=uuid.uuid4().hex)

    writes = find_writes(steps)
    calls = len(tool_calls(steps))

    if isinstance(result, RefusalResult):
        return False, f"refused: {result.message}; tool calls={calls}, writes={len(writes)}"
    if not isinstance(result, CanonicalAnswer):
        return False, f"unexpected result type {type(result).__name__}"

    with_action = sum(1 for deal in result.deals if deal.next_action)
    evidence = (
        f"rot threshold {result.deal_rot_days}d; "
        f"rotting deals={len(result.deals)} (candidates {result.candidates_found}); "
        f"uncalled customers={len(result.uncalled)}; "
        f"hidden silence={len(result.hidden_silence)}; "
        f"deals with a proposed next action={with_action}; "
        f"tool calls={calls}; writes={len(writes)}"
    )
    if writes:
        return False, f"PROPOSE mode wrote: {', '.join(writes)}; {evidence}"
    return True, evidence


async def check_model(llm: LLM) -> TaskResult:
    reply = await llm("Reply with the single word OK.", "You are a connectivity probe.")
    reply = (reply or "").strip()
    return bool(reply), f"reply={reply[:60]!r}"


async def check_loop(client: MCPClient, llm: LLM, model_name: str) -> TaskResult:
    run_id = uuid.uuid4().hex
    task = {"id": "central-loop", "prompt": CANONICAL_REQUEST, "run_id": run_id}
    run = await run_loop(task, client, llm, model_name, max_steps=LOOP_MAX_STEPS,
                         write_policy=WritePolicy(run_id=run_id))

    writes = find_writes(run.steps)
    wrote_anything = writes or run.created_record_ids or run.updated_record_ids

    evidence = (
        f"ended={run.ended}; model calls={run.calls}; "
        f"tool steps={len(tool_calls(run.steps))}; writes={len(writes)}; "
        f"created={len(run.created_record_ids)}; updated={len(run.updated_record_ids)}; "
        f"unsupported figures={len(run.unsupported_figures)}"
    )
    if run.error:
        evidence += f"; error={run.error[:150]}"
    return run.ended == "done" and not wrote_anything, evidence


async def check_refusals() -> TaskResult:
    refused = [r for r in REFUSE_REQUESTS if isinstance(check_request(r), RefusalResult)]
    canonical_refused = check_request(CANONICAL_REQUEST) is not None
    ok = len(refused) == len(REFUSE_REQUESTS) and not canonical_refused
    return ok, (
        f"refused {len(refused)}/{len(REFUSE_REQUESTS)} out-of-policy requests before any tool call; "
        f"canonical request refused={canonical_refused}"
    )


def check_contract(results: Results) -> TaskResult:
    """Sanity-check the rows written so far, before adding this one."""
    rows = results.tasks
    problems = []

    # +1 for the contract row itself, which hasn't been recorded yet.
    if not 1 <= len(rows) + 1 <= 200:
        problems.append(f"{len(rows) + 1} rows")
    if len({r["id"] for r in rows}) != len(rows):
        problems.append("duplicate ids")
    for r in rows:
        if not isinstance(r["passed"], bool) or not r["title"] or not r["evidence"]:
            problems.append(f"bad row {r['id']}")

    if problems:
        return False, "; ".join(problems)
    return True, f"{len(rows)} earlier rows: unique ids, boolean passed, evidence present"


# --- runner -----------------------------------------------------------------

async def run_all(results: Results, dry_run: bool):
    if not dry_run:
        env_ok, env_evidence = check_env()
        results.record("env-contract", "Evaluator-supplied environment variables are present",
                       env_ok, env_evidence)

    settings = Settings.from_central_env()

    client: Optional[MCPClient] = None
    client_error = ""
    try:
        client = StubMCPClient() if dry_run else RealMCPClient(settings)
    except Exception as e:  # noqa: BLE001
        client_error = describe_error(e)

    if client is None:
        reason = f"MCP client not created: {client_error}"
        await record_failure(results, PREFLIGHT, reason)
        await record_failure(results, CANONICAL, reason)
    else:
        await run_task(results, PREFLIGHT, PREFLIGHT_TIMEOUT, lambda: check_preflight(client, settings))
        await run_task(results, CANONICAL, CANONICAL_TIMEOUT, lambda: check_canonical(client))

    await run_task(results, REFUSAL, REFUSAL_TIMEOUT, check_refusals)

    if dry_run:
        return

    try:
        llm = build_llm(settings.model_name)
    except Exception as e:  # noqa: BLE001
        reason = f"model backend not created: {describe_error(e)}"
        await record_failure(results, MODEL, reason)
        await record_failure(results, LOOP, reason)
        return

    model_ok = await run_task(results, MODEL, MODEL_TIMEOUT, lambda: check_model(llm))

    if client is None:
        await record_failure(results, LOOP, "not run: MCP client unavailable")
    elif not model_ok:
        await record_failure(results, LOOP, "not run: model probe failed")
    else:
        await run_task(results, LOOP, LOOP_TIMEOUT, lambda: check_loop(client, llm, settings.model_name))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Central evaluator entry point.")
    parser.add_argument("--dry-run", action="store_true",
                        help="use the stub MCP client and skip the model tasks (local use only)")
    parser.add_argument("--results", default="results.json",
                        help="where to write results (default: results.json in the working directory)")
    args = parser.parse_args(argv)

    instance = "dry-run" if args.dry_run else os.environ.get("AGENTSWITCH_INSTANCE", "")
    results = Results(Path(args.results), instance)

    try:
        asyncio.run(run_all(results, dry_run=args.dry_run))
    except BaseException as e:  # noqa: BLE001 - results.json has to exist whatever happens
        results.record("runner-crash", "Runner finished without an unhandled error", False, describe_error(e))
    finally:
        passed, evidence = check_contract(results)
        results.record("result-contract", "results.json rows are well-formed", passed, evidence)

    print(results.summary(), flush=True)
    # Always exit 0: the evaluator scores results.json, not the exit code.
    return 0


if __name__ == "__main__":
    sys.exit(main())
