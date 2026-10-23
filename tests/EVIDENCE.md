# Test execution evidence

## Latest offline regression run

| Field | Evidence |
|---|---|
| Date | 2026-10-23 |
| Working directory | `pipeline_agent/` (branch `main`; code as submitted in `136733a`) |
| Command | `.venv/bin/pytest tests -q` |
| Result | **430 passed** in 0.53s |
| Python 3.11 check | Last done on 2026-10-19: a clean `pip install .` of `central-eval` @ `da9b7e8` into a fresh Python 3.11.15 venv: 418 passed, central dry run 4/4 |
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
| `test_it01_scenario.py` | Offline IT-01 fixture-book population, adversarial-row coverage, and server-date behavior. |
| `test_run3_bugfix_regressions.py` | IT-01 Run 3 fixes for contact classification, thresholds, timezones, evidence, linkage, reporting, and completed-activity write guards. |
| `test_emp_deal_rot_config.py` | Bug-hunt fixes 31589ab3 (employee-only parties excluded) and 66f96caa (`deal_rot_days` range 7–90, disclosure of rejected or non-default values). |
| `test_token_budget.py` | Loop hard token budget: stop on reported or estimated usage, no success over budget, no limit without a budget. |
| `test_loop_wrapup.py` | Budget wrap-up call, single wrap-up chance, proxy upstream 401 retried once, plain 401 not retried, central model-probe retry, central 140k budget. |
| `test_time_wrapup.py` | Time-budget wrap-up call, single chance, no wrap-up without a deadline, evaluator deadline parsing, task timeout capped by the deadline with a floor. |

## Latest recorded live read-only verification

The latest live evidence is the 2026-10-23 01:13 IST local run of the central
runner on `136733a` (a clean `git archive` export, `AGENTSWITCH_SECONDS_LEFT=1800`),
against the shared book on both instances, read-only, with
`anthropic/claude-sonnet-4.6` via OpenRouter, followed by the evaluator's own
central Run 7 of the same commit. It was not rerun by the offline
test command above. No run folder was kept: the central runner writes only
`results.json`.

| Check | Result |
|---|---|
| Keystone | 7/7: preflight tools=234, `G1=sales_reachable`; canonical 25 rotting deals, 8 uncalled customers, 0 writes; loop `done` in 2 calls, 19,610 of 140,000 tokens |
| Suryodaya | 7/7: preflight tools=234; canonical 54 rotting deals, 20 uncalled customers, 0 writes; loop `done` in 3 calls, 47,978 of 140,000 tokens, 0 unsupported figures |
| Company-day date check | Before committing it, a completed Suryodaya call dated the IST day at 01:13 IST was accepted by the platform (201; 422 before N489) |
| Central Run 7 | The evaluator's own run of `136733a` (2026-10-23 10:40 IST): 14/14, ks 25/8, su 54/21, 12 canonical tool calls each, 0 writes, both loops `done`, 174,121 tokens |
| Earlier (2026-10-19, `da9b7e8`) | Local 7/7 on both instances (tools=250, ks 25/8, su 54/20); Suryodaya with the budget forced to 40k still ended `done`, so the token wrap-up works on a real model; central Run 6 14/14 |

### Earlier live evidence (2026-10-07)

The earlier retained live evidence is a 2026-10-07 Keystone canonical proposal
run.

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
