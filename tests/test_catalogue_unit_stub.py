import asyncio
import datetime as dt
import json

import pytest

from pipeline_agent.agent.contract import (
    ActionStatus,
    AgentRequest,
    ContactStatus,
    ExecutionMode,
    RefusalResult,
)
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    PROVENANCE_MARKER,
    RotCalibration,
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


@pytest.mark.parametrize(
    "days, expected",
    [
        (29, ContactStatus.RECENTLY_CONTACTED),
        (30, ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD),
        (31, ContactStatus.NOT_CONTACTED_SINCE_THRESHOLD),
        (0, ContactStatus.RECENTLY_CONTACTED),
    ],
)
def test_contact_status_at_threshold(days, expected):
    contacted_at = (NOW - dt.timedelta(days=days)).isoformat()

    status, evidence = classify_contact(
        NOW,
        30,
        contacted_at,
        "party",
    )

    assert status is expected
    assert evidence["basis"] == "party"


def test_contact_status_when_there_is_no_contact_history():
    status, evidence = classify_contact(
        NOW,
        30,
        None,
        "none",
    )

    assert status is ContactStatus.NEVER_CONTACTED
    assert evidence["basis"] == "none"


def test_unparseable_contact_date_is_treated_as_insufficient_evidence():
    status, evidence = classify_contact(
        NOW,
        30,
        "not-a-date",
    )

    assert status is ContactStatus.INSUFFICIENT_EVIDENCE
    assert evidence["reason"] == "unparseable date"


def test_valid_contact_date_does_not_add_an_error_reason():
    status, evidence = classify_contact(
        NOW,
        30,
        "2026-09-23",
    )

    assert status is ContactStatus.RECENTLY_CONTACTED
    assert "reason" not in evidence


def test_rot_boundary_is_inclusive():
    calibration = RotCalibration(5)

    from pipeline_agent.agent.workflow import assess_rot

    for days, expected in [
        (4, False),
        (5, True),
        (6, True),
    ]:
        deal = {
            "id": str(days),
            "stage": "new",
            "_rot_level": "fresh",
            "updated_at": (NOW - dt.timedelta(days=days)).isoformat(),
        }

        is_rot, _ = assess_rot(
            deal,
            ActivityIndex(),
            NOW,
            calibration,
        )

        assert is_rot is expected


def test_deal_without_updated_at_is_not_marked_rot():
    from pipeline_agent.agent.workflow import assess_rot

    deal = {
        "id": "missing-date",
        "stage": "new",
        "_rot_level": "fresh",
    }

    is_rot, _ = assess_rot(
        deal,
        ActivityIndex(),
        NOW,
        RotCalibration(5),
    )

    assert is_rot is False


def test_rot_calibration_uses_default_when_there_is_no_data():
    calibration = calibrate_rot_boundary(
        [],
        ActivityIndex(),
        NOW,
    )

    assert calibration.boundary_days == 5


def test_rot_calibration_finds_a_range_from_existing_deals():
    fresh_deal = {
        "id": "fresh",
        "_rot_level": "fresh",
        "updated_at": (NOW - dt.timedelta(days=3)).isoformat(),
    }

    older_deal = {
        "id": "old",
        "_rot_level": "attention",
        "updated_at": (NOW - dt.timedelta(days=8)).isoformat(),
    }

    calibration = calibrate_rot_boundary(
        [fresh_deal, older_deal],
        ActivityIndex(),
        NOW,
    )

    assert calibration.boundary_days == 4
    assert calibration.lower == 4
    assert calibration.upper == 8
    assert calibration.exact is False


def test_rot_calibration_uses_exact_boundary_when_available():
    fresh_deal = {
        "id": "fresh",
        "_rot_level": "fresh",
        "updated_at": (NOW - dt.timedelta(days=3)).isoformat(),
    }

    attention_deal = {
        "id": "attention",
        "_rot_level": "attention",
        "updated_at": (NOW - dt.timedelta(days=4)).isoformat(),
    }

    calibration = calibrate_rot_boundary(
        [fresh_deal, attention_deal],
        ActivityIndex(),
        NOW,
    )

    assert calibration.exact is True
    assert calibration.boundary_days == 4


def test_rot_calibration_ignores_agent_created_deals():
    fresh_deal = {
        "id": "fresh",
        "_rot_level": "fresh",
        "updated_at": (NOW - dt.timedelta(days=3)).isoformat(),
    }

    attention_deal = {
        "id": "attention",
        "_rot_level": "attention",
        "updated_at": (NOW - dt.timedelta(days=8)).isoformat(),
    }

    index = ActivityIndex(
        agent_written_deals={"fresh", "attention"},
    )

    calibration = calibrate_rot_boundary(
        [fresh_deal, attention_deal],
        index,
        NOW,
    )

    assert calibration.boundary_days == 5
    assert "no usable calibration pair" in calibration.basis


def test_empty_tool_catalogue_returns_unknown():
    assert detect_surface([]) == UNKNOWN


def test_tool_catalogue_with_entity_operations_is_entity_scoped():
    tools = [
        "Deal.list",
        "Activity.create",
    ]

    assert detect_surface(tools) == "entity_scoped"


