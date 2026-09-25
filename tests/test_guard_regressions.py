"""Regression tests for the read-only catalogue and preflight behaviour."""

import asyncio
import datetime as dt
import json
import os

import pytest

from pipeline_agent.agent.contract import (
    ActionStatus,
    AgentRequest,
    ContactStatus,
    ExecutionMode,
)
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    DEFAULT_ROT_DAYS,
    RotCalibration,
    assess_rot,
    classify_contact,
    determine_next_action,
    load_activity_index,
    load_deals,
    run_canonical_task,
)
from pipeline_agent.config import Settings
from pipeline_agent.harness.loop import run_loop
from pipeline_agent.mcp.client import MCPClient, PermissionDeniedError
from pipeline_agent.mcp.real_client import _normalise_result
from pipeline_agent.mcp.tool_names import (
    ENTITY_SCOPED,
    SurfaceError,
    to_wire,
)
from pipeline_agent.preflight import (
    RECORDED_TOOL_COUNT,
    _gate_g1_verdict,
    run_preflight,
)
from pipeline_agent.runner import _load_dotenv


NOW = dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc)


class Pages(MCPClient):
    """Small MCP client used by the tests."""

    def __init__(
        self,
        deals=(),
        activities=(),
        *,
        include_total=True,
        denied=(),
    ):
        self.deals = list(deals)
        self.activities = list(activities)
        self.include_total = include_total
        self.denied = set(denied)
        self.calls = []
        self.created = []

    async def call_tool(self, name, arguments):
        entity = arguments.get("entity")

        self.calls.append((name, dict(arguments)))

        if entity in self.denied:
            raise PermissionDeniedError(
                name,
                entity,
                "crm",
                raw={"message": "not available to your seat"},
            )

        if name == "create":
            data = arguments["data"]
            self.created.append(data)
            return {"id": "created", **data}

        rows = {
            "Deal": self.deals,
            "Activity": self.activities,
            "CRMPreferences": [
                {"deal_rot_days": 17},
            ],
        }.get(entity, [])

        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 20)

        response = {
            "records": rows[offset:offset + limit],
        }

        if self.include_total:
            response["total"] = len(rows)

        return response

    async def list_tools(self):
        return await _async_tools(RECORDED_TOOL_COUNT)


async def _async_tools(count):
    required_tools = [
        "Deal.list",
        "Activity.list",
        "Activity.create",
    ]

    tools = [{"name": name} for name in required_tools]

    remaining = count - len(required_tools)

    tools.extend(
        {"name": f"Deal.tool_{i}"}
        for i in range(remaining)
    )

    return tools


def test_preflight_detects_a_change_in_available_tools():
    client = Pages()

    async def changed_tools():
        return await _async_tools(RECORDED_TOOL_COUNT + 1)

    client.list_tools = changed_tools

    report = asyncio.run(
        run_preflight(
            client,
            configured_surface=ENTITY_SCOPED,
        )
    )

    assert report.changes_from_recorded_state


def test_preflight_is_clean_when_tools_have_not_changed():
    report = asyncio.run(
        run_preflight(
            Pages(),
            configured_surface=ENTITY_SCOPED,
        )
    )

    assert report.changes_from_recorded_state == []


def test_rot_evidence_records_when_agent_authorship_suppresses_platform_signal():
    deal = {
        "id": "d",
        "stage": "new",
        "_rot_level": "fresh",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }

    index = ActivityIndex(
        agent_written_deals={"d"},
    )

    flagged, evidence = assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
    )

    assert flagged
    assert evidence["agent_write_suppressed_platform_signal"] is True
    assert "note" in evidence


def test_rot_evidence_does_not_mark_agent_authorship_when_not_present():
    deal = {
        "id": "d",
        "stage": "new",
        "_rot_level": "fresh",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }

    _, evidence = assess_rot(
        deal,
        ActivityIndex(),
        NOW,
        RotCalibration(5),
    )

    assert "agent_write_suppressed_platform_signal" not in evidence
    assert "note" not in evidence


