"""Tests for the MCP transport and client, run artifacts, and model-name parsing.

Nothing here touches the network: HTTP calls are either replaced with a
scripted transport or have ``urlopen`` patched to fail.
"""

import asyncio
import io
import json
import urllib.error

import pytest

from pipeline_agent.config import Settings
from pipeline_agent.harness.artifacts import run_artifact
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.llm import LLMConfigError, parse_model_name
from pipeline_agent.mcp import transport
from pipeline_agent.mcp.client import MCPToolError
from pipeline_agent.mcp.real_client import RealMCPClient
from pipeline_agent.mcp.tool_names import ENTITY_SCOPED, SurfaceError, to_wire
from pipeline_agent.mcp.transport import (
    JsonRpcError,
    JsonRpcTransport,
    ToolResultError,
    TransportError,
    _maybe_json,
    unwrap_tool_result,
)


MCP_URL = "https://example.test"
MCP_ENDPOINT = f"{MCP_URL}/api/mcp"


class FakeTransport(JsonRpcTransport):
    """Transport that returns canned JSON-RPC responses instead of posting."""

    def __init__(self, responses):
        super().__init__(MCP_URL, "token")
        self.responses = iter(responses)
        self.payloads = []

    async def _post(self, payload):
        self.payloads.append(payload)
        return next(self.responses)


def http_error(status, reason="x", body=b"bad"):
    """Return a urlopen replacement that raises the given HTTP error."""

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(MCP_ENDPOINT, status, reason, {}, io.BytesIO(body))

    return fail


# --- model names -------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        pytest.param("anthropic:model", ("anthropic", "model"), id="plain"),
        pytest.param(" Ollama : llama ", ("ollama", "llama"), id="whitespace-and-case"),
    ],
)
def test_parse_model_name(value, expected):
    assert parse_model_name(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "anthropic", "x:model", "ollama:"],
    ids=["empty", "no-model", "unknown-provider", "empty-model"],
)
def test_parse_model_name_rejects_bad_values(value):
    with pytest.raises(LLMConfigError):
        parse_model_name(value)


# --- tool names --------------------------------------------------------------


def test_to_wire_translates_known_entity_scoped_tool():
    assert to_wire(ENTITY_SCOPED, "list", {"entity": "Deal"}) == ("Deal.list", {})


@pytest.mark.parametrize("tool", ["not-a-tool", "report", "transition"])
def test_to_wire_rejects_unsupported_entity_scoped_tool(tool):
    with pytest.raises(SurfaceError):
        to_wire(ENTITY_SCOPED, tool, {"entity": "Deal"})


# --- JSON-RPC transport ------------------------------------------------------


class TestJsonRpcTransport:
    def test_request_returns_result(self):
        client = FakeTransport([{"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}])

        assert asyncio.run(client._request("tools/list")) == {"tools": []}

    def test_request_raises_on_error_response(self):
        client = FakeTransport([
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}},
        ])

        with pytest.raises(JsonRpcError, match="bad"):
            asyncio.run(client._request("tools/list"))

    @pytest.mark.parametrize("status", [401, 403, 500])
    def test_http_error_keeps_status_code(self, status, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", http_error(status))

        with pytest.raises(TransportError) as exc:
            JsonRpcTransport(MCP_URL, "token")._post_blocking({"jsonrpc": "2.0"})

        assert exc.value.status == status


@pytest.mark.parametrize(
    "body, expected",
    [
        pytest.param("", None, id="empty"),
        pytest.param("no-json", "no-json", id="plain-text"),
        pytest.param('{"ok": true}', {"ok": True}, id="json"),
    ],
)
def test_maybe_json(body, expected):
    assert _maybe_json(body) == expected


class TestUnwrapToolResult:
    def test_parses_json_text_content(self):
        result = {"content": [{"type": "text", "text": '{"id": "x"}'}]}

        assert unwrap_tool_result(result) == {"id": "x"}

    def test_raises_when_result_is_error(self):
        result = {"content": [{"type": "text", "text": "denied"}], "isError": True}

        with pytest.raises(ToolResultError):
            unwrap_tool_result(result)


# --- RealMCPClient -----------------------------------------------------------


def test_client_rejects_unknown_surface_on_init():
    settings = Settings(mcp_url=MCP_URL, mcp_token="token", mcp_tool_surface="nope")

    with pytest.raises(SurfaceError, match="MCP_TOOL_SURFACE"):
        RealMCPClient(settings)


def test_client_reports_auth_failure_as_http_error_not_refusal(monkeypatch):
    async def run_inline(func, *args):
        return func(*args)

    # The client posts via asyncio.to_thread; run it inline so the patched
    # urlopen is hit in this thread and the test never touches the network.
    monkeypatch.setattr(transport.urllib.request, "urlopen",
                        http_error(401, "unauthorized", b"bad token"))
    monkeypatch.setattr(transport.asyncio, "to_thread", run_inline)
    client = RealMCPClient(Settings(mcp_url=MCP_URL, mcp_token="bad"))

    with pytest.raises(MCPToolError) as exc:
        asyncio.run(client.call_tool("list", {"entity": "Deal"}))

    assert "HTTP 401" in exc.value.message
    assert "policy" not in exc.value.message.lower()


# --- run artifacts -----------------------------------------------------------


class TestRunArtifact:
    @pytest.fixture
    def settings(self, tmp_path):
        return Settings(output_dir=str(tmp_path), mcp_token="secret")

    def test_finished_run_is_marked_completed(self, tmp_path, settings):
        with run_artifact(str(tmp_path), "ok", task={"id": "ok"}, settings=settings) as writer:
            writer.finalize(TaskRun(task_id="ok", ended="done"))
            run_dir = writer.run_dir

        status = json.loads((run_dir / "status.json").read_text())
        assert status["status"] == "completed"

    def test_exception_is_reraised_and_written_to_error_file(self, tmp_path, settings):
        with pytest.raises(RuntimeError):
            with run_artifact(str(tmp_path), "bad", task={"id": "bad"}, settings=settings):
                raise RuntimeError("boom")

        assert any((path / "error.txt").is_file() for path in tmp_path.iterdir())