"""Offline regression tests for read-only catalogue behaviour."""

import argparse
import asyncio
import datetime as dt

import pytest

from pipeline_agent.agent.contract import (
    AgentRequest,
    ContactStatus,
    ExecutionMode,
)
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    classify_contact,
    load_activity_index,
    run_canonical_task,
)
from pipeline_agent.harness.loop import run_loop
from pipeline_agent.mcp.client import MCPClient, MCPToolError
from pipeline_agent.mcp.stub_client import StubMCPClient
from pipeline_agent.preflight import run_preflight
from pipeline_agent.runner import main_async

from .test_guard_regressions import NOW, Pages


@pytest.mark.parametrize(
    "deal_id",
    [
        "not-a-candidate",
        "not/a/uuid",
    ],
)
def test_requested_deal_that_is_not_a_candidate_is_reported(deal_id):
    answer = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(
                text="x",
                deal_ids=[deal_id],
            ),
            run_id="scope",
        )
    )

    assert not answer.deals
    assert "requested deal(s) not among" in answer.summary


@pytest.mark.parametrize(
    "days, expected",
    [
        (None, ContactStatus.NEVER_CONTACTED),
        (60, ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD),
        (1, ContactStatus.RECENTLY_CONTACTED),
        (0, ContactStatus.RECENTLY_CONTACTED),
        (-1, ContactStatus.INSUFFICIENT_EVIDENCE),
    ],
)
def test_contact_status_handles_common_date_cases(days, expected):
    if days is None:
        contacted_at = None
    else:
        contacted_at = (
            NOW - dt.timedelta(days=days)
        ).isoformat()

    status, _ = classify_contact(
        NOW,
        30,
        contacted_at,
    )

    assert status is expected


@pytest.mark.parametrize(
    "limit",
    [0, 1, 4, 20],
)
def test_deal_limit_does_not_change_number_of_candidates_found(limit):
    answer = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(
                text="x",
                max_deals=limit,
            ),
            run_id="cap",
        )
    )

    assert answer.candidates_found == 4
    assert len(answer.deals) == min(limit, 4)


@pytest.mark.parametrize(
    "limit",
    [0, 1],
)
def test_partial_run_is_reported_when_limit_is_below_candidate_count(limit):
    answer = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(
                text="x",
                max_deals=limit,
            ),
            run_id="cap",
        )
    )

    assert "PARTIAL RUN" in answer.summary


def test_full_run_is_not_marked_as_partial():
    answer = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(
                text="x",
                max_deals=4,
            ),
            run_id="cap",
        )
    )

    assert "PARTIAL RUN" not in answer.summary


@pytest.mark.parametrize(
    "include_total",
    [True, False],
)
@pytest.mark.parametrize(
    "count",
    [1000, 1001, 1999],
)
def test_activity_loader_reads_all_pages(include_total, count):
    client = Pages(
        activities=[
            {
                "id": str(i),
                "done": 0,
            }
            for i in range(count)
        ],
        include_total=include_total,
    )

    index = asyncio.run(
        load_activity_index(client)
    )

    assert index.scanned == count

    expected_pages = (count + 999) // 1000
    assert len(client.calls) >= expected_pages


@pytest.mark.parametrize(
    "stage",
    [
        "closed_won",
        "closed_lost",
        "closed_abandoned",
    ],
)
def test_closed_deals_are_not_returned_as_candidates(stage):
    deal = {
        "id": "closed",
        "stage": stage,
        "_rot_level": "attention",
        "_rot_days": 999,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    answer = asyncio.run(
        run_canonical_task(
            Pages(deals=[deal]),
            AgentRequest(text="x"),
            run_id="closed",
        )
    )

    assert not answer.deals


def test_canonical_answer_contains_evidence_for_each_deal():
    answer = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="canonical",
        )
    )

    assert answer.deals

    for deal in answer.deals:
        assert deal.rot_evidence
        assert deal.contact_evidence
        assert deal.reasons


def test_canonical_results_are_sorted_by_rot_days():
    answer = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="canonical",
        )
    )

    rot_days = [
        deal.rot_evidence["independent_rot_days"]
        for deal in answer.deals
    ]

    assert rot_days == sorted(
        rot_days,
        reverse=True,
    )


@pytest.mark.parametrize(
    "entity",
    [
        "CRMPreferences",
        "Deal",
        "Activity",
    ],
)
def test_stub_client_returns_records_for_supported_entities(entity):
    result = asyncio.run(
        StubMCPClient().list_(
            entity,
            limit=1,
        )
    )

    assert result["records"]


class UnreachableClient(MCPClient):
    async def call_tool(self, _name, _arguments):
        raise MCPToolError(
            "list",
            "cannot reach host",
        )


def test_unreachable_mcp_is_not_treated_as_success():
    report = asyncio.run(
        run_preflight(
            UnreachableClient(),
            configured_surface="entity_scoped",
        )
    )

    assert report.gate_g1 == "inconclusive"
    assert not report.canonical_task_ready


@pytest.mark.parametrize(
    "reply",
    [
        "prose",
        '{"action":"unknown"}',
    ],
)
def test_invalid_llm_reply_does_not_result_in_a_tool_call(reply):
    async def llm(*_):
        return reply

    run = asyncio.run(
        run_loop(
            {
                "id": "loop",
                "prompt": "x",
            },
            StubMCPClient(),
            llm,
            "fake",
            max_steps=1,
        )
    )

    assert run.unusable_replies == 1
    assert not run.steps


def test_live_mode_requires_the_mcp_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.delenv(
        "AGENTSWITCH_MCP_URL",
        raising=False,
    )
    monkeypatch.setenv(
        "AGENTSWITCH_MCP_TOKEN",
        "token",
    )

    args = argparse.Namespace(
        mode="live",
        task="canonical",
        request="x",
        exec_mode="propose",
        limit=None,
        deal_id=[],
        max_steps=1,
    )

    assert asyncio.run(main_async(args)) == 2


def test_live_mode_requires_the_mcp_token(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv(
        "AGENTSWITCH_MCP_URL",
        "https://example.test",
    )
    monkeypatch.delenv(
        "AGENTSWITCH_MCP_TOKEN",
        raising=False,
    )

    args = argparse.Namespace(
        mode="live",
        task="canonical",
        request="x",
        exec_mode="propose",
        limit=None,
        deal_id=[],
        max_steps=1,
    )

    assert asyncio.run(main_async(args)) == 2