def test_known_stage_produces_a_recommended_action():
    deal = {
        "id": "mapped",
        "title": "M",
        "stage": "new",
    }

    status, proposal, _ = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="e02",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.RECOMMENDED
    assert proposal["deal_id"] == "mapped"


def test_unknown_stage_requires_human_review():
    deal = {
        "id": "mapped",
        "title": "M",
        "stage": "made_up_stage",
    }

    status, proposal, reasons = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="e03",
            now=NOW,
            index=ActivityIndex(),
        )
    )

    assert status is ActionStatus.NEEDS_HUMAN_REVIEW
    assert proposal is None
    assert "human review" in reasons[0]


def test_existing_deal_activity_is_not_recreated():
    deal = {
        "id": "d",
        "party_id": "p",
        "stage": "new",
    }

    index = ActivityIndex(
        open_by_deal={
            "d": {
                "id": "human",
                "due_date": "2026-10-01",
            }
        }
    )

    status, _, reasons = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="existing",
            now=NOW,
            index=index,
        )
    )

    assert status is ActionStatus.EXISTING
    assert "already exists" in reasons[0]


def test_existing_party_activity_is_reported():
    deal = {
        "id": "d",
        "party_id": "p",
        "stage": "new",
    }

    index = ActivityIndex(
        open_by_party={
            "p": {
                "id": "party",
            }
        }
    )

    _, _, reasons = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="party-activity",
            now=NOW,
            index=index,
        )
    )

    assert "party" in reasons[0]


def test_agent_created_activity_is_identified_in_the_reason():
    deal = {
        "id": "d",
        "party_id": "p",
        "stage": "new",
    }

    index = ActivityIndex(
        open_by_deal={
            "d": {
                "id": "agent",
                "due_date": "2026-10-01",
                "description": PROVENANCE_MARKER,
            }
        }
    )

    _, _, reasons = asyncio.run(
        determine_next_action(
            Pages(),
            deal,
            mode=ExecutionMode.PROPOSE,
            run_id="agent-activity",
            now=NOW,
            index=index,
        )
    )

    assert "created by this agent" in reasons[0]


def test_missing_deal_permission_returns_refusal():
    client = Pages(denied={"Deal"})

    result = asyncio.run(
        run_canonical_task(
            client,
            AgentRequest(text="x"),
            run_id="missing-deal",
        )
    )

    assert isinstance(result, RefusalResult)
    assert result.missing_domain == ""
    assert not client.created


def test_loop_stops_after_repeated_denied_tool_call():
    replies = iter(
        [
            '{"action":"call_tool","tool":"list",'
            '"arguments":{"entity":"Invoice"}}',
            '{"action":"call_tool","tool":"list",'
            '"arguments":{"entity":"Invoice"}}',
        ]
    )

    async def llm(*_):
        return next(replies)

    run = asyncio.run(
        run_loop(
            {"id": "n12", "prompt": "x"},
            StubMCPClient(),
            llm,
            "fake",
            max_steps=2,
        )
    )

    assert run.ended == "refused"
    assert not run.claimed_success
    assert [step.kind for step in run.steps] == [
        "refused",
        "refused",
    ]


def test_canonical_task_reports_the_expected_deal_statuses():
    result = asyncio.run(
        run_canonical_task(
            StubMCPClient(),
            AgentRequest(text="x"),
            run_id="canonical",
        )
    )

    statuses = {deal.action_status for deal in result.deals}

    assert ActionStatus.EXISTING in statuses
    assert ActionStatus.RECOMMENDED in statuses
    assert ActionStatus.NEEDS_HUMAN_REVIEW in statuses


def test_failed_artifact_is_marked_as_failed(tmp_path):
    settings = Settings(
        mcp_token="secret",
        output_dir=str(tmp_path),
    )

    with pytest.raises(ValueError):
        with run_artifact(
            str(tmp_path),
            "failed",
            task={"id": "x"},
            settings=settings,
        ):
            raise ValueError("boom")

    run_dir = next(tmp_path.iterdir())

    status = json.loads(
        (run_dir / "status.json").read_text()
    )

    assert status["status"] == "failed"
    assert "ValueError: boom" in (
        run_dir / "error.txt"
    ).read_text()


def test_refused_artifact_is_written_without_exposing_the_token(tmp_path):
    settings = Settings(
        mcp_token="secret",
        output_dir=str(tmp_path),
    )

    with run_artifact(
        str(tmp_path),
        "refused",
        task={"id": "y"},
        settings=settings,
    ) as writer:
        writer.finalize(
            TaskRun(
                task_id="y",
                ended="refused",
                error="policy",
            )
        )
        run_dir = writer.run_dir

    status = json.loads(
        (run_dir / "status.json").read_text()
    )

    config = json.loads(
        (run_dir / "config.json").read_text()
    )

    assert status["status"] == "completed"
    assert config["mcp_token"] == "***redacted***"

    files = {path.name for path in run_dir.iterdir()}

    assert {
        "input.json",
        "config.json",
        "result.json",
        "status.json",
    }.issubset(files)

    all_contents = "".join(
        path.read_text()
        for path in run_dir.iterdir()
    )

    assert "secret" not in all_contents
