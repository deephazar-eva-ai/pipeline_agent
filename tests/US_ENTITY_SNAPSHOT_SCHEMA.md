# Synthetic US entity snapshot fixture contract

`keystone_us_entity_snapshot.example.json` is the checked-in, complete
synthetic fixture for `test_keystone_us_entity_snapshots.py`. It validates the
test harness only. Its identifiers, tax configuration, and results are
illustrative and **must not** be treated as a Keystone production capture or a
legal tax determination.

The fixture is deliberately offline and is loaded from this directory; the
module makes no HTTP requests. The latest suite execution on 2026-09-30
validated its nine contract cases as part of the 272 passing tests. If a
reviewed live-capture verifier is later needed, keep its fixture outside the
repository because it may contain customer and certificate metadata.

The snapshot must contain these top-level keys:

```json
{
  "metadata": {"default_currency": "USD", "as_of_date": "YYYY-MM-DD", "tax_configuration_version": "..."},
  "taxable_documents": ["document"],
  "exempt_documents": ["document"],
  "equivalent_path_pairs": [{"web_document_id": "...", "quote_document_id": "...", "equivalent_inputs": true}],
  "sourcing_override_cases": [{"equivalent_inputs": false, "place_of_supply": "OH", "sourcing_reason": "..."}],
  "conversion_pairs": [{"quotation_id": "...", "sales_order_id": "..."}],
  "orders": ["order line"],
  "make_orders_late_filter": {"late_count": 0, "late_true_ids": [], "invalid_value_status": 422},
  "jurisdiction_validation_cases": [{"outcome": "needs_review", "reason": "..."}],
  "conversion_api_contract": {"source_quotation_currency_code": "USD", "converted_order_currency_code": "USD", "required_sales_order_fields": ["currency_code"]},
  "us_tenant_contract": {"tenant": "keystone", "forbidden_india_tax_fields": ["gst_no", "gst_treatment", "is_reverse_charge"]}
}
```

Each `document` must have `id`, `party_id`, `currency`, `place_of_supply`,
`net_total`, `tax_total`, `grand_total`, and `tax_components`. Monetary values
are decimal strings in the fixture. A tax component has `jurisdiction`,
`rate`, and `amount`; retain zero-valued `cgst`, `sgst`, and `igst` when they
are present in a source response. An exempt document additionally has
`party_taxable: false` and `valid_resale_certificate: true`.

Each `order line` has `id`, `promised_date`, `ordered_qty`, `delivered_qty`,
and boolean `late`. Include the T-1, T, and T+1 cases relative to
`metadata.as_of_date`, plus a fully delivered past-due line.

The capture must include both valid resale-exempt variants: one without a
`place_of_supply` and one with an explicit valid jurisdiction. It must also
include at least one equivalent web/quote pair, one documented sourcing
override, one quote-to-order pair, and one incomplete-address or unexplained
jurisdiction response.

For any future source capture, use an approved read-only process, redact
identifiers and certificate data as necessary, and keep the source artifact
outside version control.

`conversion_api_contract` expresses the wire-level requirement that a
converted SalesOrder retains the source quotation's explicit USD currency.
`us_tenant_contract` names India-specific operational tax fields that must not
appear in a U.S.-tenant tool contract or entity payload. These are product
contract assertions, not a claim that a shared storage schema cannot exist.
