import asyncio
import datetime as dt
import sys
import urllib.error

import pytest

from pipeline_agent.agent.contract import CanonicalAnswer, RefusalResult
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    _parse_ts,
    is_agent_authored,
)
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.llm import (
    LLMConfigError,
    _anthropic_backend,
    _ollama_backend,
)
from pipeline_agent.mcp.tool_names import (
    ENTITY_SCOPED,
    GENERIC,
    SurfaceError,
    from_wire,
    normalise_surface,
    to_wire,
)
from pipeline_agent.preflight import PreflightReport
from pipeline_agent.runner import _report_result


@pytest.mark.parametrize(
    "surface, tool, arguments, expected",
    [
        (
            GENERIC,
            "list",
            {"entity": "Deal", "limit": 1},
            ("list", {"entity": "Deal", "limit": 1}),
        ),
        (
            ENTITY_SCOPED,
            "get",
            {"entity": "Deal", "id": "d"},
            ("Deal.get", {"id": "d"}),
        ),
        (
            ENTITY_SCOPED,
            "create",
            {
                "entity": "Activity",
                "data": {"subject": "x"},
            },
            ("Activity.create", {"subject": "x"}),
        ),
        (
            ENTITY_SCOPED,
            "update",
            {
                "entity": "Deal",
                "id": "d",
                "data": {"stage": "new"},
            },
            ("Deal.update", {"id": "d", "stage": "new"}),
        ),
        (
            ENTITY_SCOPED,
            "count",
            {
                "entity": "Deal",
                "filters": {"stage": "new"},
            },
            ("Deal.count", {"stage": "new"}),
        ),
        (
            ENTITY_SCOPED,
            "search",
            {
                "entity": "Deal",
                "query": "lathe",
            },
            ("Deal.search", {"query": "lathe"}),
        ),
        (
            ENTITY_SCOPED,
            "delete",
            {
                "entity": "Activity",
                "id": "a",
            },
            ("Activity.delete", {"id": "a"}),
        ),
        (
            ENTITY_SCOPED,
            "get_schema",
            {"entity": "Deal"},
            ("Deal.schema", {}),
        ),
    ],
)
def test_tool_arguments_are_converted_to_wire_format(
    surface,
    tool,
    arguments,
    expected,
):
    result = to_wire(surface, tool, arguments)

    assert result == expected


@pytest.mark.parametrize(
    "surface, wire_name, expected",
    [
        (GENERIC, "list", ("", "list")),
        (ENTITY_SCOPED, "Deal.list", ("Deal", "list")),
        (ENTITY_SCOPED, "Activity.create", ("Activity", "create")),
        (
            ENTITY_SCOPED,
            "CRMPreferences.list",
            ("CRMPreferences", "list"),
        ),
    ],
)
def test_wire_tool_names_can_be_converted_back(
    surface,
    wire_name,
    expected,
):
    assert from_wire(surface, wire_name) == expected


@pytest.mark.parametrize(
    "value",
    [
        "generic",
        "GENERIC",
        "entity-scoped",
        "entity_scoped",
    ],
)
def test_known_surface_values_are_normalised(value):
    result = normalise_surface(value)

    assert result in (GENERIC, ENTITY_SCOPED)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "graphql",
        " entity ",
    ],
)
def test_unknown_surface_values_are_rejected(value):
    with pytest.raises(SurfaceError):
        normalise_surface(value)


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, False),
        ("", False),
        ("not-a-date", False),
        ("2026-09-24T00:00:00", True),
        ("2026-09-24T00:00:00Z", True),
        ("2026-09-24T00:00:00+05:30", True),
    ],
)
def test_timestamp_parser_accepts_supported_formats(value, expected):
    parsed = _parse_ts(value)

    assert (parsed is not None) is expected


@pytest.mark.parametrize(
    "activity, expected",
    [
        ({}, False),
        ({"description": "human note"}, False),
        (
            {
                "description": (
                    "x (created_by=pipeline_agent run=x)"
                )
            },
            True,
        ),
    ],
)
def test_agent_authorship_is_detected_from_provenance(activity, expected):
    assert is_agent_authored(activity) is expected


def test_last_contact_date_uses_deal_activity_first():
    index = ActivityIndex(
        last_done_by_deal={
            "d": "2026-09-01",
        },
        last_done_by_party={
            "p": "2026-09-02",
        },
    )

    deal = {
        "id": "d",
        "party_id": "p",
    }

    assert index.last_contacted(deal) == (
        "2026-09-01",
        "deal",
    )


def test_last_contact_date_falls_back_to_party_activity():
    index = ActivityIndex(
        last_done_by_party={
            "p": "2026-09-02",
        }
    )

    deal = {
        "id": "d",
        "party_id": "p",
    }

    assert index.last_contacted(deal) == (
        "2026-09-02",
        "party",
    )


def test_last_contact_date_is_missing_when_there_is_no_activity():
    index = ActivityIndex()

    deal = {
        "id": "d",
        "party_id": "p",
    }

    assert index.last_contacted(deal) == (
        None,
        "none",
    )


def test_anthropic_backend_reports_missing_sdk(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        None,
    )

    with pytest.raises(
        LLMConfigError,
        match="Anthropic SDK",
    ):
        _anthropic_backend("model")


def test_ollama_backend_reports_connection_failure(monkeypatch):
    def refuse_connection(*args, **kwargs):
        raise urllib.error.URLError(
            "connection refused"
        )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        refuse_connection,
    )

    with pytest.raises(
        LLMConfigError,
        match="cannot reach the Ollama daemon",
    ):
        asyncio.run(
            _ollama_backend("model")(
                "x",
                "system",
            )
        )


@pytest.mark.parametrize(
    "result_type",
    [
        "canonical",
        "refusal",
        "preflight_ready",
        "preflight_blocked",
    ],
)
def test_runner_records_the_result_state(result_type):
    run = TaskRun(task_id="x")

    if result_type == "canonical":
        result = CanonicalAnswer(
            30,
            "now",
            [],
            "done",
        )

    elif result_type == "refusal":
        result = RefusalResult(
            "no",
            "",
            [],
            [],
        )

    else:
        result = PreflightReport(
            configured_surface="entity_scoped",
            canonical_task_ready=(
                result_type == "preflight_ready"
            ),
        )

    _report_result(run, result)

    if result_type == "refusal":
        assert run.ended == "refused"
        assert run.claimed_success is False
    else:
        assert run.ended == "done"
        assert run.claimed_success is (
            result_type == "canonical"
            or result_type == "preflight_ready"
        )