class RefusingClient(MCPClient):
    def __init__(self, raw):
        self.raw = raw

    async def call_tool(self, name, arguments):
        raise PermissionDeniedError(
            name,
            arguments["entity"],
            "sales",
            raw=self.raw,
        )


def test_refusal_history_uses_the_platform_message_when_domain_is_not_given():
    prompts = []

    async def llm(prompt, _system):
        prompts.append(prompt)

        if len(prompts) == 1:
            return (
                '{"action":"call_tool","tool":"list",'
                '"arguments":{"entity":"Deal"}}'
            )

        return (
            '{"action":"done",'
            '"claimed_success":false,'
            '"answer":{}}'
        )

    asyncio.run(
        run_loop(
            {"id": "g03", "prompt": "x"},
            RefusingClient(
                {"message": "not in tools/list"}
            ),
            llm,
            "fake",
            max_steps=2,
        )
    )

    history = json.loads(prompts[1])["history"]

    assert "tools/list" in history[-1]
    assert "Domain '" not in history[-1]


def test_refusal_history_includes_domain_when_platform_reports_one():
    prompts = []

    async def llm(prompt, _system):
        prompts.append(prompt)

        if len(prompts) == 1:
            return (
                '{"action":"call_tool","tool":"list",'
                '"arguments":{"entity":"Deal"}}'
            )

        return (
            '{"action":"done",'
            '"claimed_success":false,'
            '"answer":{}}'
        )

    asyncio.run(
        run_loop(
            {"id": "g03", "prompt": "x"},
            RefusingClient(
                {"message": "sales domain denied"}
            ),
            llm,
            "fake",
            max_steps=2,
        )
    )

    history = json.loads(prompts[1])["history"]

    assert "Domain 'sales'" in history[-1]


def test_preflight_is_blocked_when_a_required_probe_is_denied():
    client = Pages(denied={"Activity"})

    report = asyncio.run(
        run_preflight(
            client,
            configured_surface=ENTITY_SCOPED,
        )
    )

    assert not report.canonical_task_ready
    assert "BLOCKED" in report.summary()


def test_preflight_is_ready_when_all_required_probes_succeed():
    report = asyncio.run(
        run_preflight(
            Pages(),
            configured_surface=ENTITY_SCOPED,
        )
    )

    assert report.canonical_task_ready


def test_missing_preferences_use_the_default_rot_period():
    client = Pages(denied={"CRMPreferences"})

    answer = asyncio.run(
        run_canonical_task(
            client,
            AgentRequest(text="x"),
            run_id="preferences",
        )
    )

    assert answer.deal_rot_days == DEFAULT_ROT_DAYS
    assert "CRMPreferences" in answer.unavailable_note


def test_preferences_are_used_when_they_are_available():
    answer = asyncio.run(
        run_canonical_task(
            Pages(),
            AgentRequest(text="x"),
            run_id="preferences",
        )
    )

    assert answer.deal_rot_days == 17
    assert answer.unavailable_note == ""


def test_load_deals_fetches_multiple_pages():
    client = Pages(
        deals=[
            {"id": str(i)}
            for i in range(2500)
        ]
    )

    deals = asyncio.run(load_deals(client))

    assert len(deals) == 2500

    deal_calls = [
        call
        for call in client.calls
        if call[1]["entity"] == "Deal"
    ]

    assert len(deal_calls) == 3


def test_load_deals_uses_one_request_for_a_short_result():
    client = Pages(
        deals=[
            {"id": str(i)}
            for i in range(138)
        ]
    )

    deals = asyncio.run(load_deals(client))

    assert len(deals) == 138

    deal_calls = [
        call
        for call in client.calls
        if call[1]["entity"] == "Deal"
    ]

    assert len(deal_calls) == 1


