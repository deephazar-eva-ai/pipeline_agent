"""Time-budget wrap-up in the loop, and how the central runner reads the evaluator deadline."""
import asyncio
import json
import time
import types

import pytest

from pipeline_agent import central_runner
from pipeline_agent.harness import loop
from pipeline_agent.harness.loop import run_loop
from pipeline_agent.mcp.stub_client import StubMCPClient

DONE = json.dumps({"action": "done", "claimed_success": True, "answer": {}})
NOTE = json.dumps({"action": "note", "note": "I need one more turn."})
LIST_DEALS = json.dumps({"action": "call_tool", "tool": "list", "arguments": {"entity": "Deal", "limit": 1}})


class ScriptedLLM:
    """Canned replies; keeps each prompt it gets (parsed from JSON)."""

    def __init__(self, replies):
        self._replies = iter(replies)
        self.last_usage = {"input_tokens": 1, "output_tokens": 1}
        self.prompts = []

    async def __call__(self, prompt, system):
        self.prompts.append(json.loads(prompt))
        return next(self._replies)


def run_with(llm, **kwargs):
    task = {"id": "time-wrap-up", "prompt": "Summarise the pipeline."}
    return asyncio.run(run_loop(task, StubMCPClient(), llm, "test", derived_tools=False, **kwargs))


@pytest.fixture
def loop_clock(monkeypatch):
    """Feed loop.time.monotonic() from a fixed list. asyncio's own clock is left alone."""
    def set_ticks(*ticks):
        values = iter(ticks)
        fake_time = types.SimpleNamespace(monotonic=lambda: next(values), time=time.time)
        monkeypatch.setattr(loop, "time", fake_time)
    return set_ticks


# --- loop wrap-up -------------------------------------------------------------

def test_predicted_time_overrun_asks_for_final_answer(loop_clock):
    # First round took 40s, leaving 60s. Another round plus an answer would
    # take about 80s, so the second call has to be the no-tools wrap-up.
    loop_clock(0.0, 40.0)
    llm = ScriptedLLM([LIST_DEALS, DONE])

    result = run_with(llm, max_steps=3, deadline=100.0)

    assert result.ended == "done"
    assert result.claimed_success
    assert result.calls == 2
    assert len(result.steps) == 2  # the tool call, then the answer

    budget = llm.prompts[1]["budget"].lower()
    assert "time budget nearly used" in budget
    assert "do not call any tool" in budget
    assert any("60s left" in note and "40.0s" in note for note in result.context_notes)


def test_time_wrap_up_only_gets_one_chance(loop_clock):
    loop_clock(0.0, 40.0)
    llm = ScriptedLLM([NOTE, NOTE, DONE])

    result = run_with(llm, max_steps=3, deadline=100.0)

    assert result.ended == "time_budget"
    assert not result.claimed_success
    assert "time budget: no final answer" in result.error
    # The DONE reply is never reached.
    assert result.calls == 2


def test_no_deadline_means_no_wrap_up(loop_clock):
    loop_clock(0.0, 40.0)
    llm = ScriptedLLM([NOTE, DONE])

    result = run_with(llm, max_steps=2, deadline=None)

    assert result.ended == "done"
    assert result.calls == 2
    assert "budget" not in llm.prompts[1]


# --- evaluator deadline -------------------------------------------------------
# Wall clock is pinned at 1000 and the monotonic `now` at 123, so a wall-clock
# deadline of 1030 should come back as 123 + 30 = 153.

@pytest.mark.parametrize("env, expected", [
    ({"AGENTSWITCH_SECONDS_LEFT": "17.5"}, 140.5),
    ({"AGENTSWITCH_SECONDS_LEFT": "invalid", "AGENTSWITCH_DEADLINE": "1030"}, 153.0),
    ({"AGENTSWITCH_DEADLINE": "1970-01-01T00:17:10Z"}, 153.0),
    ({"AGENTSWITCH_DEADLINE": "1970-01-01T05:47:10+05:30"}, 153.0),
    ({"AGENTSWITCH_DEADLINE": "1970-01-01T00:17:10"}, 153.0),  # naive means UTC
], ids=["seconds-left", "bad-seconds-falls-back-to-epoch", "utc-z", "offset", "naive"])
def test_evaluator_deadline_formats(monkeypatch, env, expected):
    monkeypatch.setattr(central_runner.time, "time", lambda: 1_000.0)

    assert central_runner.evaluator_deadline(env, now=123.0) == expected


@pytest.mark.parametrize("env", [{}, {"AGENTSWITCH_DEADLINE": "not-a-date"}], ids=["unset", "garbage"])
def test_evaluator_deadline_is_none_without_a_usable_value(env):
    assert central_runner.evaluator_deadline(env, now=123.0) is None


def test_task_timeout_is_capped_by_deadline_but_not_below_the_floor(monkeypatch):
    monkeypatch.setattr(central_runner.time, "monotonic", lambda: 100.0)

    # 900s until the deadline, so the timeout comes in a little under that.
    monkeypatch.setattr(central_runner, "DEADLINE", 1_000.0)
    assert central_runner.task_timeout(900.0) == 880.0

    # Only 10s left, so the floor wins.
    monkeypatch.setattr(central_runner, "DEADLINE", 110.0)
    assert central_runner.task_timeout(900.0) == central_runner.MIN_TASK_TIMEOUT