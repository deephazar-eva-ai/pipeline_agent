
"""Regression tests for Pipeline-specific data and policy behavior."""

import asyncio
import datetime as dt

import pytest

from pipeline_agent.agent.contract import (
    ActionStatus,
    DealResult,
    ExecutionMode,
)
from pipeline_agent.agent.pipeline_checks import (
    PipelineContext,
    actual_contact_date,
    add_business_days,
    build_logged_contact,
    collect_data_issues,
    display_title,
    escalations_needed,
    find_late_orders,
    find_leads_needing_action,
    is_test_fixture,
    load_context,
    weighted_pipeline,
    worklist_by_owner,
)
from pipeline_agent.agent.request_guard import RULES, check_request
from pipeline_agent.agent.workflow import (
    ActivityIndex,
    determine_next_action,
    load_activity_index,
)
from pipeline_agent.config import Settings, tenant_for_url
from pipeline_agent.mcp.client import MCPClient


NOW = dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc)


def make_deal(id="deal", **extra):
    return {
        "id": id,
        "title": "Production deal",
        "party_id": "customer-a",
        "_party_id_display": "Acme Manufacturing",
        "pipeline": "sales",
        "stage": "qualification",
        "value": 1000,
        "owner": "Avery Rep",
        **extra,
    }


def make_context(**extra):
    return PipelineContext(
        today="2026-09-29",
        currency_symbol="$",
        pipelines={
            "sales": {
                "qualification",
                "proposal",
                "negotiation",
                "closed_won",
            }
        },
        parties={
            "customer-a": {
                "id": "customer-a",
                "name": "Acme Manufacturing",
                "type": "organization",
                "roles": [{"role": "customer"}],
            },
            "customer-b": {
                "id": "customer-b",
                "name": "Beta Industrial",
                "type": "organization",
            },
            "quinn": {
                "id": "quinn",
                "name": "Quinn Buyer",
                "type": "person",
            },
        },
        person_orgs={
            "quinn": {"customer-b"},
        },
        leads={
            "bad-lead": {
                "id": "bad-lead",
                "status": "disqualified",
            }
        },
        known_people={"avery rep"},
        **extra,
    )


def test_test_fixture_markers_are_detected_without_matching_normal_text():
    assert is_test_fixture("[team07-test KS-PA] deal")
    assert is_test_fixture("[team 11-probe] activity")
    assert not is_test_fixture("team 07 production test drive")


def test_uuid_like_deal_title_falls_back_to_party_name():
    deal = make_deal(
        title="Deal from 123e4567-e89b-12d3-a456-426614174000"
    )

    assert display_title(deal) == "Acme Manufacturing - (untitled deal)"


def test_business_day_calculation_skips_weekends_and_us_holiday():
    # Thanksgiving is Thursday, so three business days after Wednesday
    # lands on the following Tuesday.
    result = add_business_days(
        dt.date(2026, 11, 25),
        3,
    )

    assert result == dt.date(2026, 12, 1)


def test_logged_contact_keeps_the_original_contact_date():
    payload = build_logged_contact(
        deal=make_deal(),
        contact_type="call",
        actual_date="2026-09-26",
        today="2026-09-29",
        outcome="Spoke to buyer",
        run_id="contact-test",
    )

    assert payload["due_date"] == "2026-09-29"
    assert actual_contact_date(payload) == "2026-09-26"


def test_activity_index_uses_actual_contact_date_for_backdated_activity():
    payload = build_logged_contact(
        deal=make_deal(),
        contact_type="call",
        actual_date="2026-09-26",
        today="2026-09-29",
        outcome="Spoke to buyer",
        run_id="contact-index-test",
    )

    class Client(MCPClient):
        async def call_tool(self, name, arguments):
            return {
                "records": [
                    {
                        "id": "contact",
                        **payload,
                    }
                ],
                "total": 1,
            }

    index = asyncio.run(
        load_activity_index(
            Client(),
            now=NOW,
        )
    )

    assert index.last_contact_by_deal == {
        "deal": "2026-09-26"
    }
    assert index.backdated_contacts == 1