def test_activity_loading_continues_when_total_is_not_returned():
    client = Pages(
        activities=[
            {
                "id": str(i),
                "done": 0,
            }
            for i in range(2000)
        ],
        include_total=False,
    )

    index = asyncio.run(load_activity_index(client))

    assert index.scanned == 2000


def test_activity_loading_handles_a_total_count():
    client = Pages(
        activities=[
            {
                "id": str(i),
                "done": 0,
            }
            for i in range(2000)
        ]
    )

    index = asyncio.run(load_activity_index(client))

    assert index.scanned == 2000


def test_activity_loader_without_total_does_not_stop_early():
    no_total = Pages(
        activities=[
            {
                "id": str(i),
                "done": 0,
            }
            for i in range(2000)
        ],
        include_total=False,
    )

    with_total = Pages(
        activities=[
            {
                "id": str(i),
                "done": 0,
            }
            for i in range(2000)
        ]
    )

    asyncio.run(load_activity_index(no_total))
    asyncio.run(load_activity_index(with_total))

    assert len(no_total.calls) >= len(with_total.calls)


@pytest.mark.parametrize(
    "days",
    [-1, -3, -30],
)
def test_future_contact_dates_are_not_valid_evidence(days):
    contacted_at = (
        NOW + dt.timedelta(days=-days)
    ).isoformat()

    status, evidence = classify_contact(
        NOW,
        30,
        contacted_at,
    )

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert evidence["reason"]


@pytest.mark.parametrize(
    "days, expected",
    [
        (0, ContactStatus.RECENTLY_CONTACTED),
        (4, ContactStatus.RECENTLY_CONTACTED),
        (54, ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD),
    ],
)
def test_past_contact_dates_are_classified_correctly(days, expected):
    contacted_at = (
        NOW - dt.timedelta(days=days)
    ).isoformat()

    status, evidence = classify_contact(
        NOW,
        30,
        contacted_at,
    )

    assert status is expected
    assert "reason" not in evidence


