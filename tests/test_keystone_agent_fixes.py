"""Regression tests for Keystone next actions, rot and contact history."""
import asyncio
import datetime as dt

import pytest

from pipeline_agent.agent import workflow
from pipeline_agent.agent.contract import ActionStatus, AgentRequest, ContactStatus, ExecutionMode
from pipeline_agent.agent.workflow import (
    RotCalibration,
    assess_rot,
    calibrate_rot_boundary,
    classify_contact,
    contact_evidence_issues,
    determine_next_action,
    find_hidden_silence,
    find_uncalled,
    is_supplier_only,
)

from .keystone_support import NOW, ActivityClient, build_index
from .keystone_support import activity as make_activity
from .keystone_support import deal as make_deal

THRESHOLD = 30
LONG_AGO = "2026-07-01T12:00:00+00:00"  # deal created well before the threshold window
AGENT_MARKER = "created_by=pipeline_agent run=keystone-regression"


def returns(value):
    """An async stub that ignores its arguments and returns `value`."""
    async def stub(*_args, **_kwargs):
        return value
    return stub


def next_action(index, run_id):
    return asyncio.run(determine_next_action(
        ActivityClient(), make_deal(),
        mode=ExecutionMode.PROPOSE, run_id=run_id, now=NOW, index=index,
    ))


# --- rot ----------------------------------------------------------------------

def test_agent_activity_does_not_reset_platform_rot():
    index = build_index(make_activity(
        id="agent-follow-up", deal_id="deal", due_date="2026-09-27", done=0, type="task",
        description=f"({AGENT_MARKER})",
    ))
    deal = make_deal(created_at="2026-09-28T12:00:00+00:00", updated_at="2026-09-18T12:00:00+00:00")

    candidate, evidence = assess_rot(deal, index, NOW, RotCalibration(5), threshold_days=THRESHOLD)

    assert candidate
    assert evidence["independent_rot_days"] == 0
    assert evidence["platform_mirror_days"] == 10
    assert evidence["agent_write_suppressed_platform_signal"] is True


def test_rot_calibration_ignores_agent_written_deals():
    index = build_index(make_activity(
        id="agent-row", deal_id="agent-written", due_date="2026-09-27", done=0,
        description=AGENT_MARKER,
    ))
    deals = [
        make_deal("fresh", updated_at="2026-09-24T12:00:00+00:00", level="fresh"),
        make_deal("flagged", updated_at="2026-09-20T12:00:00+00:00", level="warning"),
        # Looks very stale, but the agent wrote to it, so it mustn't skew the boundary.
        make_deal("agent-written", updated_at="2026-08-01T12:00:00+00:00", level="fresh"),
    ]

    calibration = calibrate_rot_boundary(deals, index, NOW)

    assert calibration.boundary_days == 5
    assert calibration.lower == 5
    assert calibration.upper == 8


def test_exactly_threshold_days_is_not_rotting_or_uncalled():
    deals = [make_deal("deal", party_id="party", created_at=LONG_AGO)]
    index = build_index(make_activity(
        id="threshold-call", deal_id="deal", party_id="party", due_date="2026-08-29", type="call",
    ))

    status, evidence = classify_contact(NOW, THRESHOLD, "2026-08-29")
    candidate, rot_evidence = assess_rot(deals[0], index, NOW, RotCalibration(5), threshold_days=THRESHOLD)

    assert status is ContactStatus.RECENTLY_CONTACTED
    assert evidence["days_since"] == 30
    assert not candidate
    assert rot_evidence["independently_flags"] is False
    assert find_uncalled(deals, index, NOW, THRESHOLD) == []


def test_contact_day_count_is_stable_throughout_the_company_day():
    tz = dt.timezone(-dt.timedelta(hours=4))
    morning = dt.datetime(2026, 9, 28, 9, 5, tzinfo=tz)
    evening = dt.datetime(2026, 9, 28, 20, 11, tzinfo=tz)

    morning_status, morning_evidence = classify_contact(morning, THRESHOLD, "2026-08-29")
    evening_status, evening_evidence = classify_contact(evening, THRESHOLD, "2026-08-29")

    assert morning_status is evening_status
    assert morning_evidence["days_since"] == evening_evidence["days_since"] == 30


