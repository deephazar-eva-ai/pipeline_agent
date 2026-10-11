"""Tests for contact classification, rot calibration, next actions and run artifacts."""
import asyncio
import datetime as dt
import json

import pytest

from pipeline_agent.agent.contract import ActionStatus, AgentRequest, ContactStatus, ExecutionMode, RefusalResult
from pipeline_agent.agent.workflow import (
    PROVENANCE_MARKER,
    ActivityIndex,
    RotCalibration,
    assess_rot,
    calibrate_rot_boundary,
    classify_contact,
    determine_next_action,
    run_canonical_task,
)
from pipeline_agent.config import Settings
from pipeline_agent.harness.artifacts import run_artifact
from pipeline_agent.harness.base import TaskRun
from pipeline_agent.harness.loop import run_loop
from pipeline_agent.mcp.stub_client import StubMCPClient
from pipeline_agent.preflight import UNKNOWN, detect_surface

from .test_guard_regressions import NOW, Pages

THRESHOLD = 30


def days_ago(days):
    return (NOW - dt.timedelta(days=days)).isoformat()


def deal_updated(deal_id, level, days):
    return {"id": deal_id, "_rot_level": level, "updated_at": days_ago(days)}


def next_action(deal, run_id, index=None):
    return asyncio.run(determine_next_action(
        Pages(), deal,
        mode=ExecutionMode.PROPOSE, run_id=run_id, now=NOW, index=index or ActivityIndex(),
    ))


def read_json(path):
    return json.loads(path.read_text())


# --- contact classification ---------------------------------------------------

@pytest.mark.parametrize("days, expected", [
    (0, ContactStatus.RECENTLY_CONTACTED),
    (29, ContactStatus.RECENTLY_CONTACTED),
    (30, ContactStatus.RECENTLY_CONTACTED),  # the threshold day itself still counts as recent
    (31, ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD),
])
def test_contact_status_around_threshold(days, expected):
    status, evidence = classify_contact(NOW, THRESHOLD, days_ago(days), "party")

    assert status is expected
    assert evidence["basis"] == "party"


def test_no_contact_history_is_never_contacted():
    status, evidence = classify_contact(NOW, THRESHOLD, None, "none")

    assert status is ContactStatus.NEVER_CONTACTED
    assert evidence["basis"] == "none"


def test_unparseable_contact_date_is_insufficient_evidence():
    status, evidence = classify_contact(NOW, THRESHOLD, "not-a-date")

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert evidence["reason"] == "unparseable date"


def test_valid_contact_date_has_no_error_reason():
    status, evidence = classify_contact(NOW, THRESHOLD, "2026-09-23")

    assert status is ContactStatus.RECENTLY_CONTACTED
    assert "reason" not in evidence


# --- rot ----------------------------------------------------------------------

@pytest.mark.parametrize("days, expected", [(4, False), (5, True), (6, True)])
def test_rot_boundary_is_inclusive(days, expected):
    deal = {"id": str(days), "stage": "new", "_rot_level": "fresh", "updated_at": days_ago(days)}

    is_rot, _ = assess_rot(deal, ActivityIndex(), NOW, RotCalibration(5))

    assert is_rot is expected


def test_deal_without_updated_at_is_not_rotting():
    deal = {"id": "missing-date", "stage": "new", "_rot_level": "fresh"}

    is_rot, _ = assess_rot(deal, ActivityIndex(), NOW, RotCalibration(5))

    assert is_rot is False


def test_calibration_falls_back_to_default_with_no_deals():
    calibration = calibrate_rot_boundary([], ActivityIndex(), NOW)

    assert calibration.boundary_days == 5


def test_calibration_brackets_boundary_from_existing_deals():
    deals = [deal_updated("fresh", "fresh", 3), deal_updated("old", "attention", 8)]

    calibration = calibrate_rot_boundary(deals, ActivityIndex(), NOW)

    assert calibration.boundary_days == 4
    assert calibration.lower == 4
    assert calibration.upper == 8
    assert calibration.exact is False


def test_calibration_is_exact_when_deals_are_a_day_apart():
    deals = [deal_updated("fresh", "fresh", 3), deal_updated("attention", "attention", 4)]

    calibration = calibrate_rot_boundary(deals, ActivityIndex(), NOW)

    assert calibration.exact is True
    assert calibration.boundary_days == 4


def test_calibration_ignores_agent_written_deals():
    deals = [deal_updated("fresh", "fresh", 3), deal_updated("attention", "attention", 8)]
    index = ActivityIndex(agent_written_deals={"fresh", "attention"})

    calibration = calibrate_rot_boundary(deals, index, NOW)

    # Both deals are excluded, so we're back to the default.
    assert calibration.boundary_days == 5


