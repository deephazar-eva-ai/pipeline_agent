"""
Regression tests for the IT-01 Run 3 fixes.

Everything runs against in-memory clients, so none of this reads or writes
the shared CRM.
"""
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline_agent.agent import contact_status, workflow
from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer, ContactStatus
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    RotCalibration,
    assess_rot,
    classify_contact,
    contact_evidence_issues,
    days_since,
    find_uncalled,
    is_contact,
    load_activity_index,
    local_day,
)
from pipeline_agent.harness.loop import WritePolicy, write_block_reason
from pipeline_agent.mcp.stub_client import StubMCPClient

from .keystone_support import NOW, ActivityClient, activity, build_index, deal

# it01.py lives outside the package, so put its folder on the path first.
IT01_DIR = Path(__file__).resolve().parents[1] / "tasks" / "it01"
sys.path.insert(0, str(IT01_DIR))
import it01  # noqa: E402

THRESHOLD = 30
MARKER = "created_by=pipeline_agent run=run-3"
MARKER_ISSUE = "agent_marker_on_completed_contact"
NEW_YORK = dt.timezone(-dt.timedelta(hours=4))
NY_EVENING = dt.datetime(2026, 10, 11, 22, tzinfo=NEW_YORK)
TAG = "[team07-test IT-01]"


def run(coro):
    return asyncio.run(coro)


def returns(value):
    async def fake(*_args, **_kwargs):
        return value
    return fake


def issue_codes(issues):
    return [i["code"] for i in issues]


def index_for(deal_party, *rows):
    """Build the index through load_activity_index so link checks run."""
    return run(load_activity_index(ActivityClient(list(rows)), now=NOW, deal_party=deal_party))


def stub_client(activities, deals=None):
    """StubMCPClient with its activity (and optionally deal) rows swapped out."""
    client = StubMCPClient()
    client._activities = activities
    if deals is not None:
        client._deals = deals
    return client


def canonical(client, run_id):
    return run(workflow.run_canonical_task(client, AgentRequest(text="x"), run_id=run_id))


# --- G-A: only completed calls, meetings and emails are contact ---------------

@pytest.mark.parametrize("type_", ["Call", "EMAIL", "mEeTiNg"])
def test_contact_types_match_case_insensitively(type_):
    assert is_contact({"type": type_, "outcome": None})
    assert not is_contact({"type": "task", "outcome": None})


def test_mixed_case_contacts_are_indexed_and_not_counted_as_noncontact():
    index = build_index(
        activity(id="call", deal_id="deal", party_id="party", due_date="2026-09-25", type="Call"),
        activity(id="email", deal_id="deal", party_id="party", due_date="2026-09-27", type="EMAIL"),
        activity(id="task", deal_id="deal", party_id="party", due_date="2026-09-28", type="task"),
    )

    assert index.last_contacted(deal()) == ("2026-09-27", "deal")
    # Only the task.
    assert index.noncontact_done == 1


@pytest.mark.parametrize("type_", [None, "", "lunch", "note", "task"])
def test_non_contact_types_give_no_contact_date(type_):
    index = build_index(activity(id="row", deal_id="deal", due_date="2026-09-27", type=type_))

    assert index.last_contacted(deal()) == (None, "none")
    assert index.noncontact_done == 1


def test_blank_types_are_noncontact_but_a_meeting_counts():
    index = build_index(
        activity(id="none", deal_id="deal", party_id="party", due_date="2026-09-25", type=None),
        activity(id="blank", deal_id="deal", party_id="party", due_date="2026-09-26", type=""),
        activity(id="meeting", deal_id="deal", party_id="party", due_date="2026-09-27", type="meeting"),
    )

    assert index.last_contacted(deal()) == ("2026-09-27", "deal")
    assert index.noncontact_done == 2


@pytest.mark.parametrize("outcome", ["cancelled", "Cancelled - no answer"])
def test_cancelled_call_is_not_contact(outcome):
    assert not is_contact({"type": "call", "outcome": outcome})
    assert is_contact({"type": "call", "outcome": None})
    assert is_contact({"type": "call", "outcome": "Completed"})


