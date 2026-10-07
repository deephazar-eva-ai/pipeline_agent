# Pipeline Agent tests

This directory contains the runnable, offline regression suite for Pipeline
Agent. It currently contains 14 test modules and **297 passing tests**. The
suite covers deterministic workflow behavior, preflight and request guards,
stub clients, CLI startup, artifacts, MCP/REST boundaries, LLM configuration,
and Keystone-specific data and tenant contracts.

One module, `test_pipeline_enhancements.py`, is part of the scored task matrix:
the team wrote it by hand, and the root `README.md` maps its tests to the matrix
cases. The other modules are regression tests and are not scored. No test here
can prove a shared-CRM postcondition, because everything runs offline. See [TEST_CASES.md](TEST_CASES.md) for the coverage catalogue,
[EVIDENCE.md](EVIDENCE.md) for the latest execution record, and
[US_ENTITY_SNAPSHOT_SCHEMA.md](US_ENTITY_SNAPSHOT_SCHEMA.md) for the synthetic
US fixture contract.

## Run the suite

From the `pipeline_agent` directory:

```bash
.venv/bin/pytest tests -q
```

Use verbose output when diagnosing a failure:

```bash
.venv/bin/pytest tests -v
```

The latest local execution was on **2026-10-07**: `297 passed in 0.31s`.

The suite is source-layout compatible: `conftest.py` makes `src/` importable,
and `__init__.py` intentionally keeps `tests` a package because several
modules share fixtures with package-relative imports. Do not remove either
file.

`keystone_us_entity_snapshot.example.json` is intentionally synthetic, is
loaded only by the snapshot test, and contains no live tenant data.

## Test modules

- `test_guard_regressions.py` — preflight, pagination, refusal, credential,
  loop, and write/propose safety regressions.
- `test_catalogue_unit_stub.py` — contact/rot logic, stub workflow outcomes,
  artifacts, and safe refusal paths.
- `test_nonwrite_workflows.py` and `test_remaining_offline_catalogue.py` —
  canonical propose workflow, caps, repeatability, request bounds, and
  artifact behavior.
- `test_transport_llm_contracts.py` and `test_contract_boundaries.py` — MCP
  wire translation, JSON-RPC/HTTP failures, timestamps, provenance, runner
  states, and model-backend configuration.
- `test_logged_bug_regressions.py` — defenses for previously observed
  Pipeline data and workflow edge cases.
- `test_keystone_pipeline_regressions.py`,
  `test_keystone_application_boundaries.py`,
  `test_keystone_agent_fixes.py`, and `test_keystone_catalogue_gaps.py` —
  contact-age, rot, linkage, action-state, and supplier-scope regressions.
- `test_keystone_enhancement_regressions.py` — advisory/worklist checks,
  request guards, data quality, tenant detection, and timezone behavior.
- `test_keystone_us_entity_snapshots.py` — read-only validation of the
  bundled synthetic US tax, conversion, and fulfillment fixture.
- `test_pipeline_enhancements.py` — aggregate and derived-analysis boundaries,
  loop duplicate/repeat guards, request splitting, figure validation, and
  opt-in in-memory write behavior.
- `keystone_support.py` — fixed-clock Activity client and CRM record builders
  shared by Keystone scenarios.

## Live verification

Tests are offline by default and do not create CRM records. The most recent
recorded live read-only verification is dated 2026-10-07 in
[EVIDENCE.md](EVIDENCE.md); it was not rerun as part of the offline suite.
Live preflight and canonical propose-mode verification require
`AGENTSWITCH_MCP_URL` and `AGENTSWITCH_MCP_TOKEN`. Use `--exec-mode propose`
for read-only verification. Write-mode execution remains opt-in and requires
explicit approval, captured preconditions, and direct postcondition checks.
