"""From a query to a standing habit: digest, memory, to-dos, escalations.

crm_gap_fillup.md B7 (gaps G4, G5, G6, G16). The canonical answer is
recomputed from scratch on every run, so on its own it repeats yesterday's
list word for word. This module turns one run into a digest that remembers:

* `AgentMemory` (category `relationship`, per party, expiring) records the
  first day a deal was flagged, so the next digest says "day 4" instead of
  announcing the same deal as news;
* `AgentTodo` holds decisions only a person may make - assign an owner,
  re-quote, close a dead deal, decide an unmapped stage - and the questions
  other seats must answer (email, payments, tickets, contracts: all 403 here);
* `AgentEscalation` hands a stale high-value deal to a person with the
  platform's own record, on an `AgentSession` opened for the digest.

Safety rules, all enforced here rather than trusted to a caller:

* Propose-only unless the caller passes `write=True` (the runner does so
  only for `--exec-mode create-next-actions`, with consent recorded).
* Every record carries `HABIT_MARKER` and a key; before writing, the agent
  lists what it wrote earlier and skips any key already open. If that
  dedupe read fails, the category is NOT written - no read, no write.
* At most `HABIT_MAX_WRITES` records per run; the rest are listed as
  "not written (cap)". The agent cannot delete what it creates.
* No memory update is ever sent for a deal still flagged: the first-flagged
  date is the point, and rewriting it would reset the day count.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from pipeline_agent.agent.contract import ActionStatus, CanonicalAnswer
from pipeline_agent.mcp.client import MCPClient, MCPToolError

HABIT_MARKER = "pipeline_agent_habit"
HABIT_MAX_WRITES = 25
MEMORY_TTL_DAYS = 45
MEMORY_IMPORTANCE = 0.7

# A rotting deal is escalated to a person when the score calls it critical,
# or when it is large (value >= the open-pipeline p90) and its close date
# passed more than this many days ago. Template - confirm with the seat owner.
ESCALATE_PAST_CLOSE_DAYS = 14

# Questions this seat cannot answer from its own catalogue (all 403), and the
# seat that owns the data. From the capability plan §4.
CROSS_SEAT = (
    ("xseat:inbox", "Inbox seat 10 / Calendar seat 19",
     "latest email sent/received and latest meeting date", "uncalled"),
    ("xseat:ar", "AR seat 2 (via EA seat 28)",
     "overdue invoices or late payments", "rotting"),
    ("xseat:helpdesk", "Helpdesk seat 15", "open support tickets", "rotting"),
    ("xseat:contract", "Contract seat 17", "contract renewal dates", "rotting"),
)
CROSS_SEAT_MAX_PARTIES = 20

_KEY = re.compile(re.escape(HABIT_MARKER) + r" key=([^\s)]+)")
_FIRST = re.compile(r"first_flagged=(\d{4}-\d{2}-\d{2})")


def _key_line(key: str, **extra: str) -> str:
    return f"({HABIT_MARKER} key={key}" + "".join(f" {k}={v}" for k, v in extra.items()) + ")"


def key_of(record: dict, *fields: str) -> str | None:
    for name in fields or ("content", "detail", "reason", "description"):
        m = _KEY.search(str(record.get(name) or ""))
        if m:
            return m.group(1)
    return None


@dataclass
class HabitDigest:
    today: str
    summary: str
    still_flagged: list[dict] = field(default_factory=list)
    newly_flagged: list[dict] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)
    cross_seat_requests: list[dict] = field(default_factory=list)
    written: list[dict] = field(default_factory=list)
    not_written: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mode: str = "propose"


async def _existing(client: MCPClient, entity: str, notes: list[str]) -> list[dict] | None:
    """Every record of `entity` this agent wrote, or None when it cannot tell."""
    rows: list[dict] = []
    offset = 0
    try:
        while True:
            resp = await client.list_(entity, search=HABIT_MARKER, limit=200, offset=offset)
            records = (resp.get("records") or []) if isinstance(resp, dict) else []
            rows.extend(r for r in records if HABIT_MARKER in str(r))
            offset += len(records)
            total = resp.get("total") if isinstance(resp, dict) else None
            if not records or (isinstance(total, int) and offset >= total) or len(records) < 200:
                return rows
    except (MCPToolError, NotImplementedError) as e:
        notes.append(f"{entity} could not be read ({e}) - nothing of that kind is written "
                     f"this run, because duplicates could not be ruled out")
        return None


def plan_digest(answer: CanonicalAnswer, rows: list[dict], *, today: str,
                memories: list[dict] | None, todos: list[dict] | None,
                escalations: list[dict] | None, value_p90: float | None) -> HabitDigest:
    """Pure: what the digest says and which records it would write."""
    digest = HabitDigest(today=today, summary=answer.summary)
    by_id = {r["deal_id"]: r for r in rows}
    rotting = {d.deal_id: d for d in answer.deals}

    # --- memory: first-flagged dates -------------------------------------
    flagged_on: dict[str, dict] = {}
    for m in memories or []:
        key = key_of(m, "content")
        if key and key.startswith("rot:") and m.get("is_active", True) not in (0, False):
            flagged_on[key[4:]] = m
    for deal_id, result in rotting.items():
        row = by_id.get(deal_id, {})
        base = {"deal_id": deal_id, "title": row.get("display_title") or row.get("title") or deal_id,
                "party_id": row.get("party_id"), "value": row.get("value"),
                "rot_days": result.rot_evidence.get("independent_rot_days"),
                "rot_band": (result.rot_evidence.get("rot_score") or {}).get("rot_band")}
        mem = flagged_on.get(deal_id)
        first = _FIRST.search(str(mem.get("content") or "")) if mem else None
        if first:
            days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(first.group(1))).days
            digest.still_flagged.append({**base, "first_flagged": first.group(1),
                                         "day": days + 1})
        else:
            digest.newly_flagged.append({**base, "write": {
                "entity": "AgentMemory", "data": {
                    "category": "relationship", "source": "system",
                    "importance": MEMORY_IMPORTANCE, "is_active": True,
                    **({"party_id": base["party_id"]} if base["party_id"] else {}),
                    "expires_at": (dt.date.fromisoformat(today)
                                   + dt.timedelta(days=MEMORY_TTL_DAYS)).isoformat(),
                    "content": (f"Deal '{base['title']}' flagged as rotting on {today}: "
                                f"{base['rot_days']} days since genuine contact. "
                                + _key_line(f"rot:{deal_id}", first_flagged=today))}}})
    for deal_id, mem in flagged_on.items():
        if deal_id not in rotting:
            digest.resolved.append({"deal_id": deal_id, "memory_id": mem.get("id"),
                                    "note": "no longer rotting - memory left to expire, not "
                                            "rewritten"})
    digest.still_flagged.sort(key=lambda r: -r["day"])

    # --- to-dos: decisions for a person ------------------------------------
    open_todo_keys = {key_of(t, "detail") for t in todos or []
                      if t.get("status") not in ("done", "cancelled")}

    def todo(key: str, title: str, detail: str, priority: str = "normal") -> None:
        item = {"key": key, "title": title, "detail": detail,
                "already_open": key in open_todo_keys}
        if not item["already_open"]:
            item["write"] = {"entity": "AgentTodo", "data": {
                "title": title[:200], "priority": priority, "status": "open",
                "written_by": "pipeline_agent", "due_date": today,
                "detail": f"{detail}\n\n{_key_line(key)}"}}
        digest.todos.append(item)

    for d in answer.deals:
        title = by_id.get(d.deal_id, {}).get("display_title") or d.deal_id
        if d.action_status is ActionStatus.NEEDS_HUMAN_REVIEW:
            todo(f"stage:{d.deal_id}", f"Decide stage and next action: {title}",
                 "; ".join(d.reasons))
        elif d.action_status is ActionStatus.OVERDUE:
            todo(f"overdue:{d.deal_id}", f"Action or reschedule the overdue task: {title}",
                 "; ".join(d.reasons))
    for issue in answer.data_issues:
        if issue["deal_id"] not in rotting:
            continue
        if issue["code"] in ("no_owner", "unknown_owner"):
            todo(f"owner:{issue['deal_id']}", f"Assign an owner: {issue['deal']}",
                 f"{issue['detail']}. This seat cannot read the rep directory, so it does not "
                 f"pick one.")
        elif issue["code"] == "expired_quote":
            todo(f"requote:{issue['deal_id']}", f"Re-quote or extend: {issue['deal']}",
                 issue["detail"])
        elif issue["code"] == "close_lost_candidate":
            todo(f"closelost:{issue['deal_id']}", f"Decide close-lost: {issue['deal']}",
                 issue["detail"], priority="low")

    # --- cross-seat questions ------------------------------------------------
    uncalled_parties = [u.party_id for u in answer.uncalled][:CROSS_SEAT_MAX_PARTIES]
    rotting_parties = sorted({by_id.get(i, {}).get("party_id") for i in rotting} - {None})
    for key, seat, question, scope in CROSS_SEAT:
        parties = uncalled_parties if scope == "uncalled" else \
            rotting_parties[:CROSS_SEAT_MAX_PARTIES]
        if not parties:
            continue
        request = {"key": key, "seat": seat, "question": question, "party_ids": parties,
                   "status": "requested" if key in open_todo_keys else "to request"}
        digest.cross_seat_requests.append(request)
        todo(key, f"Ask {seat}: {question}",
             f"This seat cannot read that data (403). Please ask {seat} for {question} for "
             f"these customer party ids: {', '.join(parties)}")

    # --- escalations -----------------------------------------------------------
    open_esc_keys = {key_of(e, "reason") for e in escalations or []
                     if not e.get("resolved_at") and e.get("status") not in ("resolved",
                                                                             "closed")}
    slipped = {i["deal_id"]: i.get("days", 0) for i in answer.data_issues
               if i["code"] == "slipped_close_date"}
    for deal_id, result in rotting.items():
        row = by_id.get(deal_id, {})
        band = (result.rot_evidence.get("rot_score") or {}).get("rot_band")
        value = float(row.get("value") or 0)
        big_and_late = (value_p90 and value >= value_p90
                        and slipped.get(deal_id, 0) > ESCALATE_PAST_CLOSE_DAYS)
        if band != "critical" and not big_and_late:
            continue
        key = f"esc:{deal_id}"
        why = (f"rot band critical: {(result.rot_evidence.get('rot_score') or {}).get('rot_comment')}"
               if band == "critical" else
               f"value {value:,.0f} is in the top 10% of open pipeline and the close date "
               f"passed {slipped[deal_id]} days ago")
        title = row.get("display_title") or row.get("title")
        item = {"key": key, "deal_id": deal_id, "title": title, "why": why,
                "already_open": key in open_esc_keys}
        if not item["already_open"]:
            item["write"] = {"entity": "AgentEscalation", "data": {
                "subject": f"Stale deal needs an owner's decision: {title}"[:200],
                "reason_code": "other", "channel": "api",
                **({"party_id": row["party_id"]} if row.get("party_id") else {}),
                "reason": f"{why}. {result.rot_evidence.get('independent_rot_days')} days "
                          f"since genuine contact. {_key_line(key)}"}}
        digest.escalations.append(item)
    return digest


async def run_digest(client: MCPClient, answer: CanonicalAnswer, rows: list[dict], *,
                     today: str, value_p90: float | None, write: bool,
                     run_id: str) -> HabitDigest:
    notes: list[str] = []
    memories = await _existing(client, "AgentMemory", notes)
    todos = await _existing(client, "AgentTodo", notes)
    escalations = await _existing(client, "AgentEscalation", notes)
    digest = plan_digest(answer, rows, today=today, memories=memories, todos=todos,
                         escalations=escalations, value_p90=value_p90)
    digest.notes.extend(notes)
    digest.mode = "write" if write else "propose"
    if not write:
        digest.notes.append("propose mode - nothing written")
        return digest

    readable = {"AgentMemory": memories is not None, "AgentTodo": todos is not None,
                "AgentEscalation": escalations is not None}
    pending = [i for i in digest.newly_flagged + digest.todos + digest.escalations
               if "write" in i]
    session_id: str | None = None
    for item in pending:
        entity, data = item["write"]["entity"], dict(item["write"]["data"])
        if not readable[entity]:
            digest.not_written.append({"entity": entity, "reason": "dedupe read failed"})
            continue
        if len(digest.written) >= HABIT_MAX_WRITES:
            digest.not_written.append({"entity": entity, "reason": f"cap {HABIT_MAX_WRITES}",
                                       "key": item.get("key") or item.get("deal_id")})
            continue
        try:
            if entity == "AgentEscalation":
                if session_id is None:
                    session = await client.create("AgentSession", {
                        "title": f"pipeline_agent digest {today}", "channel": "api",
                        "actor_kind": "system", "actor_label": "pipeline_agent",
                        "metadata": f"{HABIT_MARKER} run={run_id}"})
                    session_id = (session or {}).get("id")
                    digest.written.append({"entity": "AgentSession", "id": session_id})
                data["session_id"] = session_id
            record = await client.create(entity, data)
            digest.written.append({"entity": entity, "id": (record or {}).get("id"),
                                   "key": item.get("key") or item.get("deal_id")})
        except MCPToolError as e:
            digest.not_written.append({"entity": entity, "reason": e.message,
                                       "key": item.get("key") or item.get("deal_id")})
    return digest


# --- the schedule -------------------------------------------------------------

TASK_NAME = "Pipeline digest (seat 07)"
DIGEST_CRON = "0 9 * * 1-5"
DIGEST_PROMPT = ("Which deals are rotting, who has not been contacted, and what is the next "
                 "action on each? Report the daily digest: deals still flagged (with the day "
                 "count), newly flagged deals, and decisions waiting for a person.")


def agent_task_payload(persona_id: str | None) -> dict:
    """AgentTask.create payload for the daily digest.

    Caveat, stated wherever this is used: an AgentTask runs the PLATFORM's
    agent runtime with this prompt and the persona's tools. It does not run
    this repository's Python, so it does not get the independent rot check,
    the laundering defence or the dedupe rules. The dependable habit is the
    local cron line from `local_cron_line`; the AgentTask is the platform's
    visible record of it."""
    payload = {"name": TASK_NAME, "prompt": DIGEST_PROMPT, "schedule_type": "cron",
               "cron_expression": DIGEST_CRON, "channel": "internal", "max_retries": 1,
               "timeout_seconds": 600,
               "description": f"Daily pipeline digest for seat 07. ({HABIT_MARKER} "
                              f"key=task:digest)"}
    if persona_id:
        payload["persona_id"] = persona_id
    return payload


def local_cron_line(repo_dir: str, tz_name: str) -> str:
    return (f"CRON_TZ={tz_name}\n{DIGEST_CRON} cd {repo_dir} && PYTHONPATH=src "
            f".venv/bin/python -m pipeline_agent.runner --task digest --mode live "
            f">> runs/digest.log 2>&1")