def test_summary_mentions_noncontacts_only_when_some_were_excluded():
    def stub_activity(id_, due_date, type_, **extra):
        return activity(id=id_, deal_id="stub-deal-needs-action", party_id="stub-party-silent",
                        due_date=due_date, type=type_, **extra)

    excluded = canonical(stub_client([
        stub_activity("task", "2026-10-01", "task"),
        stub_activity("cancelled", "2026-10-02", "call", outcome="Cancelled - no answer"),
    ]), "run3-noncontacts")
    included = canonical(stub_client([stub_activity("email", "2026-10-01", "email")]), "run3-contacts")

    assert ("2 completed task(s), cancelled call(s) or other non-contact activit(ies) "
            "were not counted as customer contact") in excluded.summary
    assert "completed task(s), cancelled call(s) or other non-contact" not in included.summary
    assert "0 completed" not in included.summary


# --- G-B: a configured threshold means "more than", calibration means ">=" ----

def test_calibrated_rot_boundary_is_still_inclusive():
    empty = ActivityIndex(contact_dates_indexed=True)
    at_boundary = deal(created_at="2026-09-23T12:00:00+00:00", rot_days=0)  # 5 days
    just_before = deal(created_at="2026-09-24T12:00:00+00:00", rot_days=0)  # 4 days

    rotting, _ = assess_rot(at_boundary, empty, NOW, RotCalibration(5), threshold_days=None)
    not_rotting, _ = assess_rot(just_before, empty, NOW, RotCalibration(5), threshold_days=None)

    assert rotting
    assert not not_rotting


def test_never_contacted_deal_and_party_only_flip_on_day_31():
    day_30 = deal("day-30", party_id="party-30", created_at="2026-08-29T12:00:00+00:00")
    day_31 = deal("day-31", party_id="party-31", created_at="2026-08-28T12:00:00+00:00")
    empty = ActivityIndex(contact_dates_indexed=True)

    day_30_rotting, _ = assess_rot(day_30, empty, NOW, RotCalibration(5), threshold_days=THRESHOLD)
    day_31_rotting, _ = assess_rot(day_31, empty, NOW, RotCalibration(5), threshold_days=THRESHOLD)
    uncalled = find_uncalled([day_30, day_31], empty, NOW, THRESHOLD)

    assert not day_30_rotting
    assert day_31_rotting
    assert [row.party_id for row in uncalled] == ["party-31"]


# --- G-C: calendar days are counted in the company's timezone -----------------
# Note the argument order: local_day(value, now) but days_since(now, value).

def test_naive_timestamp_is_read_as_utc():
    # 02:00 UTC on the 12th is still the evening of the 11th in New York.
    assert local_day("2026-10-12T02:00:00", NY_EVENING) == dt.date(2026, 10, 11)
    assert days_since(NY_EVENING, "2026-10-12T02:00:00") == 0


def test_date_only_value_is_the_company_day():
    assert local_day("2026-10-11", NY_EVENING) == dt.date(2026, 10, 11)
    assert days_since(NY_EVENING, "2026-10-11") == 0


def test_timestamp_offset_is_respected():
    # 07:30 at +05:30 is the same instant as 02:00 UTC.
    assert local_day("2026-10-12T07:30:00+05:30", NY_EVENING) == dt.date(2026, 10, 11)
    assert days_since(NY_EVENING, "2026-10-12T07:30:00+05:30") == 0
    assert local_day("2026-10-12T07:30:00+00:00", NY_EVENING) == dt.date(2026, 10, 12)


@pytest.mark.parametrize("value", ["", None, "not-a-date"])
def test_bad_dates_have_no_day_or_age(value):
    assert local_day(value, NOW) is None
    assert days_since(NOW, value) is None


def test_valid_date_has_a_day_and_age():
    assert local_day("2026-09-28", NOW) == dt.date(2026, 9, 28)
    assert days_since(NOW, "2026-09-28") == 0


def test_naive_now_does_not_pick_up_a_timezone():
    naive_now = dt.datetime(2026, 10, 11, 22)
    zoned_now = naive_now.replace(tzinfo=NEW_YORK)

    assert local_day("2026-10-12T02:00:00+00:00", naive_now) == dt.date(2026, 10, 12)
    assert local_day("2026-10-12T02:00:00+00:00", zoned_now) == dt.date(2026, 10, 11)


# --- G-D: agent-marked completed contact counts, but gets flagged -------------

