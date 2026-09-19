"""Phase 4: the harness's run_local equivalent.

    PYTHONPATH=src python3 -m pipeline_agent.runner --mode dry-run
    PYTHONPATH=src python3 -m pipeline_agent.runner --mode live --exec-mode create-next-actions

--mode dry-run needs no credentials: it runs the exact same workflow code
against StubMCPClient, which is how Phase 0's exit condition ("a harmless MCP
read succeeds through the adapted harness, with a persisted run folder...")
is checkable before Gate G1 and the MCP transport contract are resolved.
--mode live requires AGENTSWITCH_MCP_URL/AGENTSWITCH_MCP_TOKEN and currently
fails on its first call - RealMCPClient.call_tool is intentionally
unimplemented until that contract is confirmed (see mcp/real_client.py).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import time
import uuid
from pathlib import Path

from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer, ExecutionMode, RefusalResult
from pipeline_agent.agent.workflow import run_canonical_task
from pipeline_agent.config import Settings
from pipeline_agent.harness.artifacts import run_artifact
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.harness.recording import RecordingMCPClient
from pipeline_agent.mcp.real_client import RealMCPClient
from pipeline_agent.mcp.stub_client import StubMCPClient

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


async def main_async(args: argparse.Namespace) -> int:
    _load_dotenv()
    settings = Settings.from_env()
    client = StubMCPClient() if args.mode == "dry-run" else RealMCPClient(settings)

    run_id = uuid.uuid4().hex
    task = {"id": f"canonical__{args.mode}", "prompt": args.request, "run_id": run_id}
    request = AgentRequest(text=args.request, mode=ExecutionMode(args.exec_mode))

    with run_artifact(settings.output_dir, task["id"], task=task, settings=settings) as writer:
        run = TaskRun(task_id=task["id"], model=settings.model_name or f"none ({args.mode})")
        recorder = RecordingMCPClient(client, run.steps)
        t0 = time.time()
        try:
            result = await run_canonical_task(recorder, request, run_id=run_id)
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            run.ended = "error"
            run.seconds = time.time() - t0
            writer.finalize(run)
            raise

        run.seconds = time.time() - t0
        run.final_answer = dataclasses.asdict(result)
        if isinstance(result, CanonicalAnswer):
            run.claimed_success = True
            run.ended = "done"
            run.created_record_ids = [
                d.next_action["id"] for d in result.deals
                if d.action_status.value == "created" and d.next_action and "id" in d.next_action
            ]
            print(result.summary)
        elif isinstance(result, RefusalResult):
            run.claimed_success = False
            run.ended = "refused"
            print(f"REFUSED: {result.message}")
        writer.finalize(run)
        print(f"run artifact: {writer.run_dir}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Seat 07 Pipeline agent once.")
    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run",
                         help="dry-run uses the in-memory stub client (no credentials needed); "
                              "live uses RealMCPClient (requires AGENTSWITCH_MCP_URL/_TOKEN, "
                              "and currently fails - transport not yet implemented).")
    parser.add_argument("--exec-mode", choices=["propose", "create-next-actions"], default="propose")
    parser.add_argument("--request", default=CANONICAL_REQUEST)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
