"""Regression tests for deterministic catalogue and offline behaviour."""

import argparse
import asyncio
import json
import sys
import time

import pytest

from pipeline_agent.agent.contract import AgentRequest, ExecutionMode
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    RotCalibration,
    assess_rot,
    determine_next_action,
    run_canonical_task,
)
from pipeline_agent.config import Settings
from pipeline_agent.harness.artifacts import RunArtifactWriter
from pipeline_agent.harness.recording import RecordingMCPClient
from pipeline_agent.mcp.client import MCPClient, MCPToolError
from pipeline_agent.mcp.stub_client import StubMCPClient
from pipeline_agent.preflight import RECORDED_TOOL_COUNT, run_preflight
from pipeline_agent.runner import main

from .test_guard_regressions import NOW, Pages, _async_tools


class CapturingClient(MCPClient):
    """MCP client that records the requests made to it."""

    def __init__(self, result=None):
        self.calls = []

        if result is None:
            result = {
                "records": [],
                "total": 0,
            }

        self.result = result

    async def call_tool(self, name, arguments):
        self.calls.append(
            (
                name,
                dict(arguments),
            )
        )
        return self.result


@pytest.mark.parametrize(
    "limit",
    [
        0,
        1,
        999,
        1000,
        1001,
        -1,
        "abc",
    ],
)
def test_deal_list_passes_limit_to_mcp_without_changing_it(limit):
    client = CapturingClient()

    asyncio.run(
        client.list_(
            "Deal",
            limit=limit,
        )
    )

    assert client.calls == [
        (
            "list",
            {
                "entity": "Deal",
                "limit": limit,
                "offset": 0,
                "sort_order": "desc",
            },
        )
    ]


@pytest.mark.parametrize(
    "offset",
    [0, 4, 5, 6, -1],
)
def test_activity_list_passes_offset_to_mcp(offset):
    client = CapturingClient()

    asyncio.run(
        client.list_(
            "Activity",
            limit=5,
            offset=offset,
        )
    )

    assert client.calls[0][1]["offset"] == offset


@pytest.mark.parametrize(
    "activity, agent_authored, expected",
    [
        (
            {"created_at": "2026-09-18T00:00:00+00:00"},
            False,
            True,
        ),
        (
            {"created_at": "2026-09-18T00:00:00+00:00"},
            True,
            True,
        ),
        (
            {},
            False,
            True,
        ),
        (
            {"created_at": "not-a-date"},
            False,
            True,
        ),
        (
            {"created_at": "2026-09-25T00:00:00+00:00"},
            False,
            False,
        ),
    ],
)
def test_rot_signal_handles_activity_dates(
    activity,
    agent_authored,
    expected,
):
    deal = {
        "id": "d",
        "stage": "new",
        "_rot_level": "fresh",
        "updated_at": "2026-09-17T00:00:00+00:00",
    }

    index = ActivityIndex(
        last_touch_by_deal=(
            {"d": activity["created_at"]}
            if activity.get("created_at") and not agent_authored
            else {}
        ),
        agent_written_deals=(
            {"d"}
            if agent_authored
            else set()
        ),
    )

    candidate, evidence = assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
    )

    assert candidate is expected
    assert "independently_flags" in evidence


@pytest.mark.parametrize(
    "tool_count, has_drift",
    [
        (RECORDED_TOOL_COUNT, False),
        (RECORDED_TOOL_COUNT + 1, True),
    ],
)
def test_preflight_detects_catalogue_drift(tool_count, has_drift):
    client = Pages()

    client.list_tools = lambda: _async_tools(
        tool_count
    )

    report = asyncio.run(
        run_preflight(
            client,
            configured_surface="entity_scoped",
        )
    )

    assert bool(
        report.changes_from_recorded_state
    ) is has_drift


def test_deal_without_an_open_action_gets_a_task_proposal():
    deal = {
        "id": "d",
        "title": "deal",
        "stage": "proposal",
    }

    status, proposal, reasons = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="proposal",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status.value == "recommended"
    assert proposal["type"] == "task"
    assert "propose-only" in reasons[0]


def test_unavailable_preferences_are_reported_as_a_fallback():
    client = Pages(
        denied={"CRMPreferences"},
    )

    answer = asyncio.run(
        run_canonical_task(
            client,
            AgentRequest(text="x"),
            run_id="preferences",
        )
    )

    assert "CRMPreferences" in answer.unavailable_note
    assert answer.deal_rot_days == 30


def test_unknown_tool_is_rejected_before_sending_a_wire_request():
    from pipeline_agent.mcp.real_client import RealMCPClient

    client = RealMCPClient(
        Settings(
            mcp_url="https://example.test",
            mcp_token="token",
        )
    )

    with pytest.raises(
        MCPToolError,
        match="not one of",
    ):
        asyncio.run(
            client.call_tool(
                "made_up",
                {"entity": "Deal"},
            )
        )


def test_catalogue_drift_does_not_change_canonical_results():
    stable = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="stable",
        )
    )

    drifted_client = Pages()

    drifted_client.list_tools = lambda: _async_tools(
        RECORDED_TOOL_COUNT + 2
    )

    report = asyncio.run(
        run_preflight(
            drifted_client,
            configured_surface="entity_scoped",
        )
    )

    assert report.changes_from_recorded_state

    repeat = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="repeat",
        )
    )

    stable_ids = [
        deal.deal_id
        for deal in stable.deals
    ]

    repeat_ids = [
        deal.deal_id
        for deal in repeat.deals
    ]

    assert stable_ids == repeat_ids


def test_artifact_is_marked_running_before_the_task_finishes(tmp_path):
    writer = RunArtifactWriter(
        str(tmp_path),
        "running",
    )

    writer.start(
        task={"id": "x"},
        settings=Settings(
            mcp_token="secret",
        ),
    )

    status = json.loads(
        (writer.run_dir / "status.json").read_text()
    )

    config = json.loads(
        (writer.run_dir / "config.json").read_text()
    )

    assert status["status"] == "running"
    assert config["mcp_token"] == "***redacted***"


def test_repeated_propose_runs_return_the_same_statuses():
    first = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="one",
        )
    )

    second = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="two",
        )
    )

    first_statuses = [
        deal.action_status
        for deal in first.deals
    ]
    second_statuses = [
        deal.action_status
        for deal in second.deals
    ]
