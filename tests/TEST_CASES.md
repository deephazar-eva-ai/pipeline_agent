# Pipeline Agent executable test-case catalogue

## Scope

This catalogue describes the 13 executable test modules in this directory.
The suite uses unit, stub, CLI, artifact, and hermetic MCP/REST-contract
coverage by default. It does not create, update, or delete shared CRM
records. The latest offline result is **272 passed in 0.38s on 2026-09-30**;
the retained live read-only evidence is separately identified in
[EVIDENCE.md](EVIDENCE.md).

Run all cases from `pipeline_agent/`:

```bash
.venv/bin/pytest tests -q
```

The latest execution evidence is recorded in [EVIDENCE.md](EVIDENCE.md).

## Guard and workflow cases

| Area | Test module | Expected evidence |
|---|---|---|
| Preflight and tool-catalogue drift | `test_guard_regressions.py` | Surface detection, catalogue drift, required probes, and Gate G1 verdicts are explicit. |
| Rot and contact safety | `test_guard_regressions.py`, `test_catalogue_unit_stub.py` | Closed deals are excluded; future or malformed dates become evidence gaps; contact basis is preserved. |
| Canonical propose workflow | `test_nonwrite_workflows.py`, `test_catalogue_unit_stub.py` | Candidate scope, caps, output evidence, recommendations, existing actions, and no-write behavior are asserted. |
| Repeatability and artifacts | `test_remaining_offline_catalogue.py`, `test_catalogue_unit_stub.py` | Repeated propose results are equivalent; artifacts start running, finish correctly, and redact tokens. |
| Logged-bug regressions | `test_logged_bug_regressions.py` | Integer `done` handling, future contact dates, party linkage, rot laundering, calibration, and unknown stages remain safe. |

## Boundary and integration-contract cases

| Area | Test module | Expected evidence |
|---|---|---|
| Pagination and request bounds | `test_guard_regressions.py`, `test_nonwrite_workflows.py`, `test_remaining_offline_catalogue.py` | Deal/activity pagination reaches complete pages and forwards explicit limits and offsets. |
| MCP tool surface | `test_contract_boundaries.py`, `test_transport_llm_contracts.py` | Generic and entity-scoped tools translate correctly; unsupported tools stop before a wire call. |
| JSON-RPC and REST failures | `test_transport_llm_contracts.py` | JSON-RPC errors, malformed results, non-JSON bodies, and HTTP 401/403/500 remain distinguishable. |
| CLI and configuration | `test_nonwrite_workflows.py`, `test_transport_llm_contracts.py` | Missing credentials and invalid modes fail with actionable errors and no unsafe run. |
| LLM backends | `test_contract_boundaries.py`, `test_transport_llm_contracts.py` | Invalid model configuration, missing Anthropic SDK, and unavailable Ollama are reported clearly. |
| Keystone rot and data boundaries | `test_keystone_pipeline_regressions.py`, `test_keystone_application_boundaries.py`, `test_keystone_catalogue_gaps.py` | Contact-date age, rot levels, supplier scope, activity links, and inclusive boundaries are deterministic. |
| Keystone safety and capabilities | `test_keystone_agent_fixes.py`, `test_keystone_enhancement_regressions.py` | Request guards, advisory checks, logged contacts, stale rereads, broken sort-order avoidance, data quality, and tenant/timezone behavior remain safe. |
| US synthetic snapshot | `test_keystone_us_entity_snapshots.py` | USD, resale exemption, tax reconciliation, order lateness, and conversion contracts validate against a non-live fixture. |

## Case constraints

| Constraint | Required handling |
|---|---|
| Live CRM reads | Use credentials from the ignored `.env` file and retain a redacted run artifact. |
| CRM writes | Do not run by default. Require explicit approval, a defined target, precondition capture, and direct postcondition verification. |
| Platform UI/accessibility | Record as manual platform observations; do not attribute a UI defect to agent code without direct evidence. |
| Concurrency/token expiry | Exercise through controlled fixtures unless a safe, explicitly authorized live setup is available. |

## Fixture contract

The US entity coverage is driven by the checked-in synthetic fixture
`keystone_us_entity_snapshot.example.json`. Its required shape and safety
limits are documented in
[US_ENTITY_SNAPSHOT_SCHEMA.md](US_ENTITY_SNAPSHOT_SCHEMA.md). It is a test
contract only, not a production capture or tax determination.
