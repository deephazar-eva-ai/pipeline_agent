"""Low-level edge cases behind the read-only workflows.

Covers tool-name translation between MCP surfaces, timestamp parsing,
agent-provenance detection, last-contact lookup, LLM backend setup errors,
and how the runner turns a result into a TaskRun.
"""

import asyncio
import sys
import urllib.error

import pytest

from pipeline_agent import llm
from pipeline_agent.agent.contract import CanonicalAnswer, RefusalResult
from pipeline_agent.agent.workflow import ActivityIndex, _parse_ts, is_agent_authored
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.llm import LLMConfigError, _anthropic_backend, _ollama_backend
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


# --- tool names / MCP surfaces -----------------------------------------------


class TestToWire:
    def test_generic_surface_passes_through_unchanged(self):
        args = {"entity": "Deal", "limit": 1}

        assert to_wire(GENERIC, "list", args) == ("list", {"entity": "Deal", "limit": 1})

    @pytest.mark.parametrize(
        "tool, arguments, expected",
        [
            pytest.param("get", {"entity": "Deal", "id": "d"},
                         ("Deal.get", {"id": "d"}), id="get"),
            pytest.param("create", {"entity": "Activity", "data": {"subject": "x"}},
                         ("Activity.create", {"subject": "x"}), id="create-unwraps-data"),
            pytest.param("update", {"entity": "Deal", "id": "d", "data": {"stage": "new"}},
                         ("Deal.update", {"id": "d", "stage": "new"}), id="update-merges-id-and-data"),
            pytest.param("count", {"entity": "Deal", "filters": {"stage": "new"}},
                         ("Deal.count", {"stage": "new"}), id="count-unwraps-filters"),
            pytest.param("search", {"entity": "Deal", "query": "lathe"},
                         ("Deal.search", {"query": "lathe"}), id="search"),
            pytest.param("delete", {"entity": "Activity", "id": "a"},
                         ("Activity.delete", {"id": "a"}), id="delete"),
            pytest.param("get_schema", {"entity": "Deal"},
                         ("Deal.schema", {}), id="get_schema-renamed"),
        ],
    )
    def test_entity_scoped_surface_prefixes_entity(self, tool, arguments, expected):
        assert to_wire(ENTITY_SCOPED, tool, arguments) == expected


@pytest.mark.parametrize(
    "surface, wire_name, expected",
    [
        pytest.param(GENERIC, "list", ("", "list"), id="generic"),
        pytest.param(ENTITY_SCOPED, "Deal.list", ("Deal", "list"), id="deal"),
        pytest.param(ENTITY_SCOPED, "Activity.create", ("Activity", "create"), id="activity"),
        pytest.param(ENTITY_SCOPED, "CRMPreferences.list", ("CRMPreferences", "list"),
                     id="mixed-case-entity"),
    ],
)
def test_from_wire_splits_entity_and_tool(surface, wire_name, expected):
    assert from_wire(surface, wire_name) == expected


@pytest.mark.parametrize("value", ["generic", "GENERIC", "entity-scoped", "entity_scoped"])
def test_normalise_surface_accepts_known_spellings(value):
    assert normalise_surface(value) in (GENERIC, ENTITY_SCOPED)


@pytest.mark.parametrize("value", ["", "graphql", " entity "], ids=["empty", "unknown", "partial"])
def test_normalise_surface_rejects_anything_else(value):
    with pytest.raises(SurfaceError):
        normalise_surface(value)


# --- workflow helpers --------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "not-a-date"], ids=["none", "empty", "garbage"])
def test_parse_ts_returns_none_for_invalid_input(value):
    assert _parse_ts(value) is None


@pytest.mark.parametrize(
    "value",
    ["2026-09-24T00:00:00", "2026-09-24T00:00:00Z", "2026-09-24T00:00:00+05:30"],
    ids=["naive", "zulu", "offset"],
)
def test_parse_ts_accepts_iso_variants(value):
    assert _parse_ts(value) is not None


@pytest.mark.parametrize(
    "activity, expected",
    [
        pytest.param({}, False, id="no-description"),
        pytest.param({"description": "human note"}, False, id="human-note"),
        pytest.param({"description": "x (created_by=pipeline_agent run=x)"}, True, id="agent-marker"),
    ],
)
def test_is_agent_authored_needs_explicit_marker(activity, expected):
    assert is_agent_authored(activity) is expected


class TestLastContacted:
    deal = {"id": "d", "party_id": "p"}

    def test_uses_deal_activity_first(self):
        index = ActivityIndex(
            last_done_by_deal={"d": "2026-09-01"},
            last_done_by_party={"p": "2026-09-02"},
        )

        assert index.last_contacted(self.deal) == ("2026-09-01", "deal")

    def test_falls_back_to_party_activity(self):
        index = ActivityIndex(last_done_by_party={"p": "2026-09-02"})

        assert index.last_contacted(self.deal) == ("2026-09-02", "party")

    def test_returns_none_when_no_activity(self):
        assert ActivityIndex().last_contacted(self.deal) == (None, "none")


# --- LLM backends ------------------------------------------------------------


def test_anthropic_backend_explains_missing_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `import anthropic` fail

    with pytest.raises(LLMConfigError, match="Anthropic SDK"):
        _anthropic_backend("model")


def test_ollama_backend_explains_connection_refused(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    async def run_inline(func, *args):
        return func(*args)

    # The backend runs urlopen in a thread; run it inline instead so the
    # failure is immediate and we don't depend on a local Ollama daemon.
    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(llm.asyncio, "to_thread", run_inline)

    with pytest.raises(LLMConfigError, match="cannot reach the Ollama daemon"):
        asyncio.run(_ollama_backend("model")("x", "system"))


# --- runner ------------------------------------------------------------------


@pytest.mark.parametrize(
    "result, expected_end, expected_success",
    [
        pytest.param(CanonicalAnswer(30, "now", [], "done"), "done", True, id="canonical"),
        pytest.param(RefusalResult("no", "", [], []), "refused", False, id="refusal"),
        pytest.param(PreflightReport(configured_surface="entity_scoped", canonical_task_ready=True),
                     "done", True, id="preflight-ready"),
        pytest.param(PreflightReport(configured_surface="entity_scoped", canonical_task_ready=False),
                     "done", False, id="preflight-blocked"),
    ],
)
def test_report_result_sets_end_state(result, expected_end, expected_success):
    run = TaskRun(task_id="x")

    _report_result(run, result)

    assert run.ended == expected_end
    assert run.claimed_success is expected_success