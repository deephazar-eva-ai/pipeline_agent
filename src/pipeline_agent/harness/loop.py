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

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pipeline_agent.agent import contact_status as derived
from pipeline_agent.agent.pipeline_checks import load_company_clock
from pipeline_agent.agent.workflow import PROVENANCE_MARKER
from pipeline_agent.harness.base import Step, TaskRun
from pipeline_agent.mcp.client import MCPClient, MCPToolError, PermissionDeniedError

# The tool vocabulary below is what the 2026-10-05 live run showed the model
# was missing: with only `list` as an example it guessed `read`,
# `get_preferences` and `filters={"stage__not_in": ...}`, all of which fail,
# and never found CRMPreferences. Argument shapes are from the live
# inputSchema of Deal.list / Activity.list / CRMPreferences.list (2026-10-05).
SYSTEM = (
    "You are the Pipeline agent for Seat 07 (CRM) on the AgentSwitch platform. "
    "Reply with ONE json object and nothing else.\n"
    'To call a tool:  {"action":"call_tool","tool":"list","arguments":{"entity":"Deal"}}\n'
    'To finish:       {"action":"done","claimed_success":true|false,"answer":{...}}\n'
    "\n"
    "Tools (always pass the entity as arguments.entity):\n"
    '- list: {"entity":E, "limit":1-1000 (default 20), "offset":n, "sort_by":field, '
    '"sort_order":"asc"|"desc", "search":text, <field>:<value>}. Field filters are exact '
    "equality only (e.g. \"stage\":\"negotiation\", \"party_id\":id); there are no "
    "__in/__not/range operators, and computed fields starting with _ cannot be filtered.\n"
    '- get: {"entity":E, "id":id}.  - get_schema: {"entity":E}.\n'
    "There is no read, count or report tool. A list result starts with total, limit, "
    "offset and shown; use total to count, and page with offset until you have read "
    "all total records when the question covers the whole book. Records are compacted: "
    "null and empty fields are left out.\n"
    "Entities this seat reads: CRMPreferences (one record; deal_rot_days is the rot "
    "threshold), Deal (stage, value, currency, party_id, contact_id, owner, "
    "expected_close_date, _rot_days, _rot_level), Activity (type, subject, done, "
    "due_date, deal_id, party_id, outcome), Party (customers and contacts), Pipeline, "
    "Lead, Quotation, SalesOrder. Open deals are those whose stage is not closed_won "
    "or closed_lost.\n"
    "\n"
    "Working memory: any reply may also carry \"note\":\"text\" (or be "
    '{"action":"note","note":"text"}). Only the last 10 tool results stay in history, '
    "so note running totals, ids and what is left to read as you go. Your notes and a "
    "one-line index of every call you have made (calls_so_far) stay visible for the "
    "whole run.\n"
    "\n"
    "If a tool call is refused for a policy reason, do NOT retry it - state in your "
    "final answer exactly what is missing and why, and stop. Never fabricate a result "
    "for data you could not read, and never claim a write happened that did not."
)

# A tool result is shown to the model compacted and capped at this many
# characters. The old cap was a blind 1500-character slice: one page of
# Deal.list is ~41k characters, so the model saw about one deal and lost the
# total/limit/offset that tell it more pages exist (live run 2026-10-05).
RESULT_CHAR_BUDGET = 30000
MAX_FIELD_CHARS = 160

# Per-record fields that cost characters and carry nothing the model can act on.
_NOISE_FIELDS = frozenset({
    "company_id", "_company_id_display", "created_by", "updated_by",
    "_permissions", "_can_create", "_readonly_fields", "_redacted_fields",
    "_base_currency",
})

# How many times the SAME (tool, entity) may be denied before the harness
# force-stops the run. The platform convention is "refuse once, do not retry" -
# this is a budget/safety backstop, not a substitute for the model behaving
# correctly; a real retry attempt is still recorded in the trace before the
# guard trips.
MAX_REPEAT_DENIALS = 1

# Model notes kept for the whole run (plan item P2). Bounded, oldest dropped
# first, so a chatty model cannot crowd the request out of its own prompt.
NOTES_CHAR_BUDGET = 6000

# One retry, after this pause, when the model call fails with a transient
# error (plan item P7). The Anthropic SDK already retries 429/5xx itself; this
# covers what is left (a timeout after its retries, a dropped connection) so
# a long job is not lost to one blip.
LLM_RETRY_DELAY = 10.0
_TRANSIENT_LLM_ERRORS = frozenset({
    "APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError",
    "OverloadedError", "ServiceUnavailableError", "TimeoutError", "ConnectionError",
})

