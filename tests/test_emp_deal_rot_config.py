"""Bug-hunt fixes from 2026-10-13: employee-only parties (31589ab3) and deal_rot_days range (66f96caa)."""
import asyncio

import pytest

from pipeline_agent.agent import contact_status, pipeline_checks, workflow
from pipeline_agent.agent.contract import AgentRequest, CanonicalAnswer
from pipeline_agent.agent.pipeline_checks import PipelineContext
from pipeline_agent.agent.workflow import ActivityIndex, RotThreshold
from pipeline_agent.mcp.client import MCPClient

from .keystone_support import NOW, ActivityClient, build_index, deal

OLD = "2026-07-01T12:00:00+00:00"
EMPLOYEE_LINE = "open deal(s) on the company's own employees excluded"


def run(coro):
    return asyncio.run(coro)


def returns(value):
    async def fake(*args, **kwargs):
        return value
    return fake


def old_deal(deal_id, party_id):
    return deal(deal_id, party_id=party_id, created_at=OLD)


@pytest.fixture
def canonical(monkeypatch):
    def _run(deals, roles, threshold=RotThreshold(30, True)):
        ctx = PipelineContext(
            today="2026-09-28",
            currency_symbol="$",
            pipelines={"sales": {"qualification"}},
            parties={d["party_id"]: {"id": d["party_id"], "name": d["party_id"]} for d in deals},
        )
        note = "" if roles is not None else "Party is not readable from this seat"

        monkeypatch.setattr(workflow, "load_rot_threshold", returns(threshold))
        monkeypatch.setattr(workflow, "load_deals", returns(deals))
        monkeypatch.setattr(workflow, "load_activity_index", returns(build_index()))
        monkeypatch.setattr(workflow, "load_party_roles", returns((roles, note)))
        monkeypatch.setattr(workflow.checks, "load_company_clock", returns((NOW, "UTC", "$")))
        monkeypatch.setattr(workflow.checks, "load_context", returns(ctx))

        return run(workflow.run_canonical_task(ActivityClient(), AgentRequest(text="x"), run_id="bh"))
    return _run


@pytest.fixture
def analyse(monkeypatch):
    def _run(**fields):
        answer = CanonicalAnswer(deal_rot_days=30, analyzed_at=NOW.isoformat(), deals=[],
                                 summary="offline", **fields)
        monkeypatch.setattr(contact_status, "run_canonical_task", returns(answer))
        monkeypatch.setattr(contact_status, "load_deals", returns([]))
        monkeypatch.setattr(contact_status, "load_activity_index",
                            returns(ActivityIndex(contact_dates_indexed=True)))
        monkeypatch.setattr(contact_status.checks, "load_company_clock", returns((NOW, "UTC", "$")))
        monkeypatch.setattr(contact_status.checks, "load_context",
                            returns(PipelineContext(today="2026-09-28", pipelines={})))
        return run(contact_status.analyse(ActivityClient()))
    return _run


# BH-EMP-01
@pytest.mark.parametrize("roles, employee_only, reason", [
    ({"employee"}, True, workflow.EMPLOYEE_REASON),
    ({"employee", "contact"}, True, workflow.EMPLOYEE_REASON),
    ({"employee", "customer"}, False, None),
    ({"employee", "prospect"}, False, None),
    ({"employee", "supplier"}, True, workflow.SUPPLIER_REASON),
    ({"supplier"}, False, workflow.SUPPLIER_REASON),
    ({"customer"}, False, None),
    (set(), False, None),
    (None, False, None),
])
def test_employee_only_classification(roles, employee_only, reason):
    assert workflow.is_employee_only(roles) is employee_only
    assert workflow.non_sales_reason(roles) == reason


# BH-EMP-02
def test_canonical_excludes_employee_only_deal(canonical):
    deals = [old_deal("E", "emp"), old_deal("C", "cust"), old_deal("M", "emp-cust")]
    roles = {"emp": {"employee"}, "cust": {"customer"}, "emp-cust": {"employee", "customer"}}

    answer = canonical(deals, roles)

    assert [(r["deal_id"], r["reason"]) for r in answer.excluded_deals] == [("E", workflow.EMPLOYEE_REASON)]
    # M is an employee but also a customer, so it stays
    assert {d.deal_id for d in answer.deals} == {"C", "M"}
    assert {u.party_id for u in answer.uncalled} == {"cust", "emp-cust"}
    assert "1 " + EMPLOYEE_LINE in answer.summary


# BH-EMP-03
def test_unreadable_party_excludes_nothing(canonical):
    answer = canonical([old_deal("E", "emp")], roles=None)

    assert answer.excluded_deals == []
    assert [d.deal_id for d in answer.deals] == ["E"]
    assert "supplier-side and employee deals could not be identified and are NOT excluded" in answer.summary


