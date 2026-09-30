"""Keystone boundaries for contact age and platform rot signals."""

import pytest

from pipeline_agent.agent.workflow import (
    RotCalibration,
    assess_rot,
    platform_mirror_days,
)

from .keystone_support import NOW, activity, build_index, deal


NOW_TEXT = NOW.isoformat()


def make_activity(**kwargs):
    kwargs.setdefault("created_at", NOW_TEXT)
    return activity(**kwargs)


def make_deal(id="deal", **kwargs):
    kwargs.setdefault("created_at", NOW_TEXT)
    kwargs.setdefault("updated_at", NOW_TEXT)
    kwargs.setdefault("rot_days", 0)
    return deal(id, **kwargs)


@pytest.mark.parametrize(
    ("rot_level", "expected_platform_flag"),
    [
        ("fresh", False),
        ("none", False),
        (None, False),
        ("", False),
        ("warning", True),
    ],
)
# Inclusive platform and contact boundaries

def test_only_non_fresh_rot_levels_are_platform_flags(
    rot_level,
    expected_platform_flag,
):


    candidate, evidence = assess_rot(
        make_deal(level=rot_level),
        build_index(),
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )

    assert evidence["platform_flags"] is expected_platform_flag
    assert candidate is expected_platform_flag


@pytest.mark.parametrize(
    ("due_date", "expected_days", "expected_candidate"),
    [
        ("2026-08-30", 29, False),
        ("2026-08-29", 30, True),
        ("2026-08-28", 31, True),
    ],
)
def test_contact_age_uses_the_actual_activity_date(
    due_date,
    expected_days,
    expected_candidate,
):
    index = build_index(
        make_activity(
            id="seeded-history",
            deal_id="deal",
            due_date=due_date,
        )
    )

    candidate, evidence = assess_rot(
        make_deal(),
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )

    assert platform_mirror_days(
        make_deal(),
        index,
        NOW,
    ) == 0

    assert evidence["independent_rot_days"] == expected_days
    assert candidate is expected_candidate


def test_activity_for_another_deal_does_not_count_as_target_contact():
    index = build_index(
        make_activity(
            id="old-target",
            deal_id="target",
            due_date="2026-08-09",
        ),
        make_activity(
            id="recent-sibling",
            deal_id="sibling",
            party_id="party",
            due_date="2026-09-18",
        ),
    )

    candidate, evidence = assess_rot(
        make_deal("target"),
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )

    assert index.last_unlinked_contact_by_party == {}
    assert evidence["last_contact"] == "2026-08-09"
    assert evidence["independent_rot_days"] == 50
    assert candidate


@pytest.mark.parametrize(
    ("updated_at", "expected_mirror_days", "expected_candidate"),
    [
        ("2026-09-24T12:00:00+00:00", 4, False),
        ("2026-09-23T12:00:00+00:00", 5, True),
        ("2026-09-22T12:00:00+00:00", 6, True),
    ],
)
# Agent-write protection

def test_agent_activity_uses_the_calibrated_rot_boundary(
    updated_at,
    expected_mirror_days,
    expected_candidate,
):
    index = build_index(
        make_activity(
            id="agent-task",
            deal_id="deal",
            due_date="2026-09-27",
            done=0,
            type="task",
            description=(
                "created_by=pipeline_agent "
                "run=keystone-boundary"
            ),
        )
    )

    deal = make_deal(
        updated_at=updated_at,
    )

    candidate, evidence = assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )

    assert evidence["platform_mirror_days"] == expected_mirror_days
    assert candidate is expected_candidate

    has_suppressed_signal = (
        "agent_write_suppressed_platform_signal" in evidence
    )
    assert has_suppressed_signal is expected_candidate


def test_seeded_activity_can_have_recent_platform_age_but_old_contact_age():
    index = build_index(
        make_activity(
            id="seeded-history",
            deal_id="deal",
            due_date="2026-07-30",
        )
    )

    deal = make_deal(
        updated_at="2026-07-20T12:00:00+00:00",
    )

    candidate, evidence = assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )

    assert platform_mirror_days(
        deal,
        index,
        NOW,
    ) == 0

    assert evidence["independent_rot_days"] == 60
    assert evidence["platform_flags"] is False
    assert candidate
