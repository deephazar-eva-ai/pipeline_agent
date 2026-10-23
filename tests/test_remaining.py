"""Offline tests for contact age, write-time rechecks, the due-date guard and central runner results."""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pipeline_agent import central_runner
from pipeline_agent.agent.contract import ActionStatus, ContactStatus, ExecutionMode
from pipeline_agent.agent.pipeline_checks import PipelineContext
from pipeline_agent.agent.workflow import (
    PROVENANCE_MARKER,
    ActivityIndex,
    classify_contact,
    determine_next_action,
    find_uncalled,
    is_contact,
)
from pipeline_agent.harness.loop import call_shape_error
from pipeline_agent.mcp.client import MCPClient

NOW = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)
THRESHOLD = 30


class ReReadClient(MCPClient):
    """Answers the re-reads done right before a write; records any creates."""

    def __init__(self, current_deal=None, by_deal=(), by_party=()):
        self.current_deal = current_deal
        self.by_deal = list(by_deal)
        self.by_party = list(by_party)
        self.calls = []
        self.created = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "get":
            return self.current_deal
        if name == "create":
            self.created.append(dict(arguments["data"]))
            return {"id": f"created-{len(self.created)}", **arguments["data"]}

        assert name == "list"
        filters = arguments.get("filters") or {}
        if "deal_id" in filters:
            rows = self.by_deal
        elif "party_id" in filters:
            rows = self.by_party
        else:
            rows = []
        return {"records": rows[:arguments.get("limit", 20)], "total": len(rows)}


def run(coro):
    return asyncio.run(coro)


def proposal_deal(**changes):
    return {"id": "D1", "title": "A proposal", "stage": "proposal", "party_id": "P1", **changes}


def next_action(client, run_id, mode=ExecutionMode.CREATE_NEXT_ACTIONS, **kwargs):
    return run(determine_next_action(
        client, proposal_deal(), mode=mode, run_id=run_id, now=NOW, index=ActivityIndex(), **kwargs,
    ))


# --- contact age --------------------------------------------------------------

def test_no_contact_is_never_contacted():
    status, evidence = classify_contact(NOW, THRESHOLD, None, basis="party")

    assert status is ContactStatus.NEVER_CONTACTED
    assert evidence == {"last_contacted": None, "basis": "party"}


@pytest.mark.parametrize("last_contacted, expected_status, days", [
    ("2026-09-07", ContactStatus.RECENTLY_CONTACTED, 30),
    ("2026-09-06", ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD, 31),
], ids=["day-30", "day-31"])
def test_threshold_day_still_counts_as_recent(last_contacted, expected_status, days):
    status, evidence = classify_contact(NOW, THRESHOLD, last_contacted)

    assert status is expected_status
    assert evidence["days_since"] == days


def test_future_contact_is_not_recent():
    status, evidence = classify_contact(NOW, THRESHOLD, "2026-10-09")

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert "future" in evidence["reason"]


def test_unparseable_contact_date():
    status, evidence = classify_contact(NOW, THRESHOLD, "not-a-date")

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert evidence["reason"] == "unparseable date"


def test_days_are_counted_in_company_timezone():
    # 03:00 on the 7th in New York; the contact was 23:30 the night before.
    now = dt.datetime(2026, 10, 7, 3, tzinfo=ZoneInfo("America/New_York"))

    status, evidence = classify_contact(now, THRESHOLD, "2026-10-06T23:30:00-04:00", basis="party")

    assert status is ContactStatus.RECENTLY_CONTACTED
    assert evidence["days_since"] == 1
    assert evidence["basis"] == "party"


@pytest.mark.parametrize("activity, expected", [
    ({"type": "call", "done": True, "outcome": "Cancelled"}, False),
    ({"type": "call", "done": True, "outcome": " cancelled by customer"}, False),
    ({"type": "email", "done": True}, True),
    ({"type": "meeting", "done": True}, True),
    ({"type": "call", "done": True}, True),
    ({"type": "task", "done": True}, False),
    ({"type": "note", "done": True}, False),
], ids=["cancelled", "cancelled-leading-space", "email", "meeting", "call", "task", "note"])
def test_is_contact(activity, expected):
    assert is_contact(activity) is expected


