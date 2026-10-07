# Test execution evidence

## Latest offline regression run

| Field | Evidence |
|---|---|
| Date | 2026-10-07 |
| Working directory | `pipeline_agent/` |
| Command | `.venv/bin/pytest tests -q` |
| Result | **297 passed** in 0.31s |
| CRM effects | None. The suite is offline; no live CRM call or write-mode execution was performed. |

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
| `test_keystone_pipeline_regressions.py`, `test_keystone_application_boundaries.py` | Keystone K1/K6 rot behavior, date boundaries, and agent-laundering guards. |
| `test_keystone_agent_fixes.py`, `test_keystone_catalogue_gaps.py` | Keystone action-state, supplier-scope, linkage, and data-quality safeguards. |
| `test_keystone_enhancement_regressions.py` | Advisory capabilities, request refusals, safe re-read behavior, tenant resolution, and timezone defaults. |
| `test_keystone_us_entity_snapshots.py` | Synthetic US entity, tax, currency, and fulfillment contract fixture. |
| `test_pipeline_enhancements.py` | Aggregate boundaries, pageable/cached `contact_status`, duplicate and repeat-read guards, request splitting, figure checks, and opt-in in-memory write behavior. |

## Latest recorded live read-only verification

The latest retained live evidence is a 2026-10-07 Keystone canonical proposal
run. It was not rerun by the offline test command above.

| Check | Result |
|---|---|
| Keystone propose | Completed; 25 rotting deals and 6 uncalled customers reported; 13 tool calls; zero create/update/delete/transition calls; no created or updated IDs. |
| Artifact safety | Completed token-redacted artifact: `/tmp/pipeline-agent-live-20261007/20261007T082212Z__canonical__live__a3fd103b`. |
| Suryodaya propose | 65 rotting deals, 2 uncalled customers, 0 Activities created; tenant default was Asia/Kolkata. |
| Earlier Keystone propose | 23 rotting deals, 6 uncalled customers, 0 Activities created; tenant default was America/New_York. |
| Safety | Every listed propose-mode run issued only reads; this agent made no shared-CRM mutation. |

Artifacts:

- `/tmp/pipeline-live-suryodaya-timezone/20260929T094157Z__canonical__live__68174a39` (local, token-redacted)
- `/tmp/pipeline-live-keystone-timezone/20260929T094321Z__canonical__live__82b64614` (local, token-redacted)
- `/tmp/pipeline-agent-live-20261007/20261007T082212Z__canonical__live__a3fd103b` (local, token-redacted)

## Interpretation and limits

The 2026-10-07 result is automated evidence for local unit, stub, CLI,
artifact, and hermetic MCP/REST-contract behavior. It does not prove
shared-CRM state. Live verification must retain a redacted run artifact and
directly query the CRM postcondition. Create/update execution remains opt-in
and requires explicit approval.

## Re-run instructions

```bash
cd /home/acer/Documents/DEEPAK/eva_april2026/capstone/pipeline_agent
.venv/bin/pytest tests -q
```
