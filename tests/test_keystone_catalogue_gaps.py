"""Regression tests for awkward corners of the Pipeline data model."""
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

from .keystone_support import NOW, activity
from .keystone_support import deal as make_deal

THRESHOLD = 30
LONG_AGO = "2026-07-01T12:00:00+00:00"


class RecordsClient(MCPClient):
    """In-memory client that only supports paged `list`, with optional denied entities."""

    def __init__(self, records_by_entity, denied=()):
        self.records_by_entity = {entity: list(rows) for entity, rows in records_by_entity.items()}
        self.denied = set(denied)

    async def call_tool(self, name, arguments):
        assert name == "list"
        entity = arguments["entity"]
        if entity in self.denied:
            raise PermissionDeniedError("list", entity, "crm", raw={"message": "denied"})

        rows = self.records_by_entity.get(entity, [])
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 20)
        return {"records": rows[offset:offset + limit], "total": len(rows)}


def make_activity(**kwargs):
    kwargs.setdefault("type", "meeting")
    return activity(**kwargs)


def build_index(*activities):
    """Build the index the real way, through load_activity_index."""
    client = RecordsClient({"Activity": activities})
    return asyncio.run(load_activity_index(client, now=NOW))


def assess(deal, index):
    return assess_rot(deal, index, NOW, RotCalibration(5), threshold_days=THRESHOLD)


# --- contact linkage and age --------------------------------------------------

def test_recent_deal_creation_does_not_hide_old_contact():
    index = build_index(make_activity(id="old-contact", deal_id="deal", due_date="2026-08-09"))
    deal = make_deal(created_at="2026-09-23T12:00:00+00:00")

    candidate, evidence = assess(deal, index)

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["age_basis"] == "contact"


def test_activity_on_sibling_deal_does_not_refresh_this_deal():
    index = build_index(
        make_activity(id="old-a", deal_id="deal-a", due_date="2026-08-09"),
        make_activity(id="recent-b", deal_id="deal-b", due_date="2026-09-18", type="call"),
    )

    candidate, evidence = assess(make_deal("deal-a"), index)

    assert candidate
    assert evidence["independent_rot_days"] == 50
    assert evidence["last_contact"] == "2026-08-09"


@pytest.mark.parametrize("created_at, days, rotting", [
    ("2026-08-30T12:00:00+00:00", 29, False),
    ("2026-08-29T12:00:00+00:00", 30, False),
    ("2026-08-28T12:00:00+00:00", 31, True),
])
def test_never_contacted_deal_ages_from_creation_date(created_at, days, rotting):
    candidate, evidence = assess(make_deal(created_at=created_at), build_index())

    assert candidate is rotting
    assert evidence["independent_rot_days"] == days
    assert evidence["age_basis"] == "deal_opened"


def test_completed_contact_with_agent_marker_still_counts():
    index = build_index(
        make_activity(id="human-old", deal_id="deal", due_date="2026-08-09"),
        make_activity(
            id="agent-done", deal_id="deal", due_date="2026-09-25", done=1,
            description="created_by=pipeline_agent run=keystone-regression",
        ),
    )

    candidate, evidence = assess(make_deal(), index)

    # The completed meeting is real contact, so contact age drops to 3 days,
    # but the platform-mirror safeguard still keeps the deal on the rot list.
    assert candidate
    assert evidence["independent_rot_days"] == 3
    assert evidence["last_contact"] == "2026-09-25"
    assert evidence["agent_write_suppressed_platform_signal"] is True


def test_untouched_deal_does_not_trip_agent_write_detection():
    deal = make_deal(created_at="2026-09-28T12:00:00+00:00", updated_at="2026-08-01T12:00:00+00:00")

    candidate, evidence = assess(deal, build_index())

    assert not candidate
    assert evidence["platform_mirror_days"] is None
    assert "agent_write_suppressed_platform_signal" not in evidence


# --- uncalled customers -------------------------------------------------------

@pytest.mark.parametrize("call_date, uncalled", [
    ("2026-08-30", False),
    ("2026-08-29", False),  # exactly 30 days is still called
    ("2026-08-28", True),
])
def test_uncalled_needs_more_than_threshold_days_since_last_call(call_date, uncalled):
    deals = [make_deal(created_at=LONG_AGO)]
    index = build_index(make_activity(id="call", deal_id="deal", due_date=call_date, type="call"))

    rows = find_uncalled(deals, index, NOW, THRESHOLD)

    assert bool(rows) is uncalled
    if rows:
        assert rows[0].days_since == 31


def test_new_never_called_deal_is_reported_as_too_new():
    deals = [make_deal(created_at="2026-09-20T12:00:00+00:00")]
    too_new = []

    rows = find_uncalled(deals, build_index(), NOW, THRESHOLD, too_new=too_new)

    assert rows == []
    assert len(too_new) == 1
    assert too_new[0].days_since == 8


# --- party roles (supplier scope) ---------------------------------------------

def test_party_roles_only_include_active_roles():
    client = RecordsClient({"Party": [
        {"id": "supplier", "roles": [{"role": "supplier", "active": True}]},
        {"id": "customer", "roles": [
            {"role": "supplier", "active": False},
            {"role": "customer", "active": True},
        ]},
    ]})

    roles, note = asyncio.run(load_party_roles(client))

    assert note == ""
    assert roles == {"supplier": {"supplier"}, "customer": {"customer"}}


def test_unreadable_party_gives_unknown_roles():
    client = RecordsClient({}, denied={"Party"})

    roles, note = asyncio.run(load_party_roles(client))

    assert roles is None
    assert note == "Party is not readable from this seat"