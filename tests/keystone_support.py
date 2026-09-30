"""Shared fixtures for Keystone regression tests.

These builders describe the stable CRM record shape used by several tests.
Test modules keep only scenario-specific records and assertions.
"""

import asyncio
import datetime as dt

from pipeline_agent.agent.workflow import load_activity_index
from pipeline_agent.mcp.client import MCPClient


NOW = dt.datetime(2026, 9, 28, 12, tzinfo=dt.timezone.utc)
DEFAULT_TIMESTAMP = "2026-09-16T12:00:00+00:00"


class ActivityClient(MCPClient):
    """Read-only, paginated Activity.list fixture."""

    def __init__(self, activities=()):
        self.activities = list(activities)

    async def call_tool(self, name, arguments):
        assert name == "list"
        assert arguments["entity"] == "Activity"
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 20)
        return {
            "records": self.activities[offset : offset + limit],
            "total": len(self.activities),
        }


def activity(*, id, due_date, deal_id=None, party_id=None, done=1, type="call",
             created_at=DEFAULT_TIMESTAMP, description="", **extra):
    return {
        "id": id,
        "due_date": due_date,
        "deal_id": deal_id,
        "party_id": party_id,
        "done": done,
        "type": type,
        "created_at": created_at,
        "description": description,
        **extra,
    }


def deal(id="deal", *, party_id="party", level="fresh", stage="qualification",
         created_at=DEFAULT_TIMESTAMP, updated_at=DEFAULT_TIMESTAMP, rot_days=12, **extra):
    return {
        "id": id,
        "party_id": party_id,
        "stage": stage,
        "_rot_level": level,
        "_rot_days": rot_days,
        "created_at": created_at,
        "updated_at": updated_at,
        **extra,
    }


def build_index(*activities, now=NOW):
    return asyncio.run(load_activity_index(ActivityClient(activities), now=now))