def test_marker_without_run_id_is_contact_and_flagged():
    def call(**extra):
        return build_index(activity(
            id="call", deal_id="deal", party_id="party", due_date="2026-09-27",
            subject="Customer follow-up", **extra,
        ))

    marked = call(description="created_by=pipeline_agent")
    unmarked = call()

    assert marked.last_contacted(deal()) == ("2026-09-27", "deal")
    assert marked.marked_contacts[0]["marker_run"] is None
    assert issue_codes(contact_evidence_issues([deal()], marked)) == [MARKER_ISSUE]

    assert unmarked.last_contacted(deal()) == ("2026-09-27", "deal")
    assert contact_evidence_issues([deal()], unmarked) == []


def test_marked_completed_task_is_noncontact_with_no_issue():
    index = build_index(activity(
        id="task", deal_id="deal", party_id="party", due_date="2026-09-27", type="task",
        description=MARKER,
    ))

    assert index.noncontact_done == 1
    assert index.marked_contacts == []
    assert contact_evidence_issues([deal()], index) == []


def test_marked_open_call_is_neither_contact_nor_issue():
    index = build_index(activity(
        id="open", deal_id="deal", party_id="party", due_date="2026-09-27", done=0,
        description=MARKER,
    ))

    assert index.last_contacted(deal()) == (None, "none")
    assert index.marked_contacts == []
    assert index.noncontact_done == 0
    assert contact_evidence_issues([deal()], index) == []


def test_party_only_marked_contact_flags_that_partys_open_deals_only():
    deals = [
        deal("open-a", party_id="party"),
        deal("open-b", party_id="party"),
        deal("closed", party_id="party", stage="closed_lost"),
        deal("other-party", party_id="other"),
    ]

    def party_email(**extra):
        return build_index(activity(id="email", party_id="party", due_date="2026-09-27",
                                    type="email", **extra))

    marked = {(i["deal_id"], i["code"]) for i in contact_evidence_issues(deals, party_email(description=MARKER))}

    assert marked == {("open-a", MARKER_ISSUE), ("open-b", MARKER_ISSUE)}
    assert contact_evidence_issues(deals, party_email()) == []


# --- G-E: future-dated contact is not evidence --------------------------------

def test_past_contact_wins_over_newer_future_one():
    target = deal()
    index = build_index(
        activity(id="past", deal_id="deal", party_id="party", due_date="2026-09-10"),
        activity(id="future", deal_id="deal", party_id="party", due_date="2026-10-01"),
    )

    last, basis = index.last_contacted(target)
    status, _ = classify_contact(NOW, THRESHOLD, last, basis)

    assert (last, basis) == ("2026-09-10", "deal")
    assert status is ContactStatus.RECENTLY_CONTACTED
    assert contact_evidence_issues([target], index) == []


@pytest.mark.parametrize("due_date, expected_status, expected_issues", [
    ("2026-10-01", ContactStatus.INSUFFICIENT_EVIDENCE, ["insufficient_evidence"]),
    ("2026-09-27", ContactStatus.RECENTLY_CONTACTED, []),
], ids=["future", "past"])
def test_party_only_contact_dates_are_checked_too(due_date, expected_status, expected_issues):
    target = deal()
    index = build_index(activity(id="contact", party_id="party", due_date=due_date))

    last, basis = index.last_contacted(target)
    status, _ = classify_contact(NOW, THRESHOLD, last, basis)

    assert (last, basis) == (due_date, "party")
    assert status is expected_status
    assert issue_codes(contact_evidence_issues([target], index)) == expected_issues


def test_future_contact_on_closed_deal_is_not_an_issue():
    index = build_index(activity(id="future", deal_id="deal", party_id="party", due_date="2026-10-01"))

    assert contact_evidence_issues([deal(stage="closed_won")], index) == []


# --- C-R6: a contact linked to the wrong customer can't freshen a deal --------

def test_correct_or_missing_party_link_is_not_a_mismatch():
    correct = index_for({"deal": "party"},
                        activity(id="correct", deal_id="deal", party_id="party", due_date="2026-09-27"))
    missing = index_for({"deal": "party"},
                        activity(id="missing", deal_id="deal", due_date="2026-09-27"))

    assert correct.mismatched_links == []
    assert missing.mismatched_links == []