def test_uncalled_looks_at_calls_only_and_groups_by_party():
    deals = [
        {"id": "A", "party_id": "P", "stage": "proposal", "value": 10},
        {"id": "B", "party_id": "P", "stage": "new", "value": 20},
    ]
    index = ActivityIndex(
        last_call_by_party={"P": "2026-09-23"},
        last_unlinked_contact_by_party={"P": "2026-10-05"},
    )
    assert find_uncalled(deals, index, NOW, THRESHOLD) == []

    # Once the last call is too old, the recent non-call contact doesn't help.
    index.last_call_by_party["P"] = "2026-08-28"
    rows = find_uncalled(deals, index, NOW, THRESHOLD)

    assert len(rows) == 1
    assert rows[0].party_id == "P"
    assert rows[0].days_since == 40
    assert rows[0].open_deal_ids == ["A", "B"]


def test_new_never_called_party_goes_to_too_new():
    too_new = []
    deals = [{"id": "Q1", "party_id": "Q", "stage": "proposal"}]
    index = ActivityIndex(first_contact_by_party={"Q": "2026-09-27"})

    rows = find_uncalled(deals, index, NOW, THRESHOLD, too_new=too_new)

    assert rows == []
    assert [(r.party_id, r.days_since) for r in too_new] == [("Q", 10)]


def test_never_called_sorts_before_longest_silence():
    deals = [
        {"id": "R1", "party_id": "R", "stage": "proposal"},
        {"id": "S1", "party_id": "S", "stage": "proposal"},
    ]
    index = ActivityIndex(
        first_contact_by_party={"R": "2026-08-08"},
        last_call_by_party={"S": "2026-08-23"},
    )

    rows = find_uncalled(deals, index, NOW, THRESHOLD)

    assert [(r.party_id, r.days_since) for r in rows] == [("R", None), ("S", 45)]


# --- changes between planning and writing -------------------------------------

def test_creates_once_when_rechecks_are_clean():
    client = ReReadClient()

    status, created, reasons = next_action(client, "clean")

    assert status is ActionStatus.CREATED
    assert len(client.created) == 1
    assert created["deal_id"] == "D1"
    assert PROVENANCE_MARKER in created["description"]
    assert "fresh re-read" in reasons[0]


def test_no_write_if_a_deal_activity_shows_up_on_reread():
    concurrent = {"id": "A1", "deal_id": "D1", "done": False, "subject": "Someone called"}
    client = ReReadClient(by_deal=[concurrent])

    status, record, reasons = next_action(client, "deal-race")

    assert status is ActionStatus.EXISTING
    assert record is concurrent
    assert client.created == []
    assert "appeared between the check and the write" in reasons[0]


def test_no_write_if_an_unlinked_party_activity_shows_up():
    concurrent = {"id": "A2", "party_id": "P1", "done": False, "subject": "Customer task"}
    client = ReReadClient(by_party=[concurrent])

    status, record, _ = next_action(client, "party-race")

    assert status is ActionStatus.EXISTING
    assert record is concurrent
    assert client.created == []


def test_party_activity_on_another_deal_does_not_block():
    client = ReReadClient(by_party=[{"id": "A3", "party_id": "P1", "deal_id": "D2", "done": False}])

    status, _, _ = next_action(client, "sibling-deal")

    assert status is ActionStatus.CREATED
    assert len(client.created) == 1


def test_only_recommends_if_deal_moved_before_the_write():
    client = ReReadClient(current_deal=proposal_deal(stage="closed_lost"))

    status, proposal, reasons = next_action(client, "stage-race", ctx=PipelineContext(today="2026-10-07"))

    assert status is ActionStatus.RECOMMENDED
    assert proposal["deal_id"] == "D1"
    assert client.created == []
    assert "moved to 'closed_lost'" in reasons[0]


def test_propose_mode_does_no_rereads():
    client = ReReadClient(by_deal=[{"id": "would-not-be-read"}])

    status, _, reasons = next_action(client, "propose", mode=ExecutionMode.PROPOSE)

    assert status is ActionStatus.RECOMMENDED
    assert client.calls == []
    assert client.created == []
    assert reasons == ["propose-only mode - recommendation is not written"]


