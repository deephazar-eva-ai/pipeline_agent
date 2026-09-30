
"""Regression tests for the synthetic US entity snapshot.

The snapshot is local test data. These tests do not make network requests
or modify CRM data.
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest


SNAPSHOT_PATH = Path(__file__).with_name(
    "keystone_us_entity_snapshot.example.json"
)


@pytest.fixture(scope="module")
def snapshot():
    assert SNAPSHOT_PATH.is_file(), (
        f"bundled snapshot is missing: {SNAPSHOT_PATH}"
    )

    data = json.loads(SNAPSHOT_PATH.read_text())

    assert isinstance(data, dict)
    return data


def money(value):
    return Decimal(str(value)).quantize(
        Decimal("0.01")
    )


def documents_by_id(snapshot):
    documents = [
        *snapshot["taxable_documents"],
        *snapshot["exempt_documents"],
    ]

    ids = [document["id"] for document in documents]

    assert len(ids) == len(set(ids))

    return {
        document["id"]: document
        for document in documents
    }


def jurisdiction_signature(document):
    return sorted(
        (
            component["jurisdiction"],
            str(component["rate"]),
            money(component["amount"]),
        )
        for component in document["tax_components"]
    )


def test_taxable_documents_use_usd_and_reconcile_tax_totals(snapshot):
    assert snapshot["metadata"]["default_currency"] == "USD"

    for document in snapshot["taxable_documents"]:
        assert document["currency"] == "USD"
        assert document["place_of_supply"]
        assert document["tax_components"]

        tax_total = sum(
            (
                money(component["amount"])
                for component in document["tax_components"]
            ),
            Decimal("0.00"),
        )

        assert tax_total == money(document["tax_total"])
        assert (
            money(document["net_total"]) + tax_total
            == money(document["grand_total"])
        )

        for component in document["tax_components"]:
            assert component["jurisdiction"]
            assert money(component.get("cgst", 0)) == Decimal("0.00")
            assert money(component.get("sgst", 0)) == Decimal("0.00")
            assert money(component.get("igst", 0)) == Decimal("0.00")


def test_resale_exempt_documents_are_zero_tax(snapshot):
    """Valid resale certificates should result in zero tax."""

    observed_place_of_supply = set()

    for document in snapshot["exempt_documents"]:
        assert document["currency"] == "USD"
        assert document["party_taxable"] is False
        assert document["valid_resale_certificate"] is True

        observed_place_of_supply.add(
            "explicit"
            if document["place_of_supply"]
            else "unset"
        )

        assert money(document["tax_total"]) == Decimal("0.00")

        assert all(
            money(component["amount"]) == Decimal("0.00")
            for component in document["tax_components"]
        )

        assert all(
            money(component["rate"]) == Decimal("0.00")
            for component in document["tax_components"]
        )

    assert observed_place_of_supply == {
        "unset",
        "explicit",
    }


def test_equivalent_quote_and_web_documents_have_same_tax_result(snapshot):
    """Equivalent inputs should resolve to the same tax jurisdiction."""

    documents = documents_by_id(snapshot)

    for pair in snapshot["equivalent_path_pairs"]:
        web_document = documents[pair["web_document_id"]]
        quote_document = documents[pair["quote_document_id"]]

        assert pair["equivalent_inputs"] is True
        assert web_document["party_id"] == quote_document["party_id"]
        assert (
            web_document["place_of_supply"]
            == quote_document["place_of_supply"]
        )

        assert (
            jurisdiction_signature(web_document)
            == jurisdiction_signature(quote_document)
        )

        assert money(web_document["tax_total"]) == money(
            quote_document["tax_total"]
        )


def test_sourcing_overrides_are_explicit(snapshot):
    for case in snapshot["sourcing_override_cases"]:
        assert case["equivalent_inputs"] is False
        assert case["sourcing_reason"].strip()
        assert case["place_of_supply"]


def test_quote_conversion_preserves_usd_tax_treatment(snapshot):
    """Converting a quote to an order should preserve its tax treatment."""

    documents = documents_by_id(snapshot)

    for pair in snapshot["conversion_pairs"]:
        quote = documents[pair["quotation_id"]]
        order = documents[pair["sales_order_id"]]

        assert quote["currency"] == "USD"
        assert order["currency"] == "USD"
        assert quote["party_id"] == order["party_id"]
        assert quote["place_of_supply"] == order["place_of_supply"]

        assert (
            jurisdiction_signature(quote)
            == jurisdiction_signature(order)
        )

        for field in (
            "net_total",
            "tax_total",
            "grand_total",
        ):
            assert money(quote[field]) == money(order[field])


def test_late_order_flag_matches_delivery_state(snapshot):
    """An undelivered order past its promised date is considered late."""

    as_of = dt.date.fromisoformat(
        snapshot["metadata"]["as_of_date"]
    )

    expected_late_ids = set()

    for order in snapshot["orders"]:
        promised_date = dt.date.fromisoformat(
            order["promised_date"]
        )

        fully_delivered = (
            money(order["delivered_qty"])
            >= money(order["ordered_qty"])
        )

        expected_late = (
            not fully_delivered
            and promised_date < as_of
        )

        assert order["late"] is expected_late

        if expected_late:
            expected_late_ids.add(order["id"])

    late_filter = snapshot["make_orders_late_filter"]

    assert late_filter["invalid_value_status"] in (400, 422)
    assert late_filter["late_count"] == len(expected_late_ids)
    assert set(late_filter["late_true_ids"]) == expected_late_ids


def test_ambiguous_jurisdiction_requires_an_explicit_review_result(snapshot):
    """Incomplete US address information must not silently determine tax."""

    allowed_outcomes = {
        "validation_error",
        "needs_review",
        "warning",
    }

    for case in snapshot["jurisdiction_validation_cases"]:
        assert case["outcome"] in allowed_outcomes
        assert case["reason"].strip()


def test_converted_order_requires_usd_currency_code(snapshot):
    contract = snapshot["conversion_api_contract"]

    assert contract["source_quotation_currency_code"] == "USD"
    assert contract["converted_order_currency_code"] == "USD"
    assert "currency_code" in contract["required_sales_order_fields"]


def test_us_tenant_contract_does_not_expose_india_tax_fields(snapshot):
    contract = snapshot["us_tenant_contract"]

    assert contract["tenant"] == "keystone"

    forbidden_fields = set(
        contract["forbidden_india_tax_fields"]
    )

    assert {
        "gst_no",
        "gst_treatment",
        "is_reverse_charge",
    } <= forbidden_fields