def test_contact_for_another_customer_is_not_used_in_the_proposed_action():
    class ProposeClient(MCPClient):
        async def call_tool(self, name, arguments):
            raise AssertionError("propose mode must not call the client")

    status, action, _ = asyncio.run(
        determine_next_action(
            ProposeClient(),
            make_deal(contact_id="quinn"),
            mode=ExecutionMode.PROPOSE,
            run_id="wrong-contact",
            now=NOW,
            index=ActivityIndex(),
            ctx=make_context(),
        )
    )

    assert status is ActionStatus.RECOMMENDED
    assert action["type"] == "call"
    assert action["subject"] == "Follow up: Production deal"


def test_data_quality_checks_report_known_deal_issues():
    context = make_context(
        expired_quotes_by_deal={
            "one": [
                {
                    "number": "QTN-7",
                    "status": "sent",
                    "valid_till": "2026-09-20",
                }
            ]
        },
        overdue_milestones_by_deal={
            "one": [
                {
                    "plan": "Expansion",
                    "title": "Discovery",
                    "due_date": "2026-09-18",
                }
            ]
        },
    )

    first = make_deal(
        "one",
        contact_id="quinn",
        stage="bogus_stage",
        value="0",
        owner="nobody-xyz",
        lead_id="bad-lead",
        expected_close_date="2026-09-19",
    )

    second = make_deal(
        "two",
        lead_id="bad-lead",
        expected_close_date="2026-10-10",
    )

    won_deal = make_deal(
        "won",
        stage="closed_won",
        party_id="prospect",
    )

    context.parties["prospect"] = {
        "id": "prospect",
        "roles": [{"role": "prospect"}],
    }

    issues = collect_data_issues(
        [first, second, won_deal],
        context,
        rot_age={"one": 121},
        has_open_action=set(),
    )

    findings_by_deal = {}
    for issue in issues:
        findings_by_deal.setdefault(issue["deal_id"], []).append(issue)

    assert {"one", "two", "won"} <= set(findings_by_deal)
    assert len(findings_by_deal["one"]) >= 9
    assert len(findings_by_deal["two"]) >= 2
    assert len(findings_by_deal["won"]) == 1
    assert all(issue["detail"].strip() for issue in issues)


def test_late_orders_are_based_on_delivery_status():
    context = make_context(
        sales_orders=[
            {
                "number": "SO-late",
                "party_id": "customer-a",
                "status": "confirmed",
                "delivery_date": "2026-09-20",
                "delivered_status": "undelivered",
                "net_total": 1200,
                "late": False,
            },
            {
                "number": "SO-done",
                "party_id": "customer-a",
                "status": "confirmed",
                "delivery_date": "2026-09-20",
                "delivered_status": "delivered",
                "net_total": 500,
            },
        ]
    )

    rows = find_late_orders(
        [make_deal()],
        context,
    )

    assert rows == [
        {
            "order": "SO-late",
            "party_id": "customer-a",
            "party": "customer-a",
            "delivery_date": "2026-09-20",
            "days_late": 9,
            "delivered_status": "undelivered",
            "net_total": 1200.0,
            "open_deal_ids": ["deal"],
        }
    ]


def test_lead_action_list_excludes_test_leads():
    context = make_context()

    context.leads = {
        "qualified": {
            "id": "qualified",
            "party_id": "customer-a",
            "status": "qualified",
            "value": 200,
            "source": "web",
        },
        "overdue": {
            "id": "overdue",
            "party_id": "customer-a",
            "status": "working",
            "next_action": "2026-09-20",
            "value": 100,
        },
        "test": {
            "id": "test",
            "party_id": "customer-a",
            "status": "qualified",
            "notes": "[team10-probe]",
            "value": 999,
        },
        "supplier": {
            "id": "supplier",
            "party_id": "supplier-party",
            "status": "qualified",
            "value": 999,
        },
    }

    context.parties["supplier-party"] = {
        "name": "Vendor",
        "roles": [{"role": "supplier"}],
    }

    rows = find_leads_needing_action(
        [],
        context,
        30,
    )

    assert [row["lead_id"] for row in rows] == [
        "qualified",
        "overdue",
    ]


def test_lead_action_list_excludes_supplier_leads():
    context = make_context()

    context.leads = {
        "supplier": {
            "id": "supplier",
            "party_id": "supplier-party",
            "status": "qualified",
            "value": 999,
        }
    }

    context.parties["supplier-party"] = {
        "name": "Vendor",
        "roles": [{"role": "supplier"}],
    }

    rows = find_leads_needing_action(
        [],
        context,
        30,
    )

    assert rows == []


