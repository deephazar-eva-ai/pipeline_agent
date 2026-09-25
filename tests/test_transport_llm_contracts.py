"""Tests for MCP transport, client, artifact, and LLM configuration behaviour."""

import asyncio
import io
import json
import urllib.error

import pytest

from pipeline_agent.config import Settings
from pipeline_agent.harness.artifacts import run_artifact
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.llm import LLMConfigError, parse_model_name
from pipeline_agent.mcp.client import MCPToolError
from pipeline_agent.mcp.real_client import RealMCPClient
from pipeline_agent.mcp.tool_names import (
    ENTITY_SCOPED,
    SurfaceError,
    to_wire,
)
from pipeline_agent.mcp.transport import (
    JsonRpcError,
    JsonRpcTransport,
    ToolResultError,
    TransportError,
    _maybe_json,
    unwrap_tool_result,
)


class Replies(JsonRpcTransport):
    """Transport test double that returns predefined responses."""

    def __init__(self, replies):
        super().__init__(
            "https://example.test",
            "token",
        )
        self.replies = iter(replies)
        self.payloads = []

    async def _post(self, payload):
        self.payloads.append(payload)
        return next(self.replies)


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            "anthropic:model",
            ("anthropic", "model"),
        ),
        (
            " Ollama : llama ",
            ("ollama", "llama"),
        ),
    ],
)
def test_model_name_is_parsed_from_provider_and_model(
    value,
    expected,
):
    assert parse_model_name(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "anthropic",
        "x:model",
        "ollama:",
    ],
)
def test_invalid_model_name_is_rejected(value):
    with pytest.raises(LLMConfigError):
        parse_model_name(value)


@pytest.mark.parametrize(
    "tool_name",
    [
        "not-a-tool",
        "report",
        "transition",
    ],
)
def test_unknown_entity_tool_is_rejected_before_wire_translation(
    tool_name,
):
    with pytest.raises(SurfaceError):
        to_wire(
            ENTITY_SCOPED,
            tool_name,
            {"entity": "Deal"},
        )


def test_entity_list_tool_is_translated_to_wire_name():
    result = to_wire(
        ENTITY_SCOPED,
        "list",
        {"entity": "Deal"},
    )

    assert result == (
        "Deal.list",
        {},
    )


def test_json_rpc_error_is_raised_for_error_response():
    client = Replies(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32600,
                    "message": "bad",
                },
            }
        ]
    )

    with pytest.raises(
        JsonRpcError,
        match="bad",
    ):
        asyncio.run(
            client._request("tools/list")
        )


def test_json_rpc_result_is_returned_for_success_response():
    client = Replies(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [],
                },
            }
        ]
    )

    result = asyncio.run(
        client._request("tools/list")
    )

    assert result == {
        "tools": [],
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            "",
            None,
        ),
        (
            "no-json",
            "no-json",
        ),
        (
            '{"ok": true}',
            {"ok": True},
        ),
    ],
)
def test_response_body_is_decoded_when_it_contains_json(
    value,
    expected,
):
    assert _maybe_json(value) == expected


def test_tool_result_content_is_unwrapped():
    result = unwrap_tool_result(
        {
            "content": [
                {
                    "type": "text",
                    "text": '{"id": "x"}',
                }
            ]
        }
    )

    assert result == {
        "id": "x",
    }


def test_tool_error_response_raises_tool_result_error():
    response = {
        "content": [
            {
                "type": "text",
                "text": "denied",
            }
        ],
        "isError": True,
    }

    with pytest.raises(ToolResultError):
        unwrap_tool_result(response)


@pytest.mark.parametrize(
    "status",
    [
        401,
        403,
        500,
    ],
)
def test_http_error_is_converted_to_transport_error(
    status,
    monkeypatch,
):
    def fail_request(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.test/api/mcp",
            status,
            "x",
            {},
            io.BytesIO(b"bad"),
        )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        fail_request,
    )

    transport = JsonRpcTransport(
        "https://example.test",
        "token",
    )

    with pytest.raises(
        TransportError
    ) as exc:
        transport._post_blocking(
            {"jsonrpc": "2.0"}
        )

    assert exc.value.status == status


def test_invalid_tool_surface_is_rejected_before_network_access():
    settings = Settings(
        mcp_url="https://example.test",
        mcp_token="token",
        mcp_tool_surface="nope",
    )

    with pytest.raises(
        SurfaceError,
        match="MCP_TOOL_SURFACE",
    ):
        RealMCPClient(settings)


def test_http_authentication_failure_is_reported_as_a_tool_error(
    monkeypatch,
):
    def fail_request(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.test/api/mcp",
            401,
            "unauthorized",
            {},
            io.BytesIO(b"bad token"),
        )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        fail_request,
    )

    client = RealMCPClient(
        Settings(
            mcp_url="https://example.test",
            mcp_token="bad",
        )
    )

    with pytest.raises(
        MCPToolError
    ) as exc:
        asyncio.run(
            client.call_tool(
                "list",
                {"entity": "Deal"},
            )
        )

    assert "HTTP 401" in exc.value.message
    assert "policy" not in exc.value.message.lower()


def test_completed_artifact_is_marked_completed(tmp_path):
    settings = Settings(
        output_dir=str(tmp_path),
        mcp_token="secret",
    )

    with run_artifact(
        str(tmp_path),
        "ok",
        task={"id": "ok"},
        settings=settings,
    ) as writer:
        writer.finalize(
            TaskRun(
                task_id="ok",
                ended="done",
            )
        )

        run_dir = writer.run_dir

    status = json.loads(
        (run_dir / "status.json").read_text()
    )

    assert status["status"] == "completed"


def test_artifact_records_an_exception(tmp_path):
    settings = Settings(
        output_dir=str(tmp_path),
        mcp_token="secret",
    )

    with pytest.raises(RuntimeError):
        with run_artifact(
            str(tmp_path),
            "bad",
            task={"id": "bad"},
            settings=settings,
        ):
            raise RuntimeError("boom")

    error_files = [
        path / "error.txt"
        for path in tmp_path.iterdir()
        if (path / "error.txt").is_file()
    ]

    assert error_files