# Tools that change the book. The loop runs read-only unless a WritePolicy
# allows a write (plan item P1): "read-only" in a prompt is a request to the
# model, not a property of the harness, and the seat's catalogue does serve
# create/update/delete.
WRITE_TOOLS = frozenset({"create", "update", "delete", "transition", "bulk_update",
                         "make_from"})
# Never allowed from the loop, whatever the policy says: there is no undo on a
# shared book, and closing/transitioning deals belongs to a person.
NEVER_FROM_LOOP = frozenset({"delete", "transition", "bulk_update", "make_from"})

LLMCallable = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class WritePolicy:
    """What the loop may write in this run. The default allows nothing.

    entities  - entities the model may create or update (e.g. {"Activity"})
    deal_ids  - when set, a created record must link one of these deals
    record_ids - the only records the model may update
    max_writes - total successful writes allowed in the run
    """

    entities: frozenset[str] = frozenset()
    deal_ids: frozenset[str] = frozenset()
    record_ids: frozenset[str] = frozenset()
    max_writes: int = 0
    run_id: str = ""

    def describe(self) -> str:
        if not self.entities or self.max_writes <= 0:
            return ("Writes: this run is READ-ONLY. The harness blocks every create, update, "
                    "delete or transition call.")
        scope = (f" linked to deal(s) {', '.join(sorted(self.deal_ids))}"
                 if self.deal_ids else "")
        upd = (f" You may update only these record ids: {', '.join(sorted(self.record_ids))}."
               if self.record_ids else " Updates are blocked.")
        return (f"Writes: you may create at most {self.max_writes} record(s) in "
                f"{', '.join(sorted(self.entities))}{scope}.{upd} Deletes and transitions "
                "are always blocked. Everything else is blocked by the harness.")


def _write_target_deal(arguments: dict) -> Any:
    data = arguments.get("data") if isinstance(arguments.get("data"), dict) else {}
    return arguments.get("deal_id") or data.get("deal_id")


def write_block_reason(policy: WritePolicy, tool: str, arguments: dict,
                       writes_done: int) -> str | None:
    """None when the policy allows this write, else why the harness blocks it."""
    entity = arguments.get("entity", "")
    if tool in NEVER_FROM_LOOP:
        return f"the loop never calls {tool}; it is irreversible on a shared book"
    if not policy.entities or policy.max_writes <= 0:
        return "this run is read-only (no --allow-write given)"
    if entity not in policy.entities:
        return f"writes to {entity or 'an unnamed entity'} are not allowed in this run"
    if writes_done >= policy.max_writes:
        return f"the run's write budget of {policy.max_writes} is used up"
    if tool == "update":
        if str(arguments.get("id")) not in policy.record_ids:
            return f"record {arguments.get('id')} is not in this run's update allow-list"
    elif tool == "create" and policy.deal_ids:
        if str(_write_target_deal(arguments)) not in policy.deal_ids:
            return (f"a created {entity} must link one of the allowed deals, not "
                    f"{_write_target_deal(arguments)}")
    return None


def _stamp_provenance(arguments: dict, run_id: str) -> dict:
    """Every Activity the loop creates carries the marker the rot analysis uses
    to exclude the agent's own rows. Added by the harness, not left to the model."""
    if arguments.get("entity") != "Activity":
        return arguments
    out = dict(arguments)
    if isinstance(out.get("data"), dict):
        out["data"] = dict(out["data"])
        target = out["data"]
    else:
        target = out
    desc = str(target.get("description") or "")
    if PROVENANCE_MARKER not in desc:
        target["description"] = (desc + "\n\n" if desc else "") + \
            f"({PROVENANCE_MARKER} run={run_id}; via loop)"
    return out


def _write_field(arguments: dict, name: str) -> Any:
    data = arguments.get("data") if isinstance(arguments.get("data"), dict) else {}
    return arguments.get(name) if name in arguments else data.get(name)


