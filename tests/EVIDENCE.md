# Test execution evidence

## Latest verified run

| Field | Evidence |
|---|---|
| Date | 2026-09-25 |
| Working directory | `pipeline_agent/` |
| Command | `.venv/bin/pytest tests -q` |
| Result | **190 passed** in 0.19s |
| Writes | No CRM write-mode test was run. |

## Evidence by module

| Module | Evidence provided |
|---|---|
| `test_guard_regressions.py` | Guard activation, preflight, pagination, refusal, credentials, and loop safety. |
| `test_catalogue_unit_stub.py` | Unit/stub catalogue outcomes, artifact states, redaction, and safe refusal paths. |
| `test_nonwrite_workflows.py` | Read-only canonical workflow, caps, scope, contact classification, and CLI startup. |
| `test_remaining_offline_catalogue.py` | Offline REST-request boundaries, rot behavior, artifacts, repeatability, and non-functional checks. |
| `test_transport_llm_contracts.py` | JSON-RPC result/error handling, HTTP failures, tool translation, and LLM configuration. |
| `test_contract_boundaries.py` | Tool-surface conversion, timestamps, provenance, backend failures, and runner result states. |
| `test_logged_bug_regressions.py` | Known Pipeline-relevant bug defenses: `done` filter type, future dates, party linkage, rot signal, calibration, and stage review. |

## Latest live read-only verification

| Check | Result |
|---|---|
| Preflight | 242 entity-scoped tools; CRMPreferences, Deal, and Activity were readable; canonical task READY. |
| Canonical propose | 84 rotting candidates; 66 with existing open actions; 0 Activities created. |
| Safety | Propose mode issued only reads; this agent made no shared-CRM mutation. |

Artifacts:

- `runs/20260925T110212Z__preflight__live__8f30e4e2`
- `runs/20260925T110224Z__canonical__live__5cecbea6`

## Interpretation

This is automated evidence for the local unit, stub, CLI, artifact, and
hermetic REST-contract suite. It does not by itself prove shared-CRM state.
Live verification must retain a redacted run artifact and directly query the
CRM postcondition. Create/update tests remain opt-in and require explicit
approval.

## Re-run instructions

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
.venv/bin/pytest tests -q
```
