"""Loop budget wrap-up, and retrying a proxy 401 that's really an upstream failure."""
import asyncio
import io
import json
import urllib.error

import pytest

from pipeline_agent import central_runner
from pipeline_agent import llm as llm_module
from pipeline_agent.harness import loop
from pipeline_agent.harness.loop import run_loop
from pipeline_agent.llm import LLMConfigError, _chat_completions_backend
from pipeline_agent.mcp.stub_client import StubMCPClient

DONE = json.dumps({"action": "done", "claimed_success": True, "answer": {}})
NOTE = json.dumps({"action": "note", "note": "I need one more turn."})
URL = "https://model.test/chat/completions"
OK_BODY = b'{"choices":[{"message":{"content":"ok"}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}'


class ScriptedLLM:
    """Canned replies; also records each (prompt, system) it was called with."""

    def __init__(self, replies, usages):
        self._replies = iter(replies)
        self._usages = iter(usages)
        self.last_usage = None
        self.prompts = []

    async def __call__(self, prompt, system):
        self.prompts.append((prompt, system))
        self.last_usage = next(self._usages)
        return next(self._replies)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def usage(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def run_with(llm, **kwargs):
    task = {"id": "loop-wrap-up", "prompt": "Summarise the pipeline."}
    return asyncio.run(run_loop(task, StubMCPClient(), llm, "test", derived_tools=False, **kwargs))


def http_401(body):
    return urllib.error.HTTPError(URL, 401, "Unauthorized", {}, io.BytesIO(body))


@pytest.fixture
def sleeps(monkeypatch):
    """Record retry pauses instead of actually sleeping."""
    pauses = []

    async def fake_sleep(delay):
        pauses.append(delay)

    monkeypatch.setattr(loop.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(central_runner.asyncio, "sleep", fake_sleep)
    return pauses


@pytest.fixture
def fake_http(monkeypatch):
    """Point the chat backend at `handler` and run it inline instead of in a thread."""
    async def inline(func, *args):
        return func(*args)

    def install(handler):
        monkeypatch.setattr(llm_module.urllib.request, "urlopen", handler)
        monkeypatch.setattr(llm_module.asyncio, "to_thread", inline)
    return install


# --- budget wrap-up -----------------------------------------------------------

def test_predicted_overrun_triggers_one_final_no_tools_call():
    # First call costs 40, leaving 60. Another call like it could cost 80, so
    # the second call has to be the wrap-up even though we're still under 100.
    llm = ScriptedLLM([NOTE, DONE], [usage(10, 30), usage(20, 50)])

    result = run_with(llm, max_steps=3, token_budget=100)

    assert result.ended == "done"
    assert result.claimed_success
    assert result.calls == 2
    assert result.input_tokens + result.output_tokens == 110

    wrap_up = "\n".join(llm.prompts[1]).lower()
    assert "final answer" in wrap_up
    assert "tool" in wrap_up
    assert "do not" in wrap_up


def test_wrap_up_only_gets_one_chance():
    llm = ScriptedLLM([NOTE, NOTE, DONE], [usage(10, 30), usage(1, 1), usage(1, 1)])

    result = run_with(llm, max_steps=3, token_budget=100)

    assert result.ended == "token_budget"
    assert not result.claimed_success
    # The DONE reply is never reached.
    assert result.calls == 2


# --- retrying proxy 401s ------------------------------------------------------

@pytest.mark.parametrize("body", [
    b'{"upstream_error":"provider authentication failed"}',
    b"model provider answered with an authentication error",
], ids=["upstream-error-field", "provider-answered-text"])
def test_proxy_401_from_upstream_is_retried_once(fake_http, sleeps, body):
    attempts = []

    def urlopen(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise http_401(body)
        return FakeResponse(OK_BODY)

    fake_http(urlopen)
    backend = _chat_completions_backend("test-model", URL, "key")

    assert asyncio.run(loop._call_llm(backend, "prompt", "system")) == "ok"
    assert len(attempts) == 2
    assert sleeps == [loop.LLM_RETRY_DELAY]


def test_plain_401_is_a_real_auth_error_and_not_retried(fake_http):
    attempts = []

    def urlopen(*args, **kwargs):
        attempts.append(1)
        raise http_401(b"bad API key")

    fake_http(urlopen)
    backend = _chat_completions_backend("test-model", URL, "bad-key")

    with pytest.raises(LLMConfigError, match="HTTP 401"):
        asyncio.run(loop._call_llm(backend, "prompt", "system"))
    assert len(attempts) == 1


# --- central runner -----------------------------------------------------------

def test_model_probe_retries_once_on_a_temporary_failure(sleeps):
    attempts = []

    async def flaky_model(prompt, system):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("proxy upstream unavailable")
        return "OK"

    # `sleeps` patches both loop and central_runner, so this works whichever
    # one does the retry.
    assert asyncio.run(central_runner.check_model(flaky_model)) == (True, "reply='OK'")
    assert len(attempts) == 2
    assert sleeps == [loop.LLM_RETRY_DELAY]


def test_loop_token_budget_is_140k():
    assert central_runner.LOOP_TOKEN_BUDGET == 140_000