async def open_activity_duplicate(client: MCPClient, arguments: dict) -> str | None:
    """Why creating this open Activity would duplicate an existing next action,
    or None. The same rule as the workflow's re-read before a write: an open
    Activity on the deal, or an open one on the deal's customer with no deal
    set. Run by the harness immediately before the create, so it holds whatever
    the model checked (or did not) - V1-loop on 2026-10-06 passed only because
    the model happened to re-read by deal_id. A completed Activity (logging a
    contact that happened) is never a duplicate of a next action."""
    if arguments.get("entity") != "Activity" or _write_field(arguments, "done"):
        return None
    deal_id = _write_field(arguments, "deal_id")
    party_id = _write_field(arguments, "party_id")
    if deal_id:
        found = await client.list_("Activity", filters={"deal_id": deal_id, "done": False},
                                   limit=1)
        records = (found.get("records") or []) if isinstance(found, dict) else []
        if records:
            return (f"deal {deal_id} already has an open Activity "
                    f"'{records[0].get('subject')}' (id {records[0].get('id')}, due "
                    f"{records[0].get('due_date')}) - not creating a duplicate")
        if not party_id:
            deal = await client.get("Deal", deal_id)
            party_id = deal.get("party_id") if isinstance(deal, dict) else None
    if party_id:
        found = await client.list_("Activity", filters={"party_id": party_id, "done": False},
                                   limit=100)
        for activity in (found.get("records") or []) if isinstance(found, dict) else []:
            if not activity.get("deal_id"):
                return (f"the customer already has an open Activity "
                        f"'{activity.get('subject')}' (id {activity.get('id')}) not tied to "
                        "another deal - not creating a duplicate")
    return None


def _call_line(n: int, tool: str, arguments: dict, outcome: str) -> str:
    args = {k: v for k, v in arguments.items() if k != "entity"}
    shown = json.dumps(args, default=str)
    if len(shown) > 120:
        shown = shown[:120] + "…"
    return f"{n}. {tool}({arguments.get('entity', '')}) {shown} -> {outcome}"


def _outcome(result: Any) -> str:
    if isinstance(result, dict) and isinstance(result.get("records"), list):
        return (f"total={result.get('total')} offset={result.get('offset', 0)} "
                f"returned={len(result['records'])}")
    if isinstance(result, dict) and result.get("id"):
        return f"ok id={result.get('id')}"
    return "ok"


def _keep_notes(notes: list[str], note: Any) -> None:
    if not note:
        return
    notes.append(str(note)[:NOTES_CHAR_BUDGET])
    while sum(len(n) for n in notes) > NOTES_CHAR_BUDGET and len(notes) > 1:
        notes.pop(0)


async def _call_llm(llm: LLMCallable, prompt: str, system: str) -> str:
    try:
        return await llm(prompt, system)
    except Exception as e:
        if type(e).__name__ not in _TRANSIENT_LLM_ERRORS:
            raise
    await asyncio.sleep(LLM_RETRY_DELAY)
    return await llm(prompt, system)


def _compact_record(record: Any) -> Any:
    if not isinstance(record, dict):
        return record
    out = {}
    for key, value in record.items():
        if key in _NOISE_FIELDS or value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            value = value[:MAX_FIELD_CHARS] + "…"
        out[key] = value
    return out


def compact_result(result: Any, budget: int = RESULT_CHAR_BUDGET) -> str:
    """Render a tool result for the model's history.

    List results keep their paging fields up front and drop whole records
    that do not fit, saying how many were shown, so a page is never cut
    mid-record and the model always knows whether to page on.
    """
    records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(records, list):
        text = json.dumps(_compact_record(result), default=str)
        return text if len(text) <= budget else text[:budget] + "… [truncated]"

    head = {k: result[k] for k in ("total", "limit", "offset") if k in result}
    rendered = [json.dumps(_compact_record(r), default=str) for r in records]
    shown, used = 0, len(json.dumps(head)) + 200
    for text in rendered:
        if used + len(text) + 1 > budget:
            break
        used += len(text) + 1
        shown += 1
    head["shown"] = shown
    if shown < len(records):
        head["note"] = (f"only the first {shown} of {len(records)} records on this page fit; "
                        f"call again with offset={result.get('offset', 0) + shown} "
                        "or a smaller limit")
    return (json.dumps(head)[:-1] + ', "records": [' + ",".join(rendered[:shown]) + "]}")