def test_mismatched_newer_contact_does_not_replace_old_one():
    index = index_for(
        {"deal": "party-a"},
        activity(id="old", deal_id="deal", party_id="party-a", due_date="2026-07-10"),
        activity(id="wrong", deal_id="deal", party_id="party-b", due_date="2026-09-27",
                 subject="Call for party B"),
    )

    assert index.last_contacted(deal(party_id="party-a")) == ("2026-07-10", "deal")
    assert index.mismatched_links == [{
        "activity_id": "wrong",
        "subject": "Call for party B",
        "deal_id": "deal",
        "activity_party_id": "party-b",
        "deal_party_id": "party-a",
    }]


@pytest.mark.parametrize("party_id, expected_subjects", [
    ("party", []),
    (None, []),
    ("other", ["Contact row"]),
], ids=["correct-party", "missing-party", "wrong-party"])
def test_canonical_answer_lists_only_real_mismatches(party_id, expected_subjects):
    stale_deal = deal(
        "deal", party_id="party", title="Renewal", level="attention", rot_days=31,
        created_at="2026-08-01T12:00:00+00:00", updated_at="2026-08-01T12:00:00+00:00",
    )
    contact = activity(id="contact", deal_id="deal", party_id=party_id, due_date="2026-09-27",
                       subject="Contact row")

    answer = canonical(stub_client([contact], deals=[stale_deal]), "run3-links")

    assert [row["subject"] for row in answer.mismatched_links] == expected_subjects
    # A missing party must never show up as a named customer.
    assert all(row["activity_party"] is not None for row in answer.mismatched_links)


# --- C-R5/C-R6: must_report only carries mandatory items ----------------------

REQUIRED_ISSUE = {"deal_id": "required", "deal": "Required", "code": "insufficient_evidence", "detail": "future"}
REQUIRED_LINE = "Required: insufficient_evidence - future"
NO_OWNER_ISSUE = {"deal_id": "owner", "deal": "Ownerless", "code": "no_owner", "detail": "missing owner"}


@pytest.fixture
def analyse(monkeypatch):
    """Run contact_status.analyse over an empty book with the given exclusions and issues."""
    def _analyse(excluded=(), issues=()):
        answer = CanonicalAnswer(
            deal_rot_days=THRESHOLD, analyzed_at=NOW.isoformat(), deals=[], summary="offline",
            excluded_deals=list(excluded), data_issues=list(issues),
        )
        monkeypatch.setattr(contact_status, "run_canonical_task", returns(answer))
        monkeypatch.setattr(contact_status, "load_deals", returns([]))
        monkeypatch.setattr(contact_status, "load_activity_index",
                            returns(ActivityIndex(contact_dates_indexed=True)))
        monkeypatch.setattr(contact_status.checks, "load_company_clock", returns((NOW, "UTC", "$")))
        monkeypatch.setattr(contact_status.checks, "load_context",
                            returns(SimpleNamespace(quotations=[], sales_orders=[])))
        return run(contact_status.analyse(ActivityClient()))
    return _analyse


def test_fixture_exclusions_collapse_to_one_line(analyse):
    fixtures = [{"deal_id": str(n), "title": f"fixture {n}", "reason": "test fixture"} for n in range(3)]
    ordinary = [{"deal_id": "ordinary", "title": "Ordinary deal", "reason": "supplier-side"}]

    assert analyse(excluded=fixtures)["must_report"] == ["3 open test-fixture deal(s) excluded"]
    assert analyse(excluded=ordinary)["must_report"] == [
        "Ordinary deal: excluded from both answers - supplier-side",
    ]


def test_must_report_is_capped_at_25_with_a_pager_line(analyse):
    issues = [
        {"deal_id": str(n), "deal": f"deal {n}", "code": "insufficient_evidence", "detail": "future"}
        for n in range(27)
    ]

    over = analyse(issues=issues)["must_report"]
    at_limit = analyse(issues=issues[:25])["must_report"]

    # 25 items plus the pager line.
    assert len(over) == 26
    assert over[0].startswith("deal 0:")
    assert over[24].startswith("deal 24:")
    assert over[-1] == "... and 2 more - page section data_issues"

    assert len(at_limit) == 25
    assert at_limit[0].startswith("deal 0:")
    assert at_limit[-1].startswith("deal 24:")
    assert not any(line.startswith("... and ") for line in at_limit)