def test_weighted_pipeline_uses_stage_default_when_probability_is_zero():
    deals = [
        make_deal(
            "negotiation",
            stage="negotiation",
            value=1000,
            probability=0,
        ),
        make_deal(
            "won",
            stage="closed_won",
            value=500,
            probability=0,
        ),
        make_deal(
            "lost",
            stage="closed_lost",
            value=5000,
            probability=100,
        ),
    ]

    result = weighted_pipeline(
        deals,
        make_context(),
    )

    assert result["open_value"] == 1000
    assert result["weighted_by_stage_default"] == 750
    assert result["weighted_by_probability_field"] == 0


def test_worklist_prioritizes_higher_value_at_risk():
    ranked = worklist_by_owner(
        [
            (
                make_deal(
                    "low",
                    value=100,
                    owner="Avery",
                ),
                {"independent_rot_days": 100},
            ),
            (
                make_deal(
                    "high",
                    value=1000,
                    owner="Avery",
                ),
                {"independent_rot_days": 20},
            ),
        ]
    )

    assert [
        row["deal_id"]
        for row in ranked["Avery"]
    ] == ["high", "low"]


def test_escalations_include_review_items_for_invalid_deals():
    result = DealResult(
        "review",
        "bogus",
        {},
        None,
        {},
        ActionStatus.NEEDS_HUMAN_REVIEW,
        None,
        ["stage is invalid"],
    )

    escalations = escalations_needed(
        [result],
        [
            {
                "deal_id": "wrong-contact",
                "code": "contact_of_other_customer",
                "detail": "contact is another customer",
            }
        ],
        [
            {
                "party": "Acme",
                "order": "SO-late",
                "days_late": 9,
            }
        ],
    )

    assert len(escalations) == 3
    assert {item.get("deal_id") for item in escalations} >= {"review", "wrong-contact"}
    assert {item.get("party") for item in escalations} == {None, "Acme"}
    assert all(item["why"].strip() and item["decide"].strip() for item in escalations)


@pytest.mark.parametrize(
    "rule",
    RULES,
    ids=lambda rule: rule.code,
)
def test_unsafe_requests_are_rejected_before_tools_are_used(rule):
    prompts = {
        "approve_quote": "Mark quote QTN-4 approved",
        "set_line_amount": "Set line amount on the quote to 100",
        "revenue_attainment": "What is revenue attainment against target?",
        "invoice_payment": "Has this invoice been paid?",
        "delete_record": "Delete the duplicate deal",
        "other_users_task": "Update the weekly agent task prompt",
        "bulk_close": "Close all old deals as lost",
    }

    response = check_request(
        prompts[rule.code]
    )

    assert response is not None
    assert response.tools_attempted == []


@pytest.mark.parametrize(
    "prompt",
    [
        "List deals that are rotting",
        "Draft a follow-up for Acme",
        "Show late orders",
        "Create a quote",
    ],
)
def test_normal_pipeline_requests_are_allowed(prompt):
    assert check_request(prompt) is None


def test_closed_deal_is_not_updated_after_reread():
    class ClosingClient(MCPClient):
        def __init__(self):
            self.created = False

        async def call_tool(self, name, arguments):
            if name == "get" and arguments["entity"] == "Deal":
                return make_deal(stage="closed_won")

            if name == "create":
                self.created = True

            return {
                "records": [],
                "total": 0,
            }

    client = ClosingClient()

    status, _, reasons = asyncio.run(
        determine_next_action(
            client,
            make_deal(),
            mode=ExecutionMode.CREATE_NEXT_ACTIONS,
            run_id="closed-reread",
            now=NOW,
            index=ActivityIndex(),
            ctx=make_context(),
        )
    )

    assert status is ActionStatus.RECOMMENDED
    assert client.created is False


def test_context_loader_does_not_send_sort_order_parameter():
    class ContextClient(MCPClient):
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append(dict(arguments))
            return {
                "records": [],
                "total": 0,
            }

    client = ContextClient()

    asyncio.run(
        load_context(
            client,
            today="2026-09-29",
        )
    )

    assert client.calls
    assert all(
        "sort_order" not in call
        for call in client.calls
    )


def test_tenant_is_derived_from_mcp_url():
    url = "https://class.agentswitch.theschoolofai.in/mcp"

    assert tenant_for_url(url) == "keystone"

    settings = Settings.from_env(
        {"AGENTSWITCH_MCP_URL": url}
    )

    assert settings.tenant == "keystone"
