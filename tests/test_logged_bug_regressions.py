"""Regression tests for known Pipeline agent edge cases.

These tests use local fixtures and do not modify the shared CRM.
"""

import asyncio
import datetime as dt

from pipeline_agent.agent.contract import (
    ActionStatus,
    ContactStatus,
    ExecutionMode,
)
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    RotCalibration,
    assess_rot,
    calibrate_rot_boundary,
    classify_contact,
    determine_next_action,
    load_activity_index,
)

from .test_guard_regressions import NOW, Pages


def test_completed_activity_is_used_as_contact_history():
    """Activities returned with done=1 still count as contact history."""

    client = Pages(
        activities=[
            {
                "id": "completed",
                "done": 1,
                "party_id": "party",
                "due_date": "2026-08-01",
                "created_at": "2026-08-01T00:00:00+00:00",
            }
        ]
    )

    index = asyncio.run(
        load_activity_index(client)
    )

    contact = index.last_contacted(
        {
            "id": "deal",
            "party_id": "party",
        }
    )

    assert contact == (
        "2026-08-01",
        "party",
    )


def test_recheck_for_open_activity_uses_boolean_done_filter():
    """The Activity recheck should use done=False rather than integer 0."""

    client = Pages()

    status, _, _ = asyncio.run(
        determine_next_action(
            client,
            {
                "id": "deal",
                "title": "Deal",
                "stage": "new",
            },
            mode=ExecutionMode.CREATE_NEXT_ACTIONS,
            run_id="activity-recheck",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.CREATED

    activity_calls = [
        arguments
        for _, arguments in client.calls
        if arguments.get("entity") == "Activity"
    ]

    recheck_calls = [
        arguments
        for arguments in activity_calls
        if "filters" in arguments
    ]

    assert recheck_calls
    assert recheck_calls[-1]["filters"]["done"] is False


def test_future_activity_date_is_not_treated_as_recent_contact():
    """A future activity date is not valid evidence of a recent contact."""

    future_date = (
        NOW + dt.timedelta(days=1)
    ).isoformat()

    status, evidence = classify_contact(
        NOW,
        30,
        future_date,
    )

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert "future" in evidence["reason"]


def test_contact_history_can_be_found_through_party_link():
    """Activities linked to a party should be usable when the deal has no activity."""

    index = ActivityIndex(
        last_done_by_party={
            "party": "2026-08-01",
        }
    )

    deal = {
        "id": "deal",
        "party_id": "party",
    }

    assert index.last_contacted(deal) == (
        "2026-08-01",
        "party",
    )


def test_agent_created_activity_does_not_hide_a_rot_signal():
    """An agent-created activity should not reset the platform rot signal."""

    deal = {
        "id": "deal",
        "stage": "new",
        "_rot_level": "fresh",
        "_rot_days": 0,
        "updated_at": "2026-09-10T00:00:00+00:00",
    }

    index = ActivityIndex(
        agent_written_deals={"deal"},
    )

    candidate, evidence = assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
    )

    assert candidate
    assert (
        evidence["agent_write_suppressed_platform_signal"]
        is True
    )
    assert "agent logged an Activity" in evidence["note"]


def test_rot_boundary_is_derived_from_observed_deal_state():
    """The boundary should reflect the observed fresh/attention transition."""

    fresh_deal = {
        "id": "fresh",
        "_rot_level": "fresh",
        "updated_at": "2026-09-20T00:00:00+00:00",
    }

    flagged_deal = {
        "id": "flagged",
        "_rot_level": "attention",
        "updated_at": "2026-09-16T00:00:00+00:00",
    }

    calibration = calibrate_rot_boundary(
        [fresh_deal, flagged_deal],
        ActivityIndex(),
        NOW,
    )

    assert calibration.boundary_days == 5
    assert calibration.lower == 5
    assert calibration.upper == 8


def test_unknown_pipeline_stage_requires_human_review():
    """An unfamiliar Pipeline stage must not result in an invented action."""

    deal = {
        "id": "deal",
        "title": "Deal",
        "stage": "Bench Vice 30mm (Kg)",
    }

    status, proposal, reasons = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="unknown-stage",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.NEEDS_HUMAN_REVIEW
    assert proposal is None
    assert "human review" in reasons[0]