def test_non_mandatory_codes_stay_out_of_must_report(analyse):
    owner_only = analyse(issues=[NO_OWNER_ISSUE])
    mixed = analyse(issues=[REQUIRED_ISSUE, NO_OWNER_ISSUE])

    assert owner_only["must_report"] == []
    view = contact_status.view({**owner_only, "deals": [], "data_issues": [], "late_orders": [], "uncalled": []})
    assert "must_report" not in view

    assert mixed["must_report"] == [REQUIRED_LINE]


@pytest.mark.parametrize("section", [None, "deals", "data_issues", "late_orders", "uncalled"])
def test_must_report_key_follows_content_in_every_view(analyse, section):
    arguments = {} if section is None else {"section": section}
    empty = analyse()
    populated = analyse(issues=[REQUIRED_ISSUE])

    # Absent rather than an empty list when there's nothing to report.
    assert "must_report" not in contact_status.view(empty, arguments)
    assert contact_status.view(populated, arguments)["must_report"] == [REQUIRED_LINE]


# --- G-G: the loop may not create a completed Activity ------------------------

def block_reason(arguments):
    policy = WritePolicy(entities=frozenset({"Activity"}), max_writes=1, run_id="run3")
    return write_block_reason(policy, "create", arguments, writes_done=0)


@pytest.mark.parametrize("arguments", [
    {"entity": "Activity", "done": True},
    {"entity": "Activity", "data": {"done": True}},
    {"entity": "Activity", "data": {"done": 1}},
    {"entity": "Activity", "data": {"done": "true"}},
], ids=["top-level", "nested", "int", "string"])
def test_completed_activity_create_is_blocked(arguments):
    reason = block_reason(arguments)

    assert reason is not None
    assert "may not create a completed Activity" in reason


@pytest.mark.parametrize("done", ["absent", False, 0, ""])
@pytest.mark.parametrize("nested", [False, True], ids=["top-level", "nested"])
def test_open_activity_create_is_allowed(done, nested):
    fields = {} if done == "absent" else {"done": done}
    arguments = {"entity": "Activity", "data": fields} if nested else {"entity": "Activity", **fields}

    assert block_reason(arguments) is None


# --- IT-01 cleanup ------------------------------------------------------------

class CleanupClient:
    """Just enough of RealMCPClient for it01.cleanup."""

    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    async def get(self, entity, record_id):
        assert entity == "Activity"
        return self.rows[record_id]

    async def update(self, entity, record_id, payload):
        assert entity == "Activity"
        self.updates.append((record_id, payload))
        self.rows[record_id].update(payload)

    async def list_tools(self):
        return []


def make_run_dir(tmp_path, created_ids):
    run_dir = tmp_path / "run"
    artifact = run_dir / "runner" / "attempt"
    artifact.mkdir(parents=True)
    (run_dir / "fixtures.json").write_text(json.dumps({"activities": {}, "deals": {}}))
    (artifact / "result.json").write_text(json.dumps({"created_record_ids": created_ids}))
    return run_dir


def row(id_, subject, done=False):
    return {"id": id_, "subject": subject, "done": done}


def test_cleanup_closes_only_open_tagged_agent_writes(tmp_path, capsys):
    client = CleanupClient({
        "open-tagged": row("open-tagged", f"{TAG} follow-up"),
        "open-tagged-second": row("open-tagged-second", f"{TAG} another follow-up"),
        "already-done": row("already-done", f"{TAG} complete", done=True),
        "open-untagged": row("open-untagged", "production follow-up"),
    })
    run_dir = make_run_dir(tmp_path, list(client.rows))

    run(it01.cleanup(client, run_dir, apply=True))

    assert [record_id for record_id, _ in client.updates] == ["open-tagged", "open-tagged-second"]
    assert client.rows["open-tagged"]["done"] is True
    assert client.rows["open-tagged-second"]["done"] is True
    assert client.rows["already-done"]["done"] is True
    # Listed in the artifact, but untagged, so it must be left alone.
    assert client.rows["open-untagged"]["done"] is False
    assert capsys.readouterr().out == (
        "closed agent-written Activity open-tagged\n"
        "closed agent-written Activity open-tagged-second\n"
    )


def test_cleanup_with_no_agent_writes_does_nothing(tmp_path, capsys):
    client = CleanupClient({})

    run(it01.cleanup(client, make_run_dir(tmp_path, []), apply=True))

    assert client.updates == []
    assert capsys.readouterr().out == ""