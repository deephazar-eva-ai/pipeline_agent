"""What every run produces, and all a scorer is allowed to see.

Adapted from the week18/S18Code harness's Step/TaskRun split (same idea:
a harness records what happened; whether it actually passed is the grader's
job, computed independently, never from the agent's own claim). The fields
differ because the domain differs - file edits and pytest there, MCP tool
calls and direct-database verification here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    """One MCP tool call, or one refusal, or the final answer."""

    kind: str                 # tool_call | refused | answer | error
    tool: str = ""
    entity: str = ""
    ok: bool = True
    detail: str = ""
    arguments: dict = field(default_factory=dict)


@dataclass
class TaskRun:
    """What the harness persists before anything is scored.

    Deliberately absent: whether the task actually passed. That's the
    verifier's job, computed by reading the database directly (Phase 5) -
    never from `claimed_success` or from the agent's own prose.
    """

    task_id: str
    harness: str = "pipeline_agent"
    model: str = ""
    steps: list[Step] = field(default_factory=list)
    claimed_success: bool = False
    seconds: float = 0.0
    calls: int = 0
    unusable_replies: int = 0
    error: str = ""

    # done | refused | error | max_steps - see loop.py. Kept distinct from
    # `error` for the same reason S18Code kept it distinct: a run that refused
    # correctly and a run that crashed must never share one column.
    ended: str = ""

    created_record_ids: list[str] = field(default_factory=list)
    final_answer: Any = None