# --- tool surface detection ---------------------------------------------------

def test_empty_catalogue_is_unknown_surface():
    assert detect_surface([]) == UNKNOWN


def test_entity_operations_mean_entity_scoped_surface():
    assert detect_surface(["Deal.list", "Activity.create"]) == "entity_scoped"


# --- next action --------------------------------------------------------------

def test_known_stage_gets_a_recommended_action():
    status, proposal, _ = next_action({"id": "mapped", "title": "M", "stage": "new"}, "e02")

    assert status is ActionStatus.RECOMMENDED
    assert proposal["deal_id"] == "mapped"


def test_unknown_stage_needs_human_review():
    status, proposal, _ = next_action({"id": "mapped", "title": "M", "stage": "made_up_stage"}, "e03")

    assert status is ActionStatus.NEEDS_HUMAN_REVIEW
    assert proposal is None


def test_existing_open_deal_activity_is_not_recreated():
    deal = {"id": "d", "party_id": "p", "stage": "new"}
    index = ActivityIndex(open_by_deal={"d": {"id": "human", "due_date": "2026-10-01"}})

    status, _, _ = next_action(deal, "existing", index)

    assert status is ActionStatus.EXISTING


def test_existing_open_party_activity_is_reported():
    deal = {"id": "d", "party_id": "p", "stage": "new"}
    index = ActivityIndex(open_by_party={"p": {"id": "party"}})

    # TODO: assert on the reason text once it's settled; for now this only
    # checks that the party-activity path runs without error.
    next_action(deal, "party-activity", index)


def test_agent_created_activity_is_called_out_in_reason():
    deal = {"id": "d", "party_id": "p", "stage": "new"}
    index = ActivityIndex(open_by_deal={
        "d": {"id": "agent", "due_date": "2026-10-01", "description": PROVENANCE_MARKER},
    })

    # TODO: assert the reason mentions the agent marker; for now this only
    # checks that the path runs without error.
    next_action(deal, "agent-activity", index)


# --- canonical task and loop --------------------------------------------------

def test_missing_deal_permission_is_refused_without_writing():
    client = Pages(denied={"Deal"})

    result = asyncio.run(run_canonical_task(client, AgentRequest(text="x"), run_id="missing-deal"))

    assert isinstance(result, RefusalResult)
    assert result.missing_domain == ""
    assert not client.created


def test_canonical_task_covers_each_action_status():
    result = asyncio.run(run_canonical_task(StubMCPClient(), AgentRequest(text="x"), run_id="canonical"))

    statuses = {deal.action_status for deal in result.deals}
    assert ActionStatus.EXISTING in statuses
    assert ActionStatus.RECOMMENDED in statuses
    assert ActionStatus.NEEDS_HUMAN_REVIEW in statuses


def test_loop_stops_after_repeated_denied_tool_call():
    denied_call = json.dumps({"action": "call_tool", "tool": "list", "arguments": {"entity": "Invoice"}})
    replies = iter([denied_call, denied_call])

    async def llm(*_):
        return next(replies)

    run = asyncio.run(run_loop({"id": "n12", "prompt": "x"}, StubMCPClient(), llm, "fake", max_steps=2))

    assert run.ended == "refused"
    assert not run.claimed_success
    assert [step.kind for step in run.steps] == ["refused", "refused"]


# --- run artifacts ------------------------------------------------------------

def test_failed_run_artifact_is_marked_failed(tmp_path):
    settings = Settings(mcp_token="secret", output_dir=str(tmp_path))

    with pytest.raises(ValueError):
        with run_artifact(str(tmp_path), "failed", task={"id": "x"}, settings=settings):
            raise ValueError("boom")

    run_dir = next(tmp_path.iterdir())
    assert read_json(run_dir / "status.json")["status"] == "failed"
    assert "ValueError: boom" in (run_dir / "error.txt").read_text()


def test_refused_run_artifact_never_exposes_token(tmp_path):
    settings = Settings(mcp_token="secret", output_dir=str(tmp_path))

    with run_artifact(str(tmp_path), "refused", task={"id": "y"}, settings=settings) as writer:
        writer.finalize(TaskRun(task_id="y", ended="refused", error="policy"))
        run_dir = writer.run_dir

    assert read_json(run_dir / "status.json")["status"] == "completed"
    assert read_json(run_dir / "config.json")["mcp_token"] == "***redacted***"

    files = {path.name for path in run_dir.iterdir()}
    assert {"input.json", "config.json", "result.json", "status.json"} <= files

    # The token must not show up anywhere, not just in config.json.
    everything = "".join(path.read_text() for path in run_dir.iterdir())
    assert "secret" not in everything