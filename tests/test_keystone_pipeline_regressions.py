"""Keystone regressions for contact history and rot calculation."""

import pytest

from pipeline_agent.agent.workflow import RotCalibration, assess_rot

from .keystone_support import NOW, activity as make_activity, build_index, deal as make_deal


def assess(deal, index):
    return assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )


# Contact-history rules

def test_backdated_contact_uses_activity_date_not_insert_date():
    index = build_index(
        make_activity(
            id="old-call",
            deal_id="deal",
            due_date="2025-09-24",
        )
    )

    candidate, evidence = assess(
        make_deal(),
        index,
    )

    assert index.last_contact_by_deal == {
        "deal": "2025-09-24"
    }
    assert index.last_touch_by_deal == {
        "deal": "2026-09-16T12:00:00+00:00"
    }

    assert candidate
    assert evidence["independent_rot_days"] == 369
    assert evidence["age_basis"] == "contact"
    assert evidence["platform_flags"] is False


def test_recent_deal_edit_does_not_refresh_contact_age():
    index = build_index(
        make_activity(
            id="old-meeting",
            deal_id="deal",
            due_date="2026-08-09",
            type="meeting",
        )
    )

    deal = make_deal(
        updated_at="2026-09-27T12:00:00+00:00",
    )

    candidate, evidence = assess(
        deal,
        index,
    )

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["age_basis"] == "contact"


@pytest.mark.parametrize(
    "rot_level",
    ["warning", "attention", "stale"],
)
# Platform signal vocabulary

def test_non_fresh_platform_levels_are_treated_as_rot_signals(rot_level):
    deal = make_deal(
        created_at="2026-09-28T12:00:00+00:00",
        updated_at="2026-09-28T12:00:00+00:00",
        level=rot_level,
    )

    candidate, evidence = assess(
        deal,
        build_index(),
    )

    assert candidate
    assert evidence["platform_flags"] is True
    assert evidence["independently_flags"] is False


def test_party_level_contact_is_used_when_activity_has_no_deal_id():
    index = build_index(
        make_activity(
            id="old-deal-contact",
            deal_id="deal",
            due_date="2026-08-09",
            type="email",
        ),
        make_activity(
            id="recent-party-contact",
            party_id="party",
            due_date="2026-09-18",
            type="email",
        ),
    )

    candidate, evidence = assess(
        make_deal(),
        index,
    )

    assert not candidate
    assert evidence["independent_rot_days"] == 10
    assert evidence["last_contact"] == "2026-09-18"


def test_open_and_future_activities_do_not_refresh_contact_history():
    index = build_index(
        make_activity(
            id="old-contact",
            deal_id="deal",
            due_date="2026-08-09",
            type="meeting",
        ),
        make_activity(
            id="open-task",
            deal_id="deal",
            due_date="2026-09-26",
            done=0,
            type="task",
        ),
        make_activity(
            id="future-call",
            deal_id="deal",
            due_date="2026-10-18",
            type="call",
        ),
    )

    candidate, evidence = assess(
        make_deal(),
        index,
    )

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["last_contact"] == "2026-08-09"
