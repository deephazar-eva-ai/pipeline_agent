"""Keystone action and contact-history regressions."""

import asyncio

import pytest

from pipeline_agent.agent.contract import ActionStatus, ExecutionMode
from pipeline_agent.agent.workflow import (
    RotCalibration,
    assess_rot,
    calibrate_rot_boundary,
    determine_next_action,
    find_hidden_silence,
    find_uncalled,
    is_supplier_only,
)

from .keystone_support import (
    ActivityClient as FixtureClient,
    NOW,
    activity as make_activity,
    build_index,
    deal as make_deal,
)


# Rot safeguards

def test_agent_activity_does_not_reset_platform_rot():
    index = build_index(
        make_activity(
            id="agent-follow-up",
            deal_id="deal",
            due_date="2026-09-27",
            done=0,
            type="task",
            description="(created_by=pipeline_agent run=keystone-regression)",
        )
    )

    deal = make_deal(
        created_at="2026-09-28T12:00:00+00:00",
        updated_at="2026-09-18T12:00:00+00:00",
    )

    candidate, evidence = assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )

    assert candidate
    assert evidence["independent_rot_days"] == 0
    assert evidence["platform_mirror_days"] == 10
    assert evidence["agent_write_suppressed_platform_signal"] is True


def test_rot_calibration_ignores_agent_written_deals():
    index = build_index(
        make_activity(
            id="agent-row",
            deal_id="agent-written",
            due_date="2026-09-27",
            done=0,
            description="created_by=pipeline_agent run=keystone-regression",
        )
    )

    deals = [
        make_deal(
            "fresh",
            updated_at="2026-09-24T12:00:00+00:00",
            level="fresh",
        ),
        make_deal(
            "flagged",
            updated_at="2026-09-20T12:00:00+00:00",
            level="warning",
        ),
        make_deal(
            "agent-written",
            updated_at="2026-08-01T12:00:00+00:00",
            level="fresh",
        ),
    ]

    calibration = calibrate_rot_boundary(
        deals,
        index,
        NOW,
    )

    assert calibration.boundary_days == 5
    assert calibration.lower == 5
    assert calibration.upper == 8


# Next-action selection

def test_overdue_open_activity_is_reported_as_overdue():
    index = build_index(
        make_activity(
            id="overdue-task",
            deal_id="deal",
            due_date="2026-09-18",
            done=0,
            type="task",
        )
    )

    status, existing, reasons = asyncio.run(
        determine_next_action(
            FixtureClient(),
            make_deal(),
            mode=ExecutionMode.PROPOSE,
            run_id="overdue-activity",
            now=NOW,
            index=index,
        )
    )

    assert status is ActionStatus.OVERDUE
    assert existing["id"] == "overdue-task"


def test_current_open_activity_takes_precedence_over_overdue_activity():
    index = build_index(
        make_activity(
            id="overdue",
            deal_id="deal",
            due_date="2026-09-18",
            done=0,
            type="task",
        ),
        make_activity(
            id="current",
            deal_id="deal",
            due_date="2026-10-01",
            done=0,
            type="call",
        ),
    )

    status, existing, _ = asyncio.run(
        determine_next_action(
            FixtureClient(),
            make_deal(),
            mode=ExecutionMode.PROPOSE,
            run_id="current-activity",
            now=NOW,
            index=index,
        )
    )

    assert status is ActionStatus.EXISTING
    assert existing["id"] == "current"


# Customer call and supplier scope

def test_recent_call_on_another_deal_counts_for_the_same_party():
    deals = [
        make_deal(
            "deal-a",
            party_id="party",
            created_at="2026-07-01T12:00:00+00:00",
        ),
        make_deal(
            "deal-b",
            party_id="party",
            created_at="2026-07-01T12:00:00+00:00",
        ),
    ]

    index = build_index(
        make_activity(
            id="recent-email",
            deal_id="deal-b",
            due_date="2026-09-27",
            type="email",
        ),
        make_activity(
            id="recent-call",
            deal_id="deal-a",
            due_date="2026-09-18",
            type="call",
        ),
        make_activity(
            id="future-call",
            deal_id="deal-b",
            due_date="2026-10-18",
            type="call",
        ),
    )

    assert find_uncalled(deals, index, NOW, 30) == []


def test_agent_created_call_does_not_count_as_customer_contact():
    deals = [
        make_deal(
            "deal",
            party_id="party",
            created_at="2026-07-01T12:00:00+00:00",
        )
    ]

    index = build_index(
        make_activity(
            id="agent-call",
            deal_id="deal",
            due_date="2026-09-27",
            type="call",
            description="created_by=pipeline_agent run=keystone-regression",
        )
    )

    uncalled = find_uncalled(
        deals,
        index,
        NOW,
        30,
    )

    assert [row.party_id for row in uncalled] == ["party"]
    assert uncalled[0].last_called is None


def test_recent_non_call_activity_is_reported_as_hidden_silence():
    deals = [
        make_deal(
            "deal",
            party_id="party",
            created_at="2026-07-01T12:00:00+00:00",
        )
    ]

    index = build_index(
        make_activity(
            id="old-call",
            deal_id="deal",
            due_date="2026-08-01",
            type="call",
        ),
        make_activity(
            id="recent-email",
            deal_id="deal",
            due_date="2026-09-24",
            type="email",
        ),
    )

    uncalled = find_uncalled(
        deals,
        index,
        NOW,
        30,
    )

    hidden = find_hidden_silence(
        deals,
        index,
        NOW,
        uncalled,
        rotting_ids=set(),
    )

    assert len(hidden) == 1
    assert hidden[0].masked_deals[0].fresh_because == "non_call_activity"
    assert (
        hidden[0].masked_deals[0].fresh_basis_detail
        == "done email (not a call)"
    )


def test_recent_edit_does_not_create_hidden_silence_for_a_rotting_deal():
    deals = [
        make_deal(
            "deal",
            party_id="party",
            created_at="2026-07-01T12:00:00+00:00",
            updated_at="2026-09-27T12:00:00+00:00",
        )
    ]

    index = build_index(
        make_activity(
            id="old-call",
            deal_id="deal",
            due_date="2026-08-01",
            type="call",
        )
    )

    uncalled = find_uncalled(
        deals,
        index,
        NOW,
        30,
    )

    hidden = find_hidden_silence(
        deals,
        index,
        NOW,
        uncalled,
        rotting_ids={"deal"},
    )

    assert hidden == []


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ({"supplier"}, True),
        ({"supplier", "customer"}, False),
        ({"supplier", "prospect"}, False),
        ({"customer"}, False),
        (None, False),
    ],
)
def test_only_supplier_parties_are_excluded_from_sales(roles, expected):
    assert is_supplier_only(roles) is expected
