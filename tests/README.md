# Pipeline Agent tests

This directory contains the runnable, offline regression suite for Pipeline
Agent. It currently contains 21 test modules and **478 passing tests**. The
suite covers deterministic workflow behavior, preflight and request guards,
stub clients, CLI startup, artifacts, MCP/REST boundaries, LLM configuration,
the agent loop's token and time budgets and model retries, and Keystone-specific data
and tenant contracts.

Two modules, `test_pipeline_enhancements.py` and `test_remaining.py`, are part of
the scored task matrix: the team wrote them by hand, and the root `README.md` maps
their tests to the matrix cases. The other modules are regression tests and are not scored. No test here
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

The latest local execution was on **2026-10-23**: `478 passed in 0.53s`.

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
- `test_it01_scenario.py` — internal consistency of the offline IT-01 fixture
  book, its adversarial rows, and evening/server-date handling.
- `test_run3_bugfix_regressions.py` — IT-01 Run 3 contact classification, rot
  thresholds, timezone, evidence, linkage, reporting, and write-guard fixes.
- `test_emp_deal_rot_config.py` — 2026-10-13 bug-hunt fixes: employee-only
  parties left out of both answers (31589ab3) and the live `deal_rot_days`
  accepted only within 7–90 days (66f96caa). Specified in
  `bugs/bughunt_test_cases.md`.
- `test_token_budget.py` — the loop's hard token budget: it stops once
  reported (or estimated) usage reaches the budget, and no budget means no
  limit. Written by hand by the team.
- `test_loop_wrapup.py` — the budget wrap-up call (one final no-tools call,
  only one chance), the single retry of the evaluator proxy's upstream 401,
  no retry of a plain 401, the central model probe's retry, and the central
  140k budget. Written by hand by the team.
- `test_time_wrapup.py` — the evaluator's time budget: a predicted overrun of
  the deadline asks for one final answer, only one chance, no deadline means
  no wrap-up, the formats `AGENTSWITCH_SECONDS_LEFT` / `AGENTSWITCH_DEADLINE`
  accept, and the task timeout capped by the deadline but not below its
  floor. Written by hand by the team.
- `test_remaining.py` — part of the scored matrix (cases "no recent contact" and
  "concurrent change"), plus the company-day due-date guard, the central
  runner's failed rows, results file and loop pass rule, and the Release 13
  `Activity.cancelled` flag (T6). Plan and expected
  results: `docs/remainingtests.md`. Written by hand by the team.
- `keystone_support.py` — fixed-clock Activity client and CRM record builders
  shared by Keystone scenarios.

## Live verification

Tests are offline by default and do not create CRM records. The most recent
recorded live read-only verification is dated 2026-10-23 in
[EVIDENCE.md](EVIDENCE.md); it was not rerun as part of the offline suite.
Live preflight and canonical propose-mode verification require
`AGENTSWITCH_MCP_URL` and `AGENTSWITCH_MCP_TOKEN`. Use `--exec-mode propose`
for read-only verification. Write-mode execution remains opt-in and requires
explicit approval, captured preconditions, and direct postcondition checks.
