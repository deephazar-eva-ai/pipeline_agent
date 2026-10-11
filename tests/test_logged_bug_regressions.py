"""
Regression tests for Pipeline agent edge cases we've hit before.

Everything here runs on local fixtures; nothing touches the shared CRM.
"""
import asyncio
import datetime as dt

from pipeline_agent.agent.contract import ActionStatus, ContactStatus, ExecutionMode
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


def next_action(client, deal, mode, run_id):
    return asyncio.run(determine_next_action(
        client, deal, mode=mode, run_id=run_id, now=NOW, index=ActivityIndex(),
    ))


def test_done_as_integer_still_counts_as_contact_history():
    # done comes back as 1 here rather than True.
    client = Pages(activities=[{
        "id": "completed",
        "done": 1,
        "type": "call",
        "party_id": "party",
        "due_date": "2026-08-01",
        "created_at": "2026-08-01T00:00:00+00:00",
    }])

    index = asyncio.run(load_activity_index(client))

    assert index.last_contacted({"id": "deal", "party_id": "party"}) == ("2026-08-01", "party")


def test_open_activity_recheck_filters_on_boolean_done():
    client = Pages()
    deal = {"id": "deal", "title": "Deal", "stage": "new"}

    status, _, _ = next_action(client, deal, ExecutionMode.CREATE_NEXT_ACTIONS, "activity-recheck")

    assert status is ActionStatus.CREATED

    rechecks = [
        args for _, args in client.calls
        if args.get("entity") == "Activity" and "filters" in args
    ]
    assert rechecks
    # Has to be the boolean False, not the integer 0.
    assert rechecks[-1]["filters"]["done"] is False


def test_future_dated_contact_is_not_recent():
    tomorrow = (NOW + dt.timedelta(days=1)).isoformat()

    status, evidence = classify_contact(NOW, 30, tomorrow)

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert "future" in evidence["reason"]


def test_contact_falls_back_to_party_when_deal_has_none():
    index = ActivityIndex(last_done_by_party={"party": "2026-08-01"})

    assert index.last_contacted({"id": "deal", "party_id": "party"}) == ("2026-08-01", "party")


def test_agent_written_deal_keeps_its_rot_signal():
    # The platform says "fresh", but only because the agent touched the deal.
    deal = {
        "id": "deal",
        "stage": "new",
        "_rot_level": "fresh",
        "_rot_days": 0,
        "updated_at": "2026-09-10T00:00:00+00:00",
    }
    index = ActivityIndex(agent_written_deals={"deal"})

    candidate, evidence = assess_rot(deal, index, NOW, RotCalibration(5))

    assert candidate
    assert evidence["agent_write_suppressed_platform_signal"] is True


def test_rot_boundary_comes_from_observed_deal_levels():
    deals = [
        {"id": "fresh", "_rot_level": "fresh", "updated_at": "2026-09-20T00:00:00+00:00"},
        {"id": "flagged", "_rot_level": "attention", "updated_at": "2026-09-16T00:00:00+00:00"},
    ]

    calibration = calibrate_rot_boundary(deals, ActivityIndex(), NOW)

    assert calibration.boundary_days == 5
    assert calibration.lower == 5
    assert calibration.upper == 8


def test_unknown_stage_needs_human_review_instead_of_a_guess():
    # A stage value that doesn't match any known Pipeline stage.
    deal = {"id": "deal", "title": "Deal", "stage": "Bench Vice 30mm (Kg)"}

    status, proposal, _ = next_action(Pages(), deal, ExecutionMode.PROPOSE, "unknown-stage")

    assert status is ActionStatus.NEEDS_HUMAN_REVIEW
    assert proposal is None