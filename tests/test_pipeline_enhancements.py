"""Tests for aggregate, contact_status, request splitting, figure checking,
and the agent loop's duplicate and repeat-read guards.

Everything runs against in-memory clients, so results don't depend on what
happens to be in a live CRM.
"""

import asyncio
import datetime as dt
import json

import pytest

from pipeline_agent.agent import aggregate, contact_status
from pipeline_agent.agent.contract import (
    ActionStatus,
    CanonicalAnswer,
    DealResult,
    UncalledParty,
)
from pipeline_agent.agent.pipeline_checks import PipelineContext
from pipeline_agent.agent.request_guard import split_request
from pipeline_agent.agent.workflow import ActivityIndex
from pipeline_agent.harness.loop import (
    WritePolicy,
    open_activity_duplicate,
    run_loop,
    unsupported_figures,
)
from pipeline_agent.mcp.client import MCPClient
from pipeline_agent.mcp.stub_client import StubMCPClient


NOW = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)


# --- helpers -----------------------------------------------------------------


class RecordsClient(MCPClient):
    """In-memory client that supports get, filtered list and offset/limit paging."""

    def __init__(self, records):
        self.records = list(records)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "get":
            return next(row for row in self.records if row["id"] == arguments["id"])

        assert name == "list"
        rows = list(self.records)
        for field, wanted in (arguments.get("filters") or {}).items():
            rows = [row for row in rows if row.get(field) == wanted]
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 20)
        return {"records": rows[offset:offset + limit], "total": len(rows)}


class NoTotalRecordsClient(RecordsClient):
    """Same as RecordsClient but list responses omit ``total``."""

    async def call_tool(self, name, arguments):
        result = await super().call_tool(name, arguments)
        if name == "list":
            result.pop("total")
        return result


def run(coro):
    return asyncio.run(coro)


def scripted_llm(*replies):
    replies = iter(replies)

    async def llm(prompt, system):
        return next(replies)

    return llm


def tool_call(tool, **arguments):
    return json.dumps({"action": "call_tool", "tool": tool, "arguments": arguments})


def done(answer):
    return json.dumps({"action": "done", "claimed_success": True, "answer": answer})


def returns(value):
    """Async stand-in for a loader that always returns ``value``."""

    async def fake(*_args, **_kwargs):
        return value

    return fake


def refusals(loop_run):
    return [step.detail for step in loop_run.steps if step.kind == "refused"]


# --- aggregate ---------------------------------------------------------------


class TestAggregate:
    @pytest.fixture
    def client(self):
        # One full page of open deals plus one closed deal on the second page.
        open_deals = [
            {"id": f"open-{n}", "stage": "new", "currency": "USD", "value": 7}
            for n in range(aggregate.PAGE_SIZE)
        ]
        won = {"id": "three", "stage": "closed_won", "currency": "USD", "value": 100}
        return RecordsClient(open_deals + [won])

    def test_reads_every_page_and_applies_open_only(self, client):
        result = run(aggregate.aggregate(client, {
            "entity": "Deal",
            "group_by": "stage",
            "sum": "value",
            "open_only": True,
            "note": "a model note is not a CRM filter",
        }))

        assert result["records_read"] == aggregate.PAGE_SIZE + 1
        assert result["records_used"] == aggregate.PAGE_SIZE
        assert result["overall"]["sum_value"] == 7000
        assert result["groups"] == [
            {"key": "new", "count": aggregate.PAGE_SIZE, "sum_value": 7000},
        ]
        assert client.calls[0][1]["limit"] == aggregate.PAGE_SIZE
        assert client.calls[1][1]["offset"] == aggregate.PAGE_SIZE

    def test_second_call_reuses_cached_records(self, client):
        cache = {}
        run(aggregate.aggregate(client, {"entity": "Deal", "group_by": "stage", "sum": "value",
                                         "open_only": True}, cache=cache))

        result = run(aggregate.aggregate(client, {"entity": "Deal", "sum": "value"}, cache=cache))

        assert result["overall"]["sum_value"] == 7100
        assert len(client.calls) == 2  # only the two pages from the first call

    def test_open_only_on_non_deal_errors_without_reading(self):
        client = RecordsClient([])

        result = run(aggregate.aggregate(client, {"entity": "Activity", "open_only": True}))

        assert result == {"error": "open_only applies to Deal only"}
        assert client.calls == []