# BH-EMP-04
def test_employee_only_lead_not_flagged():
    def party(pid, *roles):
        return {"id": pid, "name": pid, "roles": [{"role": r, "active": True} for r in roles]}

    ctx = PipelineContext(
        today="2026-09-28",
        pipelines={"sales": {"qualification"}},
        parties={
            "emp": party("emp", "employee"),
            "emp-pros": party("emp-pros", "employee", "prospect"),
            "pros": party("pros", "prospect"),
        },
        leads={
            "L1": {"id": "L1", "party_id": "emp", "status": "qualified"},
            "L2": {"id": "L2", "party_id": "emp-pros", "status": "qualified"},
            "L3": {"id": "L3", "party_id": "pros", "status": "qualified"},
        },
    )

    # no deals, so none of the leads has a deal yet
    rows = pipeline_checks.find_leads_needing_action([], ctx, 30)
    flagged = {r["lead_id"] for r in rows}

    assert flagged == {"L2", "L3"}


# BH-EMP-05
def test_must_report_collapses_employee_exclusions(analyse):
    excluded = [{"deal_id": f"e{i}", "title": f"Staff deal {i}", "reason": workflow.EMPLOYEE_REASON}
                for i in range(20)]
    excluded.append({"deal_id": "s1", "title": "Supplier deal", "reason": workflow.SUPPLIER_REASON})
    excluded += [{"deal_id": f"f{i}", "title": f"fixture {i}", "reason": "test fixture"} for i in range(2)]

    lines = analyse(excluded_deals=excluded)["must_report"]

    assert lines.count("20 " + EMPLOYEE_LINE) == 1
    assert len([l for l in lines if "Supplier deal" in l]) == 1
    assert "2 open test-fixture deal(s) excluded" in lines
    assert not any("Staff deal" in l for l in lines)


# --- deal_rot_days ---

class PrefsClient(MCPClient):
    def __init__(self, records):
        self.records = records

    async def list_(self, entity, *args, **kwargs):
        assert entity == "CRMPreferences"
        return {"records": self.records}

    async def call_tool(self, name, arguments):
        return await self.list_(arguments["entity"])


def threshold_for(value):
    return run(workflow.load_rot_threshold(PrefsClient([{"deal_rot_days": value}])))


# BH-ROT-01
@pytest.mark.parametrize("value, days, live, note", [
    (6, 30, False, "is 6 days, outside the accepted 7-90"),
    (7, 7, True, ""),
    (30, 30, True, ""),
    (90, 90, True, ""),
    (91, 30, False, "is 91 days"),
    (365, 30, False, "is 365 days"),
    (1, 30, False, "is 1 days"),
    ("45", 45, True, ""),
    (45.0, 45, True, ""),
])
def test_rot_threshold_range(value, days, live, note):
    result = threshold_for(value)
    got_days, got_live = result

    assert (got_days, got_live) == (days, live)
    assert isinstance(result.note, str)
    if note:
        assert note in result.note
    else:
        assert result.note == ""


# BH-ROT-02
@pytest.mark.parametrize("value", [0, -5, "thirty", None])
def test_unusable_rot_value_falls_back(value):
    result = threshold_for(value)

    assert tuple(result) == (30, False)
    assert result.note == ""


def test_no_prefs_record_falls_back():
    result = run(workflow.load_rot_threshold(PrefsClient([])))

    assert tuple(result) == (30, False)
    assert result.note == ""


# BH-ROT-03
def test_out_of_range_note_shows_up(canonical, analyse):
    note = "deal_rot_days is 365 days, outside the accepted 7-90"

    answer = canonical([old_deal("C", "cust")], {"cust": {"customer"}}, RotThreshold(30, False, note))

    assert answer.unavailable_note == note
    assert note in answer.summary
    assert answer.deal_rot_days == 30
    assert analyse(unavailable_note=note)["must_report"][0] == note


def test_old_tuple_threshold_still_works(canonical):
    # some older tests monkeypatch load_rot_threshold with a plain tuple
    answer = canonical([old_deal("C", "cust")], {"cust": {"customer"}}, (30, True))

    assert answer.deal_rot_days == 30
    assert not answer.unavailable_note
    assert "live CRMPreferences.deal_rot_days" not in answer.summary


# BH-ROT-04
@pytest.mark.parametrize("threshold, disclosed", [
    (RotThreshold(45, True), True),
    (RotThreshold(30, True), False),
    (RotThreshold(30, False, "CRMPreferences is not readable from this seat"), False),
])
def test_non_default_threshold_disclosed(canonical, threshold, disclosed):
    answer = canonical([old_deal("C", "cust")], {"cust": {"customer"}}, threshold)

    assert answer.deal_rot_days == threshold[0]
    assert ("live CRMPreferences.deal_rot_days" in answer.summary) == disclosed
    if disclosed:
        assert "45-day threshold" in answer.summary
    if threshold.note:
        assert threshold.note in answer.summary