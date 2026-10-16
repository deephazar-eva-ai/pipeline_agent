"""Tests for the loop's token budget."""
import asyncio
import json

from pipeline_agent.harness.loop import run_loop
from pipeline_agent.mcp.stub_client import StubMCPClient

DONE = json.dumps({"action": "done", "claimed_success": True, "answer": {}})


class ScriptedLLM:
    """Returns canned replies and sets last_usage the way a real backend would."""

    def __init__(self, replies, usages):
        self._replies = iter(replies)
        self._usages = iter(usages)
        self.last_usage = None

    async def __call__(self, prompt, system):
        self.last_usage = next(self._usages)
        return next(self._replies)


def usage(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def run_with(llm, **kwargs):
    task = {"id": "token-budget", "prompt": "Summarise the pipeline."}
    return asyncio.run(run_loop(task, StubMCPClient(), llm, "test", derived_tools=False, **kwargs))


def test_stops_once_reported_usage_hits_the_budget():
    llm = ScriptedLLM([DONE], [usage(7, 6)])

    result = run_with(llm, max_steps=2, token_budget=10)

    assert result.ended == "token_budget"
    assert result.calls == 1
    assert (result.input_tokens, result.output_tokens) == (7, 6)
    # Even though the model said done, going over budget means no success.
    assert not result.claimed_success
    assert "13 >= 10" in result.error


def test_estimates_usage_when_backend_reports_none():
    reply = "x" * 400
    llm = ScriptedLLM([reply], [None])

    result = run_with(llm, max_steps=2, token_budget=1)

    assert result.ended == "token_budget"
    assert result.input_tokens > 0
    # Roughly 4 characters per token.
    assert result.output_tokens == len(reply) // 4


def test_no_budget_means_no_limit():
    note = json.dumps({"action": "note", "note": "continue"})
    llm = ScriptedLLM([note, DONE], [usage(50_000, 50_000), usage(50_000, 50_000)])

    result = run_with(llm, max_steps=2)

    assert result.ended == "done"
    assert result.calls == 2
    assert result.claimed_success
    assert result.input_tokens + result.output_tokens == 200_000