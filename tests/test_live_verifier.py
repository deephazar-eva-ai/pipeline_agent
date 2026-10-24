"""
Live check of a canonical propose-only run against the real CRM.

The rules are re-implemented here on purpose instead of importing them from
workflow: a check that uses the agent's own helpers can only show that the
code agrees with itself.

Skipped unless you opt in:

    RUN_LIVE_VERIFIER=1 PYTHONPATH=src .venv/bin/pytest tests/test_live_verifier.py -q -s

The run artifact is left in OUTPUT_DIR (default: runs) as evidence.
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipeline_agent import runner
from pipeline_agent.config import Settings
from pipeline_agent.mcp.real_client import RealMCPClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_VERIFIER") != "1",
    reason="set RUN_LIVE_VERIFIER=1 to run against the shared CRM",
)

CONTACT_TYPES = {"call", "meeting", "email"}
COMPANY_TIMEZONES = {"keystone": "America/New_York", "suryodaya": "Asia/Kolkata"}
AGENT_MARKER = "created_by=pipeline_agent"
PAGE_SIZE = 1000
UNCALLED_DAYS = 30
PLATFORM_FRESH_LEVELS = {"fresh", "none", ""}
# Matches tags like "[team07-test IT-01]" or "[Team 3 probe]".
TEST_FIXTURE_TAG = re.compile(r"\[team\s*\d+[-_ ]?(?:test|probe)", re.IGNORECASE)


# --- our own reading of the rules ---------------------------------------------

def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # Naive timestamps from the platform are UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def company_day(value, zone):
    # A bare date is already a company date; a timestamp needs converting.
    text = str(value or "")
    if len(text) <= 10:
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            return None
    stamp = parse_datetime(text)
    return stamp.astimezone(zone).date() if stamp else None


def is_done(activity):
    # The API has returned both booleans and 0/1 here.
    return activity.get("done") is True or activity.get("done") == 1


def is_contact(activity):
    if not is_done(activity):
        return False
    if str(activity.get("type") or "").lower() not in CONTACT_TYPES:
        return False
    if activity.get("cancelled") is True:
        return False
    if str(activity.get("outcome") or "").strip().lower().startswith("cancelled"):
        return False
    return AGENT_MARKER not in str(activity.get("description") or "")


def is_call(activity):
    return is_contact(activity) and str(activity.get("type") or "").lower() == "call"


def is_open(deal):
    return not str(deal.get("stage") or "").lower().startswith("closed")


def platform_says_rotting(deal):
    # The platform's own rot level on the Deal, e.g. "attention".
    level = deal.get("_rot_level")
    return isinstance(level, str) and level.lower() not in PLATFORM_FRESH_LEVELS


def roles_by_party(parties):
    roles = {}
    for party in parties:
        roles[str(party.get("id"))] = {
            str(r.get("role")).lower()
            for r in party.get("roles") or []
            if isinstance(r, dict) and r.get("active", True) and r.get("role")
        }
    return roles


def is_non_sales(deal, roles):
    # Supplier or employee, and not also a customer or prospect.
    party_roles = roles.get(str(deal.get("party_id")), set())
    if not party_roles:
        return False
    return bool(party_roles & {"supplier", "employee"}) and not party_roles & {"customer", "prospect"}


def party_names(parties):
    return {str(party.get("id")): str(party.get("name") or "") for party in parties}


def is_test_fixture(deal, names):
    # Decided from the deal title and party name in the CRM, not from anything the agent says.
    title = str(deal.get("title") or "")
    party = names.get(str(deal.get("party_id")), "")
    return bool(TEST_FIXTURE_TAG.search(title) or TEST_FIXTURE_TAG.search(party))


def latest_contact_day(deal, activities, today, zone):
    days = []
    for activity in activities:
        on_deal = activity.get("deal_id") == deal.get("id")
        on_party_only = not activity.get("deal_id") and activity.get("party_id") == deal.get("party_id")
        if not (on_deal or on_party_only) or not is_contact(activity):
            continue
        day = company_day(activity.get("due_date"), zone)
        if day is not None and day <= today:
            days.append(day)
    return max(days, default=None)


def rotting_days(deal, activities, today, zone):
    # From the last real contact, or from when the deal was opened.
    since = latest_contact_day(deal, activities, today, zone) or company_day(deal.get("created_at"), zone)
    if since is None or since > today:
        return None
    return (today - since).days


# --- reading the CRM ----------------------------------------------------------

async def read_all(client, entity):
    rows, offset = [], 0
    while True:
        response = await client.list_(entity, limit=PAGE_SIZE, offset=offset, sort_order="asc")
        page = response.get("records") if isinstance(response, dict) else None
        if not isinstance(page, list):
            raise AssertionError(f"{entity}.list did not return records: {response!r}")
        rows.extend(r for r in page if isinstance(r, dict))
        offset += len(page)
        total = response.get("total")
        if not page or len(page) < PAGE_SIZE or (isinstance(total, int) and offset >= total):
            return rows


async def snapshot(client):
    return {entity: await read_all(client, entity)
            for entity in ("CRMPreferences", "Deal", "Activity", "Party")}


def latest_agent_update(rows):
    stamps = [str(r["updated_at"]) for r in rows
              if AGENT_MARKER in str(r.get("description") or "") and r.get("updated_at")]
    return max(stamps, default=None)


def changed_since(rows, started):
    return [r for r in rows if (stamp := parse_datetime(r.get("updated_at"))) and stamp > started]


def volatile_deal_ids(snap, started):
    """Deals someone else may have touched during our run, so we can't compare them exactly."""
    deals = {str(r.get("id")) for r in changed_since(snap["Deal"], started)}
    parties = set()
    for activity in changed_since(snap["Activity"], started):
        if activity.get("deal_id"):
            deals.add(str(activity["deal_id"]))
        if activity.get("party_id"):
            parties.add(str(activity["party_id"]))
    # A party-only Activity can affect every deal for that party.
    deals.update(str(d.get("id")) for d in snap["Deal"] if str(d.get("party_id")) in parties)
    return deals


