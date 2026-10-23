# Pipeline Agent executable test-case catalogue

## Scope

This catalogue describes the 20 executable test modules in this directory.
The suite uses unit, stub, CLI, artifact, and hermetic MCP/REST-contract
coverage by default. It does not create, update, or delete shared CRM
records. The latest offline result is **430 passed in 0.53s on 2026-10-23**;
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
| Aggregate and derived-analysis boundaries | `test_pipeline_enhancements.py` | Cached/pageable contact status, duplicate and repeat-read guards, request splitting, figure checks, and opt-in in-memory writes remain bounded. |
| IT-01 fixture integrity | `test_it01_scenario.py` | Fixture population, adversarial traps, and company-versus-server date handling remain coherent offline. |
| IT-01 Run 3 regressions | `test_run3_bugfix_regressions.py` | Contact type and cancellation rules, rot boundaries, timezone dates, provenance markers, link mismatches, reports, and write guards remain safe. |
| Loop token budget | `test_token_budget.py` | The loop ends as `token_budget` once reported or estimated usage reaches the budget, a model's `done` over budget is not a success, and no budget means no limit. |
| Budget wrap-up and model retries | `test_loop_wrapup.py` | A predicted overrun triggers exactly one final no-tools call; a non-answer on it ends the run; the proxy's upstream 401 is retried once, a plain 401 is not; the central model probe retries once; the central budget is 140k. |
| Time budget wrap-up | `test_time_wrapup.py` | A predicted deadline overrun triggers exactly one final no-tools call; no deadline means no wrap-up; `AGENTSWITCH_SECONDS_LEFT` / `AGENTSWITCH_DEADLINE` formats are parsed and unusable values ignored; the task timeout is capped by the deadline but not below its floor. |
| Bug-hunt fixes 2026-10-13 | `test_emp_deal_rot_config.py` | Employee-only parties are excluded from deals, uncalled and leads and collapse to one `must_report` line; the live rot threshold is accepted only within 7–90 days, and a rejected or non-default value is disclosed (cases BH-EMP-01..05, BH-ROT-01..04). |

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
