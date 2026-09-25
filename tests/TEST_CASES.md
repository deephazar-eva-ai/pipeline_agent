# Pipeline Agent test-case catalogue

## Scope

This catalogue describes the executable tests in this directory. The suite
uses unit, stub, CLI, artifact, and hermetic REST-contract doors by default.
It does not create, update, or delete shared CRM records.

Run all cases from `pipeline_agent/`:

```bash
.venv/bin/pytest tests -q
```

The latest execution evidence is recorded in [EVIDENCE.md](EVIDENCE.md).

## Guard and workflow cases

| Area | Test module | Expected evidence |
|---|---|---|
| Preflight and tool-catalogue drift | `test_guard_regressions.py` | Surface detection, tool-count drift, required probes, and Gate G1 verdicts are explicit. |
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

## Case constraints

| Constraint | Required handling |
|---|---|
| Live CRM reads | Use credentials from the ignored `.env` file and retain a redacted run artifact. |
| CRM writes | Do not run by default. Require explicit approval, a defined target, precondition capture, and direct postcondition verification. |
| Platform UI/accessibility | Record as manual platform observations; do not attribute a UI defect to agent code without direct evidence. |
| Concurrency/token expiry | Exercise through controlled fixtures unless a safe, explicitly authorized live setup is available. |
