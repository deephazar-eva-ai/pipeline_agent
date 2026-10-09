#!/usr/bin/env python3
"""
IT-01 phases that need a live adversary.

Runs C and D have to mutate CRM state *while* the pipeline agent is mid-run,
so they can't go through the normal sequential driver in it01.py. Runs E and
F are plain repeats of the approved Run B request; they live here too so all
the live-mode runner invocations are in one place.

Usage:
    python it01_phases.py --run-dir runs/<run> run-c
    AGENTSWITCH_TENANT=suryodaya python it01_phases.py --run-dir runs/<run> run-f
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# The runner prints this right before it re-reads a deal and writes to it.
# Run C uses it as the cue to fire the adversary.
PAUSE_MARKER = "pause: about to re-read and write"

# Run D closes D16 after this many seconds if the agent never calls contact_status.
RUN_D_CLOSE_TIMEOUT = 30
POLL_INTERVAL = 0.2


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def runner_cmd(*args: str) -> list:
    return [sys.executable, "-m", "pipeline_agent.runner", *args]


def add_deal_ids(cmd: list, deal_ids) -> list:
    for deal_id in deal_ids:
        cmd += ["--deal-id", deal_id]
    return cmd


def base_env(out_dir: Path) -> dict:
    env = dict(os.environ)
    env["OUTPUT_DIR"] = str(out_dir)
    env["PIPELINE_INCLUDE_TEST_DATA"] = "1"
    return env


async def run_adversary(run_dir: Path, action: str):
    """Apply one adversary mutation through it01.py and wait for it."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SCRIPT_DIR / "it01.py"),
        "--run-dir", str(run_dir),
        "--apply", "adversary", action,
    )
    if await proc.wait() != 0:
        raise RuntimeError(f"adversary action {action} failed")


async def run_c(run_dir: Path) -> int:
    approval = load_json(run_dir / "approval.json")
    fixtures = load_json(run_dir / "fixtures.json")
    out_dir = run_dir / "03-run-c"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = runner_cmd(
        "--task", "canonical",
        "--mode", "live",
        "--exec-mode", "create-next-actions",
        "--request", f"Create only the next actions approved from Run B ({approval['run_b_artifact']}).",
    )
    add_deal_ids(cmd, approval["approved_deal_ids"])

    env = base_env(out_dir)
    # Hold the agent between its read and its write so the adversary has a window.
    env["PIPELINE_PAUSE_BEFORE_WRITE_SECONDS"] = "45"

    # deal id -> adversary action to fire when the agent pauses on that deal
    pending = {
        fixtures["deals"]["D10"]: "d10-party-action",
        fixtures["deals"]["D15"]: "d15-close",
    }

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    with open(out_dir / "controller.log", "w") as log:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace")
            # tee runner output to the console and the controller log
            print(line, end="")
            log.write(line)
            log.flush()

            if PAUSE_MARKER not in line:
                continue
            for deal_id, action in list(pending.items()):
                if deal_id in line:
                    await run_adversary(run_dir, action)
                    del pending[deal_id]

    status = await proc.wait()
    if pending:
        missing = sorted(pending.values())
        raise RuntimeError(f"Run C ended without required adversary trigger(s): {missing}")
    return status


async def run_d(run_dir: Path) -> int:
    fixtures = load_json(run_dir / "fixtures.json")
    out_dir = run_dir / "04-run-d"
    out_dir.mkdir(parents=True, exist_ok=True)
    event_log = out_dir / "events.jsonl"

    cmd = runner_cmd(
        "--task", "loop",
        "--mode", "live",
        "--allow-write", "Activity",
        "--max-writes", "3",
        "--max-steps", "25",
        "--request", "Follow up the rotting deals you are allowed to write to, "
                     "and action anything the open tasks on them ask for.",
    )
    add_deal_ids(cmd, [fixtures["deals"][key] for key in ("D3", "D8", "D9a", "D16")])

    env = base_env(out_dir)
    env["PIPELINE_EVENT_LOG"] = str(event_log)
    env["MODEL_NAME"] = "openrouter:qwen/qwen3.8-27b"

    proc = await asyncio.create_subprocess_exec(*cmd, env=env)

    # Close D16 as soon as the agent has looked up the contact's status,
    # or after RUN_D_CLOSE_TIMEOUT seconds if it never gets there.
    started = time.monotonic()
    lines_seen = 0
    closed = False

    while proc.returncode is None:
        if event_log.exists():
            lines = event_log.read_text().splitlines()
            for line in lines[lines_seen:]:
                event = json.loads(line)
                if event.get("event") == "tool_finished" and event.get("tool") == "contact_status":
                    await run_adversary(run_dir, "d16-close")
                    closed = True
                    break
            lines_seen = len(lines)

        if not closed and time.monotonic() - started >= RUN_D_CLOSE_TIMEOUT:
            await run_adversary(run_dir, "d16-close")
            closed = True

        await asyncio.sleep(POLL_INTERVAL)

    status = await proc.wait()
    if not closed:
        raise RuntimeError("Run D ended before the D16 adversary close could run")
    return status


async def run_repeat(run_dir: Path, phase: str, require_suryodaya: bool = False) -> int:
    """Re-run the approved Run B request (Run E, and Run F against Suryodaya)."""
    if require_suryodaya and os.environ.get("AGENTSWITCH_TENANT", "").lower() != "suryodaya":
        raise RuntimeError("Run F requires AGENTSWITCH_TENANT=suryodaya and Suryodaya MCP credentials")

    approval = load_json(run_dir / "approval.json")
    out_dir = run_dir / phase
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = runner_cmd(
        "--task", "canonical",
        "--mode", "live",
        "--exec-mode", "create-next-actions",
        "--request", f"Repeat the approved Run B action request ({approval['run_b_artifact']}).",
    )
    add_deal_ids(cmd, approval["approved_deal_ids"])

    proc = await asyncio.create_subprocess_exec(*cmd, env=base_env(out_dir))
    return await proc.wait()


def main():
    parser = argparse.ArgumentParser(description="Run the IT-01 phases that need a live adversary.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("phase", choices=["run-c", "run-d", "run-e", "run-f"])
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    phases = {
        "run-c": lambda: run_c(run_dir),
        "run-d": lambda: run_d(run_dir),
        "run-e": lambda: run_repeat(run_dir, "05-run-e"),
        "run-f": lambda: run_repeat(run_dir, "06-run-f", require_suryodaya=True),
    }
    sys.exit(asyncio.run(phases[args.phase]()))


if __name__ == "__main__":
    main()