def test_existing_activity_found_by_reread_prevents_duplicate_creation():
    deal = {
        "id": "d",
        "title": "D",
        "stage": "new",
    }

    client = Pages(
        activities=[
            {
                "id": "race",
                "deal_id": "d",
                "done": 0,
            }
        ]
    )

    status, _, _ = asyncio.run(
        determine_next_action(
            client,
            deal,
            mode=ExecutionMode.CREATE_NEXT_ACTIONS,
            run_id="reread",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.EXISTING
    assert not client.created


def test_create_mode_creates_activity_when_none_exists():
    deal = {
        "id": "d",
        "title": "D",
        "stage": "new",
    }

    client = Pages()

    status, _, _ = asyncio.run(
        determine_next_action(
            client,
            deal,
            mode=ExecutionMode.CREATE_NEXT_ACTIONS,
            run_id="create",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.CREATED
    assert len(client.created) == 1


def test_propose_mode_does_not_create_activity():
    deal = {
        "id": "d",
        "title": "D",
        "stage": "new",
    }

    client = Pages()

    status, _, _ = asyncio.run(
        determine_next_action(
            client,
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="propose",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.RECOMMENDED
    assert not client.created


def test_closed_deal_is_not_considered_rot():
    deal = {
        "id": "d",
        "stage": "closed_won",
        "_rot_days": 999,
    }

    candidate, evidence = assess_rot(
        deal,
        ActivityIndex(),
        NOW,
        RotCalibration(5),
    )

    assert not candidate
    assert evidence["excluded"] == "closed stage"


def test_open_deal_can_still_be_considered_rot():
    deal = {
        "id": "open",
        "stage": "new",
        "_rot_level": "attention",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }

    candidate, evidence = assess_rot(
        deal,
        ActivityIndex(),
        NOW,
        RotCalibration(5),
    )

    assert candidate
    assert "excluded" not in evidence


def test_normalise_result_accepts_data_field():
    result = _normalise_result(
        "list",
        {
            "data": [
                {"id": "x"},
            ]
        },
    )

    assert result["records"] == [{"id": "x"}]


def test_normalise_result_leaves_records_unchanged():
    result = _normalise_result(
        "list",
        {
            "records": [
                {"id": "y"},
            ]
        },
    )

    assert result == {
        "records": [
            {"id": "y"},
        ]
    }


def test_unknown_entity_tool_is_rejected():
    with pytest.raises(
        SurfaceError,
        match="report",
    ):
        to_wire(
            ENTITY_SCOPED,
            "report",
            {"entity": "Deal"},
        )


def test_entity_list_tool_preserves_filters():
    result = to_wire(
        ENTITY_SCOPED,
        "list",
        {
            "entity": "Deal",
            "filters": {
                "stage": "new",
            },
        },
    )

    assert result == (
        "Deal.list",
        {
            "stage": "new",
        },
    )


def test_mcp_url_is_required():
    with pytest.raises(
        RuntimeError,
        match="AGENTSWITCH_MCP_URL",
    ):
        Settings(
            mcp_token="t",
        ).require_mcp_credentials()


def test_mcp_token_is_required():
    with pytest.raises(
        RuntimeError,
        match="AGENTSWITCH_MCP_TOKEN",
    ):
        Settings(
            mcp_url="u",
        ).require_mcp_credentials()


def test_mcp_credentials_are_valid_when_both_values_are_present():
    Settings(
        mcp_url="u",
        mcp_token="t",
    ).require_mcp_credentials()


def test_invalid_llm_replies_do_not_call_tools():
    replies = iter(
        [
            "prose",
            "{bad",
            '{"action":"nonsense"}',
        ]
    )

    async def llm(*_):
        return next(replies)

    run = asyncio.run(
        run_loop(
            {"id": "g18", "prompt": "x"},
            Pages(),
            llm,
            "fake",
            max_steps=3,
        )
    )

    assert run.unusable_replies == 3
    assert not run.steps


def test_valid_tool_call_is_processed():
    async def llm(*_):
        return (
            '{"action":"call_tool",'
            '"tool":"list",'
            '"arguments":{"entity":"Deal"}}'
        )

    run = asyncio.run(
        run_loop(
            {"id": "g18", "prompt": "x"},
            Pages(),
            llm,
            "fake",
            max_steps=1,
        )
    )

    assert run.unusable_replies == 0
    assert run.steps[0].kind == "tool_call"


def test_load_dotenv_does_not_overwrite_existing_environment(
    tmp_path,
    monkeypatch,
):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "G19_EXISTING=file\n"
        "G19_NEW=added\n"
    )

    monkeypatch.setenv(
        "G19_EXISTING",
        "real",
    )
    monkeypatch.delenv(
        "G19_NEW",
        raising=False,
    )

    _load_dotenv(str(dotenv))

    assert os.environ["G19_EXISTING"] == "real"
    assert os.environ["G19_NEW"] == "added"


def test_g1_verdict_is_inconclusive_when_results_are_mixed():
    results = [
        {
            "entity": "Deal",
            "outcome": "refused",
        },
        {
            "entity": "Activity",
            "outcome": "error",
        },
    ]

    assert _gate_g1_verdict(results) == "inconclusive"


def test_g1_verdict_reports_sales_blocked_when_all_checks_are_refused():
    results = [
        {
            "entity": "Deal",
            "outcome": "refused",
        },
        {
            "entity": "Activity",
            "outcome": "refused",
        },
    ]

    assert _gate_g1_verdict(results) == "sales_blocked"


def test_g1_verdict_reports_sales_reachable_when_one_check_succeeds():
    results = [
        {
            "entity": "Deal",
            "outcome": "ok",
        },
        {
            "entity": "Activity",
            "outcome": "refused",
        },
    ]

    assert _gate_g1_verdict(results) == "sales_reachable"