class TestAggregateWithoutTotal:
    """62 western deals (one with a bad value, mixed currencies) and one eastern deal."""

    @pytest.fixture
    def client(self):
        west = [
            {
                "id": f"west-{n}",
                "region": "west",
                "bucket": f"group-{n:02}",
                "currency": "USD" if n % 2 else "EUR",
                "value": "not-a-number" if n == 0 else "1",
            }
            for n in range(62)
        ]
        east = {"id": "east", "region": "east", "bucket": "not-selected", "value": 99}
        return NoTotalRecordsClient(west + [east])

    @pytest.fixture
    def result(self, client):
        return run(aggregate.aggregate(client, {
            "entity": "Deal",
            "region": "east",  # top-level keys aren't filters; only `filters` is
            "filters": {"region": "west"},
            "group_by": "bucket",
            "sum": "value",
        }))

    def test_only_filters_key_is_used(self, result):
        assert result["filters"] == {"region": "west"}
        assert result["records_read"] == 62

    def test_short_page_ends_paging_without_total(self, client, result):
        assert result["total_reported"] is None
        assert result["complete"] is True
        assert result["note_total"]
        assert len(client.calls) == 1

    def test_bad_values_are_counted_and_sums_split_by_currency(self, result):
        assert result["overall"]["missing_value"] == 1
        assert sum(result["overall"]["sum_value"].values()) == 61

    def test_groups_are_capped(self, result):
        assert result["group_count"] == 62
        assert len(result["groups"]) == aggregate.MAX_GROUPS
        assert "only the 60 largest" in result["note"]


# --- contact_status.view -----------------------------------------------------


@pytest.fixture
def analysis():
    return {
        "summary": {"open_deals": 3, "rotting_deals": 2},
        "deals": [
            {"deal_id": "contact", "rotting": True, "rotting_by_contact": True},
            {"deal_id": "platform", "rotting": True, "rotting_by_contact": False},
            {"deal_id": "fresh", "rotting": False},
        ],
        "data_issues": [{"code": "bad_stage", "deal_id": "contact"}],
        "late_orders": [],
        "uncalled": [{"customer": "Acme"}],
    }


def test_view_summary_pages_deals(analysis):
    summary = contact_status.view(analysis, {"limit": 1})

    assert summary["deals_page"]["returned"] == 1
    assert summary["deals_page"]["next_offset"] == 1


def test_view_filters_rotting_deals_by_contact_rule(analysis):
    result = contact_status.view(
        analysis, {"section": "deals", "rotting_only": True, "rule": "contact"},
    )

    assert [row["deal_id"] for row in result["records"]] == ["contact"]


def test_view_filters_data_issues_by_code(analysis):
    result = contact_status.view(analysis, {"section": "data_issues", "code": "bad_stage"})

    assert result["total"] == 1


def test_contact_status_analyses_once_per_cache(monkeypatch):
    calls = []

    async def fake_analyse(client, *, run_id):
        calls.append(run_id)
        return {
            "summary": {"open_deals": 1, "rotting_deals": 0},
            "deals": [{"deal_id": "one", "rotting": False}],
            "data_issues": [],
            "late_orders": [],
            "uncalled": [],
        }

    monkeypatch.setattr(contact_status, "analyse", fake_analyse)
    cache = {}

    first = run(contact_status.contact_status(RecordsClient([]), run_id="case", cache=cache))
    second = run(contact_status.contact_status(
        RecordsClient([]), {"section": "deals", "deal_id": "one"}, run_id="case", cache=cache,
    ))

    assert calls == ["case"]
    assert first["open_deals"] == 1
    assert second["records"] == [{"deal_id": "one", "rotting": False}]


# --- contact_status on a large book ------------------------------------------


BOOK_SIZE = 61  # more than one page


def make_deals():
    return [
        {
            "id": f"deal-{n}",
            "title": f"Deal {n}",
            "party_id": f"party-{n}",
            "stage": "undefined" if n == 0 else "qualification",
            "pipeline": "sales",
            "value": n + 1,
            "currency": "USD",
            "created_at": "2026-08-01T00:00:00+00:00",
        }
        for n in range(BOOK_SIZE)
    ]


