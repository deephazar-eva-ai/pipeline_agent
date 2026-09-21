"""Model backends for `harness/loop.py`.

`run_loop` needs exactly one thing: an async callable of `(prompt, system) ->
str`. Which model provides it is a runner-level choice, not a harness
dependency, so the backend is selected by configuration:

    MODEL_NAME=anthropic:claude-opus-5   Anthropic Messages API
    MODEL_NAME=ollama:llama3.1           a local Ollama daemon

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
BACKENDS = (ANTHROPIC, OLLAMA)

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
    client = anthropic.AsyncAnthropic()

    async def call(prompt: str, system: str) -> str:
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
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


def build_llm(model_name: str) -> LLMCallable:
    """Resolve MODEL_NAME to the callable `run_loop` expects."""
    backend, model = parse_model_name(model_name)
    if backend == ANTHROPIC:
        return _anthropic_backend(model)
    return _ollama_backend(model)