async def run_loop(task: dict, client: MCPClient, llm: LLMCallable, model: str,
                    *, max_steps: int = 12, write_policy: WritePolicy | None = None,
                    derived_tools: bool = True) -> TaskRun:
    run = TaskRun(task_id=task["id"], model=model)
    t0 = time.time()
    policy = write_policy or WritePolicy(run_id=task.get("run_id", ""))
    system = SYSTEM
    if derived_tools:
        system = system.replace("There is no read, count or report tool.",
                                derived.DESCRIPTION + "There is no read, count or report tool.")
    system += "\n" + policy.describe()
    # The model has no clock. Without this, every date question was answered
    # against a "today" guessed from record timestamps - 2026-09-28/29 on a
    # 2026-10-06 run - so slipped deals and days-overdue were all short by a
    # week (job M3, 2026-10-06). Read once, from the company's own timezone.
    try:
        clock_notes: list[str] = []
        now, tz_name, _ = await load_company_clock(
            client, os.environ.get("PIPELINE_TIMEZONE"), clock_notes)
        system += (f"\nToday is {now.date().isoformat()} ({now.strftime('%A')}, {tz_name}). "
                   "Use this date for anything relative to today; do not infer it from records.")
        run.context_notes.append(f"harness read the company clock: today="
                                 f"{now.date().isoformat()} {tz_name}")
    except MCPToolError as e:
        run.context_notes.append(f"company clock unavailable, model not told today's date: {e}")
    history: list[str] = []
    calls: list[str] = []
    notes: list[str] = []
    denials: dict[tuple[str, str], int] = {}
    derived_cache: dict = {}
    writes_done = 0

    for _ in range(max_steps):
        prompt = json.dumps({"request": task["prompt"], "history": history[-10:],
                             "calls_so_far": calls, "notes": notes})
        run.calls += 1
        try:
            raw = await _call_llm(llm, prompt, system)
        except Exception as e:
            run.error = f"llm: {type(e).__name__}: {e}"
            run.ended = "error"
            break
        usage = getattr(llm, "last_usage", None)
        if isinstance(usage, dict):
            run.input_tokens += int(usage.get("input_tokens") or 0)
            run.output_tokens += int(usage.get("output_tokens") or 0)

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
        _keep_notes(notes, act.get("note"))

        if action == "note":
            continue

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

            if derived_tools and tool == derived.TOOL_NAME:
                try:
                    result = await derived.contact_status(
                        client, arguments, run_id=task.get("run_id", "loop"), cache=derived_cache)
                except MCPToolError as e:
                    run.steps.append(Step("error", tool, "", False, str(e), arguments=arguments))
                    history.append(f"ERROR {tool}: {e.message}")
                    calls.append(_call_line(len(calls) + 1, tool, arguments, "error"))
                    continue
                run.steps.append(Step("tool_call", tool, "", True, arguments=arguments))
                text = json.dumps(result, default=str)
                if len(text) > RESULT_CHAR_BUDGET:
                    text = text[:RESULT_CHAR_BUDGET] + "… [truncated]"
                history.append(f"{tool}() -> {text}")
                outcome = (f"total={result.get('total')} offset={result.get('offset')} "
                           f"returned={result.get('returned')}" if "total" in result else
                           f"summary open_deals={result.get('open_deals')} "
                           f"rotting={result.get('rotting_deals')}")
                calls.append(_call_line(len(calls) + 1, tool, arguments, outcome))
                continue

            if tool in WRITE_TOOLS:
                reason = write_block_reason(policy, tool, arguments, writes_done)
                if reason:
                    denials[key] = denials.get(key, 0) + 1
                    run.steps.append(Step("refused", tool, entity, False,
                                           f"harness write gate: {reason}", arguments=arguments))
                    history.append(f"BLOCKED {tool}({entity}) by the harness: {reason}. "
                                   f"Nothing was written. Do not retry this call.")
                    calls.append(_call_line(len(calls) + 1, tool, arguments, "BLOCKED"))
                    continue
                if tool == "create":
                    duplicate = await open_activity_duplicate(client, arguments)
                    if duplicate:
                        # Not a policy refusal - the model may go on to other deals -
                        # so it does not count toward the repeat-denial stop.
                        run.steps.append(Step("refused", tool, entity, False,
                                               f"harness duplicate guard: {duplicate}",
                                               arguments=arguments))
                        history.append(f"BLOCKED {tool}({entity}) by the harness: {duplicate}. "
                                       "Nothing was written.")
                        calls.append(_call_line(len(calls) + 1, tool, arguments,
                                                "BLOCKED duplicate"))
                        continue
                    arguments = _stamp_provenance(arguments, policy.run_id or
                                                  task.get("run_id", ""))

            try:
                result = await client.call_tool(tool, arguments)
                run.steps.append(Step("tool_call", tool, entity, True, arguments=arguments))
                history.append(f"{tool}({entity}) -> {compact_result(result)}")
                calls.append(_call_line(len(calls) + 1, tool, arguments, _outcome(result)))
                if tool in WRITE_TOOLS:
                    writes_done += 1
                    record_id = result.get("id") if isinstance(result, dict) else None
                    if tool == "create" and record_id:
                        run.created_record_ids.append(str(record_id))
                    elif tool == "update":
                        run.updated_record_ids.append(str(arguments.get("id")))
            except PermissionDeniedError as e:
                denials[key] = denials.get(key, 0) + 1
                run.steps.append(Step("refused", tool, entity, False, str(e), arguments=arguments))
                calls.append(_call_line(len(calls) + 1, tool, arguments, "REFUSED"))
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
                calls.append(_call_line(len(calls) + 1, tool, arguments, "error"))

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