# --- next action --------------------------------------------------------------

def test_overdue_open_activity_is_reported_as_overdue():
    index = build_index(make_activity(
        id="overdue-task", deal_id="deal", due_date="2026-09-18", done=0, type="task",
    ))

    status, existing, _reasons = next_action(index, "overdue-activity")

    assert status is ActionStatus.OVERDUE
    assert existing["id"] == "overdue-task"


def test_current_open_activity_wins_over_overdue_one():
    index = build_index(
        make_activity(id="overdue", deal_id="deal", due_date="2026-09-18", done=0, type="task"),
        make_activity(id="current", deal_id="deal", due_date="2026-10-01", done=0, type="call"),
    )

    status, existing, _reasons = next_action(index, "current-activity")

    assert status is ActionStatus.EXISTING
    assert existing["id"] == "current"


# --- what counts as contact ---------------------------------------------------

def test_recent_call_on_another_deal_counts_for_the_same_party():
    deals = [
        make_deal("deal-a", party_id="party", created_at=LONG_AGO),
        make_deal("deal-b", party_id="party", created_at=LONG_AGO),
    ]
    index = build_index(
        make_activity(id="recent-email", deal_id="deal-b", due_date="2026-09-27", type="email"),
        make_activity(id="recent-call", deal_id="deal-a", due_date="2026-09-18", type="call"),
        make_activity(id="future-call", deal_id="deal-b", due_date="2026-10-18", type="call"),
    )

    assert find_uncalled(deals, index, NOW, THRESHOLD) == []


def test_completed_tasks_and_cancelled_calls_are_not_contact():
    deals = [make_deal("deal", party_id="party", created_at=LONG_AGO)]
    index = build_index(
        make_activity(id="completed-task", deal_id="deal", due_date="2026-09-27", type="task"),
        make_activity(
            id="cancelled-call", deal_id="deal", due_date="2026-09-27", type="call",
            outcome="Cancelled by customer", description=AGENT_MARKER,
        ),
    )

    uncalled = find_uncalled(deals, index, NOW, THRESHOLD)

    assert [row.party_id for row in uncalled] == ["party"]
    assert uncalled[0].last_called is None
    assert index.noncontact_done == 2


def test_completed_email_and_meeting_count_as_contact():
    index = build_index(
        make_activity(id="meeting", deal_id="deal", due_date="2026-09-22", type="meeting"),
        make_activity(id="email", deal_id="deal", due_date="2026-09-27", type="email"),
        # Open, so it shouldn't count even though it's the latest.
        make_activity(id="open-call", deal_id="deal", due_date="2026-09-28", done=0, type="call"),
    )

    assert index.last_contacted(make_deal()) == ("2026-09-27", "deal")
    assert index.last_contact_type_by_deal["deal"] == "email"


def test_completed_call_with_agent_marker_counts_but_is_flagged():
    deals = [make_deal("deal", party_id="party", created_at=LONG_AGO)]
    index = build_index(make_activity(
        id="marked-call", deal_id="deal", party_id="party", due_date="2026-09-27", type="call",
        subject="Customer follow-up", description="created_by=pipeline_agent run=external-run",
    ))

    issues = contact_evidence_issues(deals, index)

    assert find_uncalled(deals, index, NOW, THRESHOLD) == []
    assert index.marked_contacts == [{
        "activity_id": "marked-call",
        "subject": "Customer follow-up",
        "type": "call",
        "date": "2026-09-27",
        "deal_id": "deal",
        "party_id": "party",
        "marker_run": "external-run",
    }]
    assert [(i["deal_id"], i["code"]) for i in issues] == [("deal", "agent_marker_on_completed_contact")]
    assert "Counted as contact" in issues[0]["detail"]


