"""The general-purpose model/tool-call loop - "your own loop" the harness
grading criterion asks for.

This is used for the free-form / refusal-style tasks (Phase 3) where the
model itself decides which MCP tool to call next. The canonical three-part
task (Phase 2) does NOT run through here for its safety-critical parts -
rot detection, contact classification, and the create-Activity idempotency
guard are deterministic Python in agent/workflow.py, on purpose, per the
plan: "let the model explain and resolve limited judgement, but do not leave
required safety checks to free-form prompting." This loop is the harness
primitive underneath that workflow's optional model calls, and the vehicle
for the mandatory refusal task.

Protocol the model must reply in - one JSON object, nothing else:
    {"action": "call_tool", "tool": "list", "arguments": {"entity": "Deal", ...}}
    {"action": "done", "claimed_success": true, "answer": {...}}
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Awaitable, Callable

from pipeline_agent.harness.base import Step, TaskRun
from pipeline_agent.mcp.client import MCPClient, MCPToolError, PermissionDeniedError

SYSTEM = (
    "You are the Pipeline agent for Seat 07 (CRM) at Suryodaya Precision Works. "
    "Reply with ONE json object and nothing else.\n"
    'To call a tool:  {"action":"call_tool","tool":"list","arguments":{"entity":"Deal"}}\n'
    'To finish:       {"action":"done","claimed_success":true|false,"answer":{...}}\n'
    "If a tool call is refused for a policy reason, do NOT retry it - state in your "
    "final answer exactly what is missing and why, and stop. Never fabricate a result "
    "for data you could not read, and never claim a write happened that did not."
)

# How many times the SAME (tool, entity) may be denied before the harness
# force-stops the run. The platform convention is "refuse once, do not retry" -
# this is a budget/safety backstop, not a substitute for the model behaving
# correctly; a real retry attempt is still recorded in the trace before the
# guard trips.
MAX_REPEAT_DENIALS = 1

LLMCallable = Callable[[str, str], Awaitable[str]]


async def run_loop(task: dict, client: MCPClient, llm: LLMCallable, model: str,
                    *, max_steps: int = 12) -> TaskRun:
    run = TaskRun(task_id=task["id"], model=model)
    t0 = time.time()
    history: list[str] = []
    denials: dict[tuple[str, str], int] = {}

    for _ in range(max_steps):
        prompt = json.dumps({"request": task["prompt"], "history": history[-10:]})
        run.calls += 1
        try:
            raw = await llm(prompt, SYSTEM)
        except Exception as e:
            run.error = f"llm: {type(e).__name__}: {e}"
            run.ended = "error"
            break

        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            run.unusable_replies += 1
            history.append("your reply was not json - reply with exactly one json object")
            continue
        try:
            act = json.loads(m.group(0))
        except json.JSONDecodeError:
            run.unusable_replies += 1
            history.append("your json did not parse")
            continue

        action = act.get("action")

        if action == "call_tool":
            tool = act.get("tool", "")
            arguments = act.get("arguments", {}) or {}
            entity = arguments.get("entity", "")
            key = (tool, entity)

            if denials.get(key, 0) >= MAX_REPEAT_DENIALS:
                run.steps.append(Step("refused", tool, entity, False,
                                       "harness stopped a repeat call after a policy refusal"))
                run.error = f"stopped: repeated denied call to {tool}({entity}) after refusal"
                run.ended = "refused"
                break

            try:
                result = await client.call_tool(tool, arguments)
                run.steps.append(Step("tool_call", tool, entity, True, arguments=arguments))
                history.append(f"{tool}({entity}) -> {json.dumps(result, default=str)[:1500]}")
            except PermissionDeniedError as e:
                denials[key] = denials.get(key, 0) + 1
                run.steps.append(Step("refused", tool, entity, False, str(e), arguments=arguments))
                # Say only what the platform said. `e.domain` is this repo's own
                # ENTITY_DOMAIN guess and is populated whether or not the server
                # named a domain, so asserting it unconditionally fed the model a
                # fabricated exclusion - the Gate G1 error, which was fixed in
                # PermissionDeniedError and build_refusal on 2026-09-22 and missed
                # here. On this platform the server names no domain, so this branch
                # was telling the model 'sales' is excluded while the agent was
                # reading Deal and Activity from it.
                reason = (f"Domain '{e.domain}' is outside this seat's policy."
                          if e.cites_domain else
                          "The platform named no domain - the tool is simply not in "
                          "this seat's tools/list.")
                history.append(f"REFUSED {tool}({entity}): {e.message} {reason} "
                                f"Do not retry this call.")
            except MCPToolError as e:
                run.steps.append(Step("error", tool, entity, False, str(e), arguments=arguments))
                history.append(f"ERROR {tool}({entity}): {e.message}")

        elif action == "done":
            run.claimed_success = bool(act.get("claimed_success"))
            run.final_answer = act.get("answer")
            run.steps.append(Step("answer", ok=True, detail=json.dumps(act.get("answer"), default=str)[:500]))
            run.ended = "done"
            break

        else:
            run.unusable_replies += 1
            history.append(f"unknown action {action!r}")

    run.ended = run.ended or "max_steps"
    run.seconds = time.time() - t0
    return run
