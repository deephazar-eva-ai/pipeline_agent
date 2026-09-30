
"""Regression tests for Pipeline data-model edge cases."""

import asyncio

import pytest

from pipeline_agent.agent.workflow import (
    RotCalibration,
    assess_rot,
    find_uncalled,
    load_activity_index,
    load_party_roles,
)
from pipeline_agent.mcp.client import MCPClient, PermissionDeniedError

from .keystone_support import NOW, activity, deal as make_deal



class RecordsClient(MCPClient):
    def __init__(self, records_by_entity, *, denied=()):
        self.records_by_entity = {
            entity: list(records)
            for entity, records in records_by_entity.items()
        }
        self.denied = set(denied)

    async def call_tool(self, name, arguments):
        assert name == "list"

        entity = arguments["entity"]

        if entity in self.denied:
            raise PermissionDeniedError(
                "list",
                entity,
                "crm",
                raw={"message": "denied"},
            )

        rows = self.records_by_entity.get(entity, [])
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 20)

        return {
            "records": rows[offset : offset + limit],
            "total": len(rows),
        }


def make_activity(**kwargs):
    kwargs.setdefault("type", "meeting")
    return activity(**kwargs)


def build_index(*activities):
    client = RecordsClient(
        {"Activity": activities}
    )

    return asyncio.run(
        load_activity_index(
            client,
            now=NOW,
        )
    )


def assess(deal, index):
    return assess_rot(
        deal,
        index,
        NOW,
        RotCalibration(5),
        threshold_days=30,
    )


# Linkage and age quality

def test_old_contact_is_not_replaced_by_recent_deal_creation():
    index = build_index(
        make_activity(
            id="old-contact",
            deal_id="deal",
            due_date="2026-08-09",
        )
    )

    deal = make_deal(
        created_at="2026-09-23T12:00:00+00:00",
    )

    candidate, evidence = assess(deal, index)

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["age_basis"] == "contact"


def test_activity_on_sibling_deal_does_not_refresh_target_deal():
    index = build_index(
        make_activity(
            id="old-a",
            deal_id="deal-a",
            due_date="2026-08-09",
        ),
        make_activity(
            id="recent-b",
            deal_id="deal-b",
            due_date="2026-09-18",
            type="call",
        ),
    )

    candidate, evidence = assess(
        make_deal("deal-a"),
        index,
    )

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["last_contact"] == "2026-08-09"


@pytest.mark.parametrize(
    ("created_at", "expected_days", "expected_candidate"),
    [
        ("2026-08-30T12:00:00+00:00", 29, False),
        ("2026-08-29T12:00:00+00:00", 30, True),
        ("2026-08-28T12:00:00+00:00", 31, True),
    ],
)
def test_never_contacted_deal_uses_creation_date_for_rot_boundary(
    created_at,
    expected_days,
    expected_candidate,
):
    candidate, evidence = assess(
        make_deal(created_at=created_at),
        build_index(),
    )

    assert candidate is expected_candidate
    assert evidence["independent_rot_days"] == expected_days
    assert evidence["age_basis"] == "deal_opened"


def test_completed_agent_activity_does_not_refresh_contact_history():
    index = build_index(
        make_activity(
            id="human-old",
            deal_id="deal",
            due_date="2026-08-09",
        ),
        make_activity(
            id="agent-done",
            deal_id="deal",
            due_date="2026-09-25",
            done=1,
            description=(
                "created_by=pipeline_agent "
                "run=keystone-regression"
            ),
        ),
    )

    candidate, evidence = assess(
        make_deal(),
        index,
    )

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["last_contact"] == "2026-08-09"


def test_untouched_deal_does_not_trigger_agent_write_detection():
    deal = make_deal(
        created_at="2026-09-28T12:00:00+00:00",
        updated_at="2026-08-01T12:00:00+00:00",
    )

    candidate, evidence = assess(
        deal,
        build_index(),
    )

    assert not candidate
    assert evidence["platform_mirror_days"] is None
    assert "agent_write_suppressed_platform_signal" not in evidence


@pytest.mark.parametrize(
    ("call_date", "expected_uncalled"),
    [
        ("2026-08-30", False),
        ("2026-08-29", True),
        ("2026-08-28", True),
    ],
)
def test_uncalled_boundary_is_inclusive_for_completed_calls(
    call_date,
    expected_uncalled,
):
    deals = [
        make_deal(
            created_at="2026-07-01T12:00:00+00:00",
        )
    ]

    index = build_index(
        make_activity(
            id="call",
            deal_id="deal",
            due_date=call_date,
            type="call",
        )
    )

    rows = find_uncalled(
        deals,
        index,
        NOW,
        30,
    )

    assert bool(rows) is expected_uncalled

    if rows:
        assert rows[0].days_since in (30, 31)


def test_recently_created_never_called_deal_is_reported_separately():
    too_new = []

    deals = [
        make_deal(
            created_at="2026-09-20T12:00:00+00:00",
        )
    ]

    rows = find_uncalled(
        deals,
        build_index(),
        NOW,
        30,
        too_new=too_new,
    )

    assert rows == []
    assert len(too_new) == 1
    assert too_new[0].days_since == 8


# Supplier scope fallback

def test_party_roles_include_only_active_roles():
    client = RecordsClient(
        {
            "Party": [
                {
                    "id": "supplier",
                    "roles": [
                        {
                            "role": "supplier",
                            "active": True,
                        }
                    ],
                },
                {
                    "id": "customer",
                    "roles": [
                        {
                            "role": "supplier",
                            "active": False,
                        },
                        {
                            "role": "customer",
                            "active": True,
                        },
                    ],
                },
            ]
        }
    )

    roles, note = asyncio.run(
        load_party_roles(client)
    )

    assert note == ""
    assert roles == {
        "supplier": {"supplier"},
        "customer": {"customer"},
    }


def test_party_role_access_failure_returns_unknown_roles():
    client = RecordsClient(
        {},
        denied={"Party"},
    )

    roles, note = asyncio.run(
        load_party_roles(client)
    )

    assert roles is None
    assert note == "Party is not readable from this seat"