def make_answer(deals):
    results = [
        DealResult(
            deal_id=deal["id"],
            stage=deal["stage"],
            rot_evidence={
                "independently_flags": n % 2 == 0,
                "platform_flags": True,
                "independent_rot_days": 67,
                "rot_boundary_days": 30,
                "_rot_level": "attention",
            },
            contact_status="not_contacted_since_threshold",
            contact_evidence={},
            action_status=ActionStatus.RECOMMENDED,
            next_action=None,
        )
        for n, deal in enumerate(deals)
    ]
    return CanonicalAnswer(
        30,
        NOW.isoformat(),
        results,
        "synthetic analysis",
        candidates_found=len(results),
        data_issues=[
            {"code": "seeded_issue", "deal_id": d["id"], "deal": d["title"], "detail": "fixture"}
            for d in deals
        ],
        late_orders=[
            {"party": d["party_id"], "order": f"SO-{n}", "delivery_date": "2026-09-01",
             "days_late": 36, "open_deal_ids": [d["id"]]}
            for n, d in enumerate(deals)
        ],
        uncalled=[
            UncalledParty(d["party_id"], f"Party {n}", None, None, [d["id"]], d["value"])
            for n, d in enumerate(deals)
        ],
    )


@pytest.fixture
def large_book(monkeypatch):
    """Patch contact_status's loaders with a 61-deal book.

    deal-0 has a stage that isn't in its pipeline, an activity linked to the
    wrong customer, and a quote that turned into a sales order.
    Returns the list of canonical-task calls so tests can count them.
    """
    deals = make_deals()
    answer = make_answer(deals)
    index = ActivityIndex(
        contact_dates_indexed=True,
        mismatched_links=[{
            "deal_id": "deal-0",
            "subject": "Wrong customer",
            "activity_party_id": "other",
            "deal_party_id": "party-0",
        }],
    )
    context = PipelineContext(
        today="2026-10-07",
        pipelines={"sales": {"qualification"}},
        quotations=[{
            "id": "quote-0", "deal_id": "deal-0", "number": "Q-0", "status": "sent",
            "valid_till": "2026-09-30", "grand_total": 10,
        }],
        sales_orders=[{
            "number": "SO-0", "quotation_id": "quote-0", "party_id": "party-0",
            "status": "confirmed", "delivery_date": "2026-09-30",
            "delivered_status": "undelivered",
        }],
    )
    canonical_calls = []

    async def fake_canonical(*_args, **_kwargs):
        canonical_calls.append(True)
        return answer

    monkeypatch.setattr(contact_status, "run_canonical_task", fake_canonical)
    monkeypatch.setattr(contact_status.checks, "load_company_clock", returns((NOW, "UTC", "$")))
    monkeypatch.setattr(contact_status, "load_deals", returns(deals))
    monkeypatch.setattr(contact_status, "load_activity_index", returns(index))
    monkeypatch.setattr(contact_status.checks, "load_context", returns(context))
    return canonical_calls


class TestContactStatusLargeBook:
    @pytest.fixture
    def cache(self):
        return {}

    def status(self, cache, params=None):
        return run(contact_status.contact_status(RecordsClient([]), params, cache=cache))

    def test_runs_canonical_task_once_across_sections(self, large_book, cache):
        self.status(cache)
        for section in ("data_issues", "late_orders", "uncalled"):
            self.status(cache, {"section": section, "offset": 30, "limit": 60})

        assert large_book == [True]

    def test_summary_deals_include_quotes_and_orders(self, large_book, cache):
        summary = self.status(cache)

        assert summary["deals_page"]["total"] == BOOK_SIZE
        first_deal = summary["deals_page"]["records"][0]
        assert first_deal["quotes"][0]["number"] == "Q-0"
        assert first_deal["orders"][0]["number"] == "SO-0"
        assert first_deal["customer_late_orders"] == 1

    def test_data_issues_include_derived_checks(self, large_book, cache):
        issues = self.status(cache, {"section": "data_issues", "offset": 30, "limit": 60})

        # 61 seeded issues + stage-not-in-pipeline + mismatched activity link
        assert issues["total"] == 63
        codes = {row["code"] for row in issues["records"]}
        assert "stage_not_in_pipeline" in codes
        assert "activity_linked_to_other_customers_deal" in codes

    @pytest.mark.parametrize("section", ["late_orders", "uncalled"])
    def test_sections_page_from_offset(self, large_book, cache, section):
        page = self.status(cache, {"section": section, "offset": 30, "limit": 60})

        assert page["total"] == BOOK_SIZE
        assert page["returned"] == 31


# --- duplicate guard ---------------------------------------------------------


