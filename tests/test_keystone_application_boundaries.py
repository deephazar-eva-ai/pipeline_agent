"""Keystone boundary tests for contact age and the platform rot signal."""
import pytest

from pipeline_agent.agent.workflow import RotCalibration, assess_rot, platform_mirror_days

from .keystone_support import NOW, activity, build_index, deal

NOW_TEXT = NOW.isoformat()
THRESHOLD = 30
AGENT_MARKER = "created_by=pipeline_agent run=keystone-boundary"


def make_activity(**kwargs):
    kwargs.setdefault("created_at", NOW_TEXT)
    return activity(**kwargs)


def make_deal(deal_id="deal", **kwargs):
    # Fresh by default: created and updated now, no platform rot.
    kwargs.setdefault("created_at", NOW_TEXT)
    kwargs.setdefault("updated_at", NOW_TEXT)
    kwargs.setdefault("rot_days", 0)
    return deal(deal_id, **kwargs)


def check_rot(deal, index):
    return assess_rot(deal, index, NOW, RotCalibration(5), threshold_days=THRESHOLD)


# --- platform and contact boundaries ------------------------------------------

@pytest.mark.parametrize("rot_level, flagged", [
    ("fresh", False),
    ("none", False),
    (None, False),
    ("", False),
    ("warning", True),
])
def test_only_non_fresh_rot_levels_count_as_platform_flags(rot_level, flagged):
    candidate, evidence = check_rot(make_deal(level=rot_level), build_index())

    assert evidence["platform_flags"] is flagged
    assert candidate is flagged


@pytest.mark.parametrize("due_date, days, rotting", [
    ("2026-08-30", 29, False),
    ("2026-08-29", 30, False),  # exactly at the threshold is still fine
    ("2026-08-28", 31, True),
])
def test_contact_age_uses_the_actual_activity_date(due_date, days, rotting):
    deal = make_deal()
    index = build_index(make_activity(id="seeded-history", deal_id="deal", due_date=due_date))

    candidate, evidence = check_rot(deal, index)

    assert platform_mirror_days(deal, index, NOW) == 0
    assert evidence["independent_rot_days"] == days
    assert candidate is rotting


def test_activity_on_another_deal_does_not_count_for_this_one():
    index = build_index(
        make_activity(id="old-target", deal_id="target", due_date="2026-08-09"),
        # Same customer, more recent, but linked to a different deal.
        make_activity(id="recent-sibling", deal_id="sibling", party_id="party", due_date="2026-09-18"),
    )

    candidate, evidence = check_rot(make_deal("target"), index)

    assert index.last_unlinked_contact_by_party == {}
    assert evidence["last_contact"] == "2026-08-09"
    assert evidence["independent_rot_days"] == 50
    assert candidate


def test_seeded_activity_can_look_recent_to_platform_but_old_by_contact():
    deal = make_deal(updated_at="2026-07-20T12:00:00+00:00")
    index = build_index(make_activity(id="seeded-history", deal_id="deal", due_date="2026-07-30"))

    candidate, evidence = check_rot(deal, index)

    assert platform_mirror_days(deal, index, NOW) == 0
    assert evidence["independent_rot_days"] == 60
    assert evidence["platform_flags"] is False
    assert candidate


# --- agent-write protection ---------------------------------------------------

@pytest.mark.parametrize("updated_at, mirror_days, rotting", [
    ("2026-09-24T12:00:00+00:00", 4, False),
    ("2026-09-23T12:00:00+00:00", 5, True),  # calibrated boundary is inclusive
    ("2026-09-22T12:00:00+00:00", 6, True),
])
def test_agent_activity_falls_back_to_calibrated_boundary(updated_at, mirror_days, rotting):
    # The agent's own open task would make the deal look freshly touched to
    # the platform, so rot has to come from the calibrated mirror instead.
    index = build_index(make_activity(
        id="agent-task", deal_id="deal", due_date="2026-09-27", done=0, type="task",
        description=AGENT_MARKER,
    ))

    candidate, evidence = check_rot(make_deal(updated_at=updated_at), index)

    assert evidence["platform_mirror_days"] == mirror_days
    assert candidate is rotting
    assert ("agent_write_suppressed_platform_signal" in evidence) is rotting