# --- due-date guard -----------------------------------------------------------

def due_date_error(due_date, today):
    arguments = {
        "entity": "Activity",
        "data": {"subject": "Follow up", "type": "call", "due_date": due_date, "done": False},
    }
    return call_shape_error("create", arguments, derived_tools=False, today=today)


@pytest.mark.parametrize("due_date, expected", [
    ("2026-10-17", None),
    ("2026-10-18", None),
    ("17/10/2026", "Activity.due_date must be YYYY-MM-DD"),
], ids=["today", "future", "bad-format"])
def test_due_date_today_or_later_in_iso_format(due_date, expected):
    assert due_date_error(due_date, dt.date(2026, 10, 17)) == expected


def test_due_date_before_company_today_is_refused():
    assert "before the company's today (2026-10-17)" in due_date_error("2026-10-16", dt.date(2026, 10, 17))


def test_due_date_uses_company_day_not_utc():
    # 04:25 IST on the 17th is still the 16th in UTC.
    now_ist = dt.datetime(2026, 10, 17, 4, 25, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert now_ist.astimezone(dt.timezone.utc).date() == dt.date(2026, 10, 16)

    assert due_date_error("2026-10-17", now_ist.date()) is None
    assert "before the company's today" in due_date_error("2026-10-16", now_ist.date())


# --- central runner -----------------------------------------------------------

@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(central_runner, "DEADLINE", None)
    return central_runner.Results(tmp_path / "results.json", "ks")


def test_timeout_becomes_a_failed_row(results):
    async def too_slow():
        await asyncio.sleep(0.03)
        return True, "unexpected"

    passed = run(central_runner.run_task(results, ("slow", "Slow task"), 0.001, too_slow))

    row = results.tasks[0]
    assert passed is False
    assert row["passed"] is False
    assert row["evidence"].startswith("timed out")
    assert row["evidence"].endswith("s]")


def test_exception_becomes_a_failed_row(results):
    async def broken():
        raise RuntimeError("x")

    passed = run(central_runner.run_task(results, ("broken", "Broken task"), 1, broken))

    assert passed is False
    assert results.tasks[0]["passed"] is False
    assert results.tasks[0]["evidence"].startswith("RuntimeError: x")


def test_results_file_is_saved_after_each_row(tmp_path):
    path = tmp_path / "results.json"
    results = central_runner.Results(path, "ks")

    results.record("one", "First", True, "first evidence")
    first = json.loads(path.read_text())
    results.record("two", "Second", False, "second evidence")
    second = json.loads(path.read_text())

    assert len(first["tasks"]) == 1
    assert len(second["tasks"]) == 2
    assert [r["id"] for r in second["tasks"]] == ["ks:one", "ks:two"]
    assert [r["passed"] for r in second["tasks"]] == [True, False]
    assert [r["score"] for r in second["tasks"]] == [1.0, 0.0]
    assert second["summary"] == "ks: 1/2 tasks passed"
    assert not path.with_name("results.json.tmp").exists()


@pytest.mark.parametrize("ended, expected", [("time_budget", False), ("done", True)])
def test_check_loop_passes_only_a_finished_read_only_run(monkeypatch, ended, expected):
    monkeypatch.setattr(central_runner, "DEADLINE", None)
    fake_run = SimpleNamespace(
        ended=ended,
        calls=2,
        steps=[SimpleNamespace(kind="tool_call", tool="list", entity="Deal")],
        created_record_ids=[],
        updated_record_ids=[],
        unsupported_figures=[],
        input_tokens=10,
        output_tokens=20,
        error="",
    )

    async def fake_loop(*args, **kwargs):
        return fake_run

    monkeypatch.setattr(central_runner, "run_loop", fake_loop)

    passed, evidence = run(central_runner.check_loop(None, None, "test-model"))

    assert passed is expected
    assert f"ended={ended}" in evidence
    assert "tool steps=1" in evidence
    assert "writes=0" in evidence