class TestOpenActivityDuplicate:
    @pytest.fixture
    def deal_activity_client(self):
        return RecordsClient([{
            "id": "deal-open", "deal_id": "deal-1", "party_id": "party-1", "done": False,
            "subject": "Already scheduled", "due_date": "2026-10-08",
        }])

    def test_blocks_when_deal_has_open_activity(self, deal_activity_client):
        message = run(open_activity_duplicate(
            deal_activity_client, {"entity": "Activity", "data": {"deal_id": "deal-1"}},
        ))

        assert "deal-1 already has an open Activity" in message

    def test_blocks_when_customer_has_open_activity(self):
        client = RecordsClient([{
            "id": "party-open", "party_id": "party-1", "done": False,
            "subject": "Customer follow-up", "due_date": "2026-10-08",
        }])

        message = run(open_activity_duplicate(
            client, {"entity": "Activity", "data": {"party_id": "party-1"}},
        ))

        assert "customer already has an open Activity" in message

    def test_allows_logging_a_completed_contact(self, deal_activity_client):
        message = run(open_activity_duplicate(
            deal_activity_client,
            {"entity": "Activity", "data": {"deal_id": "deal-1", "done": True}},
        ))

        assert message is None


# --- agent loop guards -------------------------------------------------------


def test_loop_does_not_repeat_identical_read():
    llm = scripted_llm(
        tool_call("list", entity="Deal"),
        tool_call("list", entity="Deal"),
        done({"deals": 6}),
    )

    result = run(run_loop({"id": "repeat-read", "prompt": "List deals."}, StubMCPClient(),
                          llm, "test", max_steps=3, derived_tools=False))

    assert result.ended == "done"
    assert [step.kind for step in result.steps].count("tool_call") == 1
    assert any("repeat-read guard" in detail for detail in refusals(result))


def test_loop_blocks_duplicate_create_before_calling_client():
    client = StubMCPClient()
    llm = scripted_llm(
        tool_call("create", entity="Activity", data={
            "deal_id": "stub-deal-existing", "subject": "Duplicate", "done": False,
        }),
        done("blocked"),
    )
    policy = WritePolicy(entities=frozenset({"Activity"}), max_writes=1, run_id="case")

    result = run(run_loop(
        {"id": "duplicate-create", "prompt": "Create a follow-up.", "run_id": "case"},
        client, llm, "test", max_steps=2, write_policy=policy, derived_tools=False,
    ))

    assert result.created_record_ids == []
    assert client.created_records == []
    assert any("duplicate guard" in detail for detail in refusals(result))


def test_loop_write_stamps_provenance_and_blocks_second_create():
    client = StubMCPClient()
    policy = WritePolicy(
        entities=frozenset({"Activity"}),
        deal_ids=frozenset({"stub-deal-needs-action"}),
        max_writes=1,
        run_id="write-case",
    )

    def create_follow_up():
        llm = scripted_llm(
            tool_call("create", entity="Activity", data={
                "deal_id": "stub-deal-needs-action", "subject": "Follow up", "done": False,
            }),
            done("created"),
        )
        return run(run_loop(
            {"id": "write-stub", "prompt": "Create a follow-up.", "run_id": "write-case"},
            client, llm, "test", max_steps=2, write_policy=policy, derived_tools=False,
        ))

    first = create_follow_up()
    second = create_follow_up()

    assert len(first.created_record_ids) == 1
    assert len(client.created_records) == 1
    description = client.created_records[0]["record"]["description"]
    assert "created_by=pipeline_agent run=write-case" in description
    assert second.created_record_ids == []
    assert any("duplicate guard" in detail for detail in refusals(second))


# --- request splitting and figure checks -------------------------------------


def test_split_request_keeps_in_scope_part_for_data_refusal():
    result = split_request("List rotting deals and their unpaid invoices.")

    assert result.refusal is not None
    assert result.in_scope_rest is True
    assert "invoice_payment" in result.refusal.message


def test_split_request_refuses_whole_request_for_bulk_action():
    result = split_request("List rotting deals and close every deal as lost.")

    assert result.refusal is not None
    assert result.in_scope_rest is False
    assert "bulk_close" in result.refusal.message


def test_unsupported_figures_allows_rounding_and_indian_grouping():
    answer = {"rounded": "₹68,657", "indian": "₹4,51,902.60", "invented": 12}
    evidence = ["The tool returned 68657.35 and 451902.60."]

    assert unsupported_figures(answer, evidence) == ["12"]