def test_future_dated_contact_alone_is_insufficient_evidence():
    deals = [make_deal("deal", party_id="party")]
    index = build_index(make_activity(
        id="future-call", deal_id="deal", party_id="party", due_date="2026-10-01", type="call",
    ))

    last_contacted, basis = index.last_contacted(deals[0])
    status, evidence = classify_contact(NOW, THRESHOLD, last_contacted, basis)
    issues = contact_evidence_issues(deals, index)

    assert (last_contacted, basis) == ("2026-10-01", "deal")
    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert "future" in evidence["reason"]
    assert [(i["deal_id"], i["code"]) for i in issues] == [("deal", "insufficient_evidence")]


def test_mismatched_contact_link_is_reported_with_names(monkeypatch):
    deals = [make_deal("deal", party_id="party-acme", title="Acme renewal",
                       created_at="2026-09-27T12:00:00+00:00")]
    index = workflow.ActivityIndex(
        contact_dates_indexed=True,
        mismatched_links=[{
            "activity_id": "mislinked-call",
            "subject": "Call for Beta",
            "deal_id": "deal",
            "activity_party_id": "party-beta",
            "deal_party_id": "party-acme",
        }],
    )
    context = workflow.checks.PipelineContext(
        today="2026-09-28",
        currency_symbol="$",
        pipelines={"sales": {"qualification"}},
        parties={
            "party-acme": {"id": "party-acme", "name": "Acme Manufacturing"},
            "party-beta": {"id": "party-beta", "name": "Beta Manufacturing"},
        },
    )
    roles = {"party-acme": {"customer"}, "party-beta": {"customer"}}

    monkeypatch.setattr(workflow, "load_rot_threshold", returns((30, True)))
    monkeypatch.setattr(workflow, "load_deals", returns(deals))
    monkeypatch.setattr(workflow, "load_activity_index", returns(index))
    monkeypatch.setattr(workflow, "load_party_roles", returns((roles, "")))
    monkeypatch.setattr(workflow.checks, "load_company_clock", returns((NOW, "UTC", "$")))
    monkeypatch.setattr(workflow.checks, "load_context", returns(context))

    answer = asyncio.run(workflow.run_canonical_task(
        ActivityClient(), AgentRequest(text="List contact evidence."), run_id="named-mismatch",
    ))

    assert answer.mismatched_links == [{
        "activity_id": "mislinked-call",
        "subject": "Call for Beta",
        "deal_id": "deal",
        "deal": "Acme renewal",
        "activity_party": "Beta Manufacturing",
        "deal_party": "Acme Manufacturing",
    }]


# --- hidden silence -----------------------------------------------------------

def test_recent_non_call_activity_is_hidden_silence():
    deals = [make_deal("deal", party_id="party", created_at=LONG_AGO)]
    index = build_index(
        make_activity(id="old-call", deal_id="deal", due_date="2026-08-01", type="call"),
        make_activity(id="recent-email", deal_id="deal", due_date="2026-09-24", type="email"),
    )

    uncalled = find_uncalled(deals, index, NOW, THRESHOLD)
    hidden = find_hidden_silence(deals, index, NOW, uncalled, rotting_ids=set())

    assert len(hidden) == 1
    masked = hidden[0].masked_deals[0]
    assert masked.fresh_because == "non_call_activity"
    assert masked.fresh_basis_detail == "done email (not a call)"


def test_recent_edit_on_rotting_deal_is_not_hidden_silence():
    deals = [make_deal("deal", party_id="party", created_at=LONG_AGO,
                       updated_at="2026-09-27T12:00:00+00:00")]
    index = build_index(make_activity(id="old-call", deal_id="deal", due_date="2026-08-01", type="call"))

    uncalled = find_uncalled(deals, index, NOW, THRESHOLD)
    hidden = find_hidden_silence(deals, index, NOW, uncalled, rotting_ids={"deal"})

    assert hidden == []


# --- supplier scope -----------------------------------------------------------

@pytest.mark.parametrize("roles, expected", [
    ({"supplier"}, True),
    ({"supplier", "customer"}, False),
    ({"supplier", "prospect"}, False),
    ({"customer"}, False),
    (None, False),
])
def test_only_supplier_parties_are_excluded_from_sales(roles, expected):
    assert is_supplier_only(roles) is expected