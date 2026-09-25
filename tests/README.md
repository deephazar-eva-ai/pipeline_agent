# Tests

This directory contains the copied manual regression suite for Pipeline Agent.
The tests cover deterministic workflow behaviour, stubs, CLI startup,
artifacts, JSON-RPC/REST boundaries, LLM configuration, and guard regressions.
They are not a substitute for a human-authored scored verifier or for direct
postcondition checks against the live CRM.

## Run the suite

From the `pipeline_agent` directory:

```bash
.venv/bin/pytest tests -q
```

Use verbose output when diagnosing a failure:

```bash
.venv/bin/pytest tests -v
```

The suite is source-layout compatible: `conftest.py` makes `src/` importable,
and the tests use `pipeline_agent.*` imports. Shared test fixtures are
imported with explicit package-relative imports.

## Test modules

- `test_guard_regressions.py` — guard-activation and preflight regressions.
- `test_catalogue_unit_stub.py` — safe unit and stub catalogue coverage.
- `test_nonwrite_workflows.py` — canonical workflow, cap, scope, and CLI tests.
- `test_remaining_offline_catalogue.py` — additional safe boundaries and
  artifact/workflow behaviour.
- `test_transport_llm_contracts.py` — JSON-RPC transport, REST-boundary, and
  LLM configuration contracts.
- `test_contract_boundaries.py` — tool-surface, timestamp, provenance, and
  runner-result boundaries.
- `test_logged_bug_regressions.py` — defensive handling of Pipeline-relevant
  defects observed in live bug reports.

## Live verification

Tests are offline by default and do not create CRM records. Live preflight and
canonical propose-mode verification require `AGENTSWITCH_MCP_URL` and
`AGENTSWITCH_MCP_TOKEN`; keep credentials in the ignored `.env` file and use
`--exec-mode propose` for read-only verification. Write-mode tests remain
opt-in and require explicit approval.