# --- running the agent --------------------------------------------------------

def canonical_args():
    # Same as the documented read-only CLI run, without shelling out.
    return argparse.Namespace(
        mode="live", task="canonical", exec_mode="propose", limit=None, deal_id=[],
        request=runner.CANONICAL_REQUEST, contact_date=None, contact_type="call", outcome=None,
        max_steps=12, token_budget=None, allow_write=[], max_writes=3, update_id=[],
    )


def run_canonical(output_dir):
    """Run the canonical task once and return its artifact directory."""
    before = set(output_dir.glob("*__canonical__live__*"))
    assert asyncio.run(runner.main_async(canonical_args())) == 0

    new_dirs = set(output_dir.glob("*__canonical__live__*")) - before
    assert len(new_dirs) == 1, f"expected one canonical artifact, found {sorted(map(str, new_dirs))}"
    run_dir = new_dirs.pop()
    assert json.loads((run_dir / "status.json").read_text())["status"] == "completed"
    return run_dir


def counts(snap):
    return {e: {"count": len(snap[e]), "latest_team07_update": latest_agent_update(snap[e])}
            for e in ("Deal", "Activity")}


# --- the check ----------------------------------------------------------------

def test_live_canonical_answer_matches_the_crm():
    # Only loads .env on this opt-in path; it never overrides env vars already set.
    runner._load_dotenv()
    settings = Settings.from_env()
    settings.require_mcp_credentials()
    tz_name = os.environ.get("PIPELINE_TIMEZONE") or COMPANY_TIMEZONES.get(settings.tenant)
    assert tz_name, f"no company timezone known for tenant {settings.tenant!r}"
    zone = ZoneInfo(tz_name)

    client = RealMCPClient(settings)
    started = dt.datetime.now(dt.timezone.utc)
    before = asyncio.run(snapshot(client))

    run_dir = run_canonical(Path(settings.output_dir))
    result = json.loads((run_dir / "result.json").read_text())
    answer = result.get("final_answer")
    # Only the structured fields are checked, never the summary text.
    assert isinstance(answer, dict), "canonical run did not produce a structured answer"
    assert result.get("created_record_ids") == []
    assert result.get("updated_record_ids") == []

    after = asyncio.run(snapshot(client))

    # Propose mode must not have changed any Deal or Activity.
    for entity in ("Deal", "Activity"):
        assert len(after[entity]) == len(before[entity]), f"{entity} count changed during propose run"
        assert latest_agent_update(after[entity]) == latest_agent_update(before[entity]), (
            f"latest team07 {entity} update changed during propose run"
        )

    analyzed_at = parse_datetime(answer.get("analyzed_at"))
    assert analyzed_at is not None, "answer has no usable analyzed_at"
    today = analyzed_at.astimezone(zone).date()
    prefs = after["CRMPreferences"]
    assert prefs and prefs[0].get("deal_rot_days") is not None
    threshold = int(prefs[0]["deal_rot_days"])

    volatile = volatile_deal_ids(after, started)
    roles = roles_by_party(after["Party"])
    names = party_names(after["Party"])

    # Rotting deals: recompute and compare exactly, ignoring volatile ones.
    # A deal counts if it's past the threshold by contact age, or if the
    # platform already flags it (Suryodaya's "attention" can fire earlier).
    # The third signal in the contract can't be checked from here.
    expected = {}
    platform_flagged = []
    test_fixture_ids = []
    for deal in after["Deal"]:
        deal_id = str(deal.get("id"))
        if not deal.get("id") or not is_open(deal) or is_non_sales(deal, roles) or deal_id in volatile:
            continue
        if is_test_fixture(deal, names):
            # Tagged test deals are left out of the answer, so leave them out here too.
            test_fixture_ids.append(deal_id)
            continue
        age = rotting_days(deal, after["Activity"], today, zone)
        flagged = platform_says_rotting(deal)
        if flagged:
            platform_flagged.append(deal_id)
        if flagged or (age is not None and age > threshold):
            expected[deal_id] = age

    reported = {str(r["deal_id"]): r for r in answer.get("deals", [])
                if isinstance(r, dict) and r.get("deal_id")}
    stable = set(reported) - volatile
    assert stable == set(expected), (
        f"rotting deals differ: missing={sorted(set(expected) - stable)}, "
        f"unexpected={sorted(stable - set(expected))}"
    )
    for deal_id in stable:
        evidence = reported[deal_id].get("rot_evidence") or {}
        assert evidence.get("independent_rot_days") == expected[deal_id], f"{deal_id}: wrong days_since"

    # Uncalled customers: only check that each reported one really is uncalled.
    # Not an exact match, since a brand-new customer hasn't had 30 days yet.
    calls = {}
    for activity in after["Activity"]:
        if not is_call(activity) or not activity.get("party_id"):
            continue
        day = company_day(activity.get("due_date"), zone)
        if day is not None and day <= today:
            calls.setdefault(str(activity["party_id"]), []).append(day)
    for row in answer.get("uncalled", []):
        assert isinstance(row, dict) and row.get("party_id")
        last_call = max(calls.get(str(row["party_id"]), []), default=None)
        assert last_call is None or (today - last_call).days > UNCALLED_DAYS, (
            f"{row['party_id']} was called within the last {UNCALLED_DAYS} days ({last_call})"
        )

    # Spot-check a few deals the agent left out, so the exact match above
    # can't pass just because both sides came up empty.
    skip = set(reported) | volatile
    omitted = [d for d in after["Deal"]
               if str(d.get("id")) not in skip and not is_test_fixture(d, names)]
    assert len(omitted) >= 3, "not enough stable omitted deals to spot-check"
    checked = []
    for deal in sorted(omitted, key=lambda d: str(d.get("id")))[:5]:
        checked.append(str(deal.get("id")))
        if is_open(deal) and not is_non_sales(deal, roles):
            assert not platform_says_rotting(deal), (
                f"agent left out platform-flagged deal {deal.get('id')} ({deal.get('_rot_level')!r})"
            )
            age = rotting_days(deal, after["Activity"], today, zone)
            assert age is None or age <= threshold, f"agent left out rotting deal {deal.get('id')}"

    # Save the verdict next to the run. IDs and counts only, no settings or token.
    (run_dir / "live_verifier.json").write_text(json.dumps({
        "verifier": "tests/test_live_verifier.py",
        "run_started_at": started.isoformat(),
        "company_timezone": tz_name,
        "company_today": today.isoformat(),
        "deal_rot_days": threshold,
        "before": counts(before),
        "after": counts(after),
        "volatile_deal_ids_excluded": sorted(volatile),
        "test_fixture_deal_ids_excluded": sorted(test_fixture_ids),
        "rotting_deal_ids": sorted(expected),
        "platform_flagged_rotting_deal_ids": sorted(platform_flagged),
        "days_since": expected,
        "uncalled_party_ids_checked": sorted(
            str(r["party_id"]) for r in answer.get("uncalled", []) if isinstance(r, dict)
        ),
        "spot_check_deal_ids": checked,
        "verdict": "pass",
    }, indent=2, sort_keys=True))