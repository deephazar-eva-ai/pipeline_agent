"""Model backends for `harness/loop.py`.

`run_loop` needs exactly one thing: an async callable of `(prompt, system) ->
str`. Which model provides it is a runner-level choice, not a harness
dependency, so the backend is selected by configuration:

    MODEL_NAME=anthropic:claude-opus-5   Anthropic Messages API
    MODEL_NAME=ollama:llama3.1           a local Ollama daemon
    MODEL_NAME=openrouter:qwen/qwen3.8-27b  any OpenRouter model (OpenAI protocol)
    MODEL_NAME=openai:<model>            OPENAI_BASE_URL + OPENAI_API_KEY (central evaluator)

Both halves are optional in the usual sense - the harness's other two tasks
(`canonical`, `preflight`) never import this module, so the repo still runs
end-to-end with no model configured and no third-party package installed.

The Anthropic SDK is imported lazily, inside the backend that needs it, for
that reason: `pip install pipeline-agent[anthropic]` is only required if you
actually select that backend. Ollama is plain HTTP against a local daemon and
needs nothing installed here.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Awaitable, Callable

LLMCallable = Callable[[str, str], Awaitable[str]]

ANTHROPIC = "anthropic"
OLLAMA = "ollama"
OPENROUTER = "openrouter"
OPENAI = "openai"
BACKENDS = (ANTHROPIC, OLLAMA, OPENROUTER, OPENAI)

# OpenRouter speaks the OpenAI chat-completions protocol. Added 2026-10-06 so
# the complex-jobs runs could continue on another model when Anthropic credit
# ran out; the model id after the colon is OpenRouter's (e.g. qwen/qwen3.8-27b).
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT = 300.0

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT = 120.0

# Generous, because it is a ceiling and not a spend: the loop's replies are one
# small JSON object, but a thinking model needs headroom to get there.
MAX_TOKENS = 16000


class LLMConfigError(Exception):
    """MODEL_NAME is missing, malformed, or names a backend we don't have."""


def parse_model_name(value: str) -> tuple[str, str]:
    """`"anthropic:claude-opus-5"` -> `("anthropic", "claude-opus-5")`."""
    raw = (value or "").strip()
    if not raw:
        raise LLMConfigError(
            "MODEL_NAME is not set. The loop task needs a model backend, e.g. "
            "MODEL_NAME=anthropic:claude-opus-5 or MODEL_NAME=ollama:llama3.1")
    backend, _, model = raw.partition(":")
    backend = backend.strip().lower()
    model = model.strip()
    if backend not in BACKENDS:
        raise LLMConfigError(
            f"unknown model backend {backend!r} in MODEL_NAME={raw!r}; "
            f"expected one of {BACKENDS}")
    if not model:
        raise LLMConfigError(
            f"MODEL_NAME={raw!r} names a backend but no model, e.g. "
            f"{backend}:<model-id>")
    return backend, model


def _anthropic_backend(model: str) -> LLMCallable:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMConfigError(
            "the anthropic backend needs the Anthropic SDK: "
            "pip install 'anthropic' (or install this package with the "
            "[anthropic] extra)") from exc

    # Zero-arg: the SDK resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an
    # `ant auth login` profile. An unset API key does not mean no credential,
    # so this deliberately does not pre-check for one.
    # The SDK retries 429/5xx/overloaded on its own (2 by default); long loop
    # jobs get a little more headroom (plan item P7).
    client = anthropic.AsyncAnthropic(max_retries=4)

    async def call(prompt: str, system: str) -> str:
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        # Read by run_loop after each call, so a run records what it cost
        # (plan item P6). An attribute rather than a second return value keeps
        # the `(prompt, system) -> str` contract every backend shares.
        usage = getattr(response, "usage", None)
        call.last_usage = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }
        # Thinking blocks are present but carry no answer; the loop parses the
        # text. Joining only text blocks keeps that contract.
        return "".join(b.text for b in response.content if b.type == "text")

    return call


def _ollama_backend(model: str) -> LLMCallable:
    host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")
    url = f"{host}/api/chat"

    def post_blocking(payload: dict) -> str:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise LLMConfigError(f"HTTP {exc.code} from {url}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise LLMConfigError(
                f"cannot reach the Ollama daemon at {url}: {exc.reason}. Is "
                f"`ollama serve` running, and is {model!r} pulled?") from exc
        return (body.get("message") or {}).get("content", "")

    async def call(prompt: str, system: str) -> str:
        payload = {
            "model": model,
            "stream": False,
            # The loop's protocol is one JSON object; asking the daemon to
            # constrain output to JSON saves a round of unusable replies.
            "format": "json",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        }
        return await asyncio.to_thread(post_blocking, payload)

    return call


def _openrouter_backend(model: str) -> LLMCallable:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPEN_ROUTER_API_KEY")
    if not key:
        raise LLMConfigError("the openrouter backend needs OPENROUTER_API_KEY "
                             "(or OPEN_ROUTER_API_KEY) in the environment or .env")
    return _chat_completions_backend(model, os.environ.get("OPENROUTER_URL", OPENROUTER_URL), key)


def _openai_backend(model: str) -> LLMCallable:
    """The central evaluator's platform model. OPENAI_BASE_URL is used the way
    the OpenAI SDK uses it: the base already carries any /v1, and
    /chat/completions is appended."""
    base = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not base or not key:
        raise LLMConfigError("the openai backend needs OPENAI_BASE_URL and OPENAI_API_KEY "
                             "in the environment")
    return _chat_completions_backend(model, base + "/chat/completions", key)


def _chat_completions_backend(model: str, url: str, key: str) -> LLMCallable:
    def post_blocking(payload: dict) -> dict:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            # Cloudflare in front of some OpenAI-compatible hosts rejects the
            # default Python user agent (error 1010).
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                     "User-Agent": "pipeline-agent/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code == 429 or exc.code >= 500:
                # Named so run_loop's transient-error retry picks it up.
                raise ConnectionError(f"HTTP {exc.code} from {url}: {detail[:300]}") from exc
            raise LLMConfigError(f"HTTP {exc.code} from {url}: {detail[:300]}") from exc

    async def call(prompt: str, system: str) -> str:
        payload = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        }
        try:
            body = await asyncio.to_thread(post_blocking, payload)
        except LLMConfigError as exc:
            # MAX_TOKENS is sized for thinking models; a server whose model has
            # a smaller output limit rejects it with a 400. Retry once and let
            # the server apply its own default.
            text = str(exc).lower()
            if not (text.startswith("http 400") and ("max_tokens" in text or "token" in text)):
                raise
            payload.pop("max_tokens")
            body = await asyncio.to_thread(post_blocking, payload)
        usage = body.get("usage") or {}
        call.last_usage = {"input_tokens": usage.get("prompt_tokens") or 0,
                           "output_tokens": usage.get("completion_tokens") or 0}
        choices = body.get("choices") or [{}]
        return (choices[0].get("message") or {}).get("content") or ""

    return call


def build_llm(model_name: str) -> LLMCallable:
    """Resolve MODEL_NAME to the callable `run_loop` expects."""
    backend, model = parse_model_name(model_name)
    if backend == ANTHROPIC:
        return _anthropic_backend(model)
    if backend == OPENROUTER:
        return _openrouter_backend(model)
    if backend == OPENAI:
        return _openai_backend(model)
    return _ollama_backend(model)
