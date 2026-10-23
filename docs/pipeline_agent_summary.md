# Pipeline Agent: repository summary

*As of 2026-10-23, branch `main` (central submission `136733a`). The headline status is from 2026-10-23; the detailed canonical-task table is the 2026-10-03 live measurement. See [Current status](#current-status).*

Pipeline Agent is the capstone deliverable for **AgentSwitch Seat 07, Pipeline (CRM)**. It runs against the Suryodaya Precision Works tenant on `https://agentswitch.theschoolofai.in`, and has also been exercised on the Keystone tenant (`class.agentswitch.theschoolofai.in`). The repo contains a CRM agent, the harness that runs it and records evidence, and the supporting gap analysis, bug reports and tests.

## What it answers

The seat's canonical question:

> "Which deals are rotting, who has not been contacted, and what is the next action on each?"

For every open deal the agent returns whether it is rotting, whether the customer has been contacted (with the date and evidence), and a next action. It can also write that next action back as an `Activity`.

## How it works

### Canonical task: deterministic, not prompted

`agent/workflow.py` (`run_canonical_task`) answers from three core reads over MCP, followed by plain Python:

1. **`CRMPreferences.list`** reads the live `deal_rot_days` threshold. If that read fails, it falls back to the schema default rather than refusing.
2. **`Deal.list`** fetches every deal. `_rot_level` and `_rot_days` come back per record but are not server-filterable, so selection happens client-side.
3. **`Activity.list`** does one full scan, indexed by deal *and* by party. No live Activity carries a `deal_id`, so contact status is proven through the party link. Each verdict records its basis (`deal`, `party` or `none`).

It then makes optional context reads for the checks below: Pipeline, Party, Lead, Quotation, SalesOrder, and the `crm.account_plans` and `people_directory` endpoints. A live run is 13 read calls in about 27 seconds.

The parts where safety matters are plain Python, deliberately not left to a model's free-form reasoning: rot detection, contact classification, and the guard against creating duplicate activities.

Behaviours that go beyond the platform's own signal:

- **Rot feedback-loop fix.** The platform resets a deal's rot clock whenever any Activity is linked, including ones this agent creates. The agent recomputes rot with its own rows excluded, reports both numbers, and warns on any deal that only looks healthy because of its own write.
- **Hidden silence** (`find_hidden_silence`). Finds deals the platform calls `fresh` that have had no real customer contact.
- **Uncalled customers** (`find_uncalled`). Classifies contact age at the party level and excludes supplier-only parties.
- **Pipeline checks** (`agent/pipeline_checks.py`). Advisory findings a sales manager would want flagged, such as a contact belonging to another customer, a stage the pipeline does not define, two deals from one lead, or a slipped close date. Every source is optional: a narrower seat gets a narrower answer, never a crash.
- **Request guard** (`agent/request_guard.py`). Keyword rules refuse requests the agent must not act on, before it touches any data: approving a quote, setting a line amount, revenue attainment, invoice or payment, deleting records, other users' tasks, and bulk-closing deals. Each refusal names a safe alternative.

### Write mode

`--exec-mode propose` (the default) only reads. `--exec-mode create-next-actions` writes one `Activity` per deal that has no next action. Before each write it re-reads the deal's activities, which guards against concurrent changes and duplicates. `--limit` and `--deal-id` narrow the scope. Write mode has run live only once, on a single deal, with consent.

### Harness

| Module | Role |
|---|---|
| `mcp/client.py` | Abstract MCP interface and `PermissionDeniedError` |
| `mcp/real_client.py`, `mcp/transport.py` | Live JSON-RPC 2.0 client for `POST /api/mcp`, standard library only. Supports the entity-scoped tool surface (`Deal.list`, …), which is the default, and the 13-tool generic one |
| `mcp/stub_client.py` | In-memory fixture used for offline (dry-run) runs |
| `harness/loop.py` | Model-driven tool loop ("your own loop"), used for judgement tasks such as refusal. A second denied call to the same tool/entity force-stops the run, so the model cannot keep retrying and then claim success. An optional token budget stops the run, after one wrap-up call in which the model is told to answer now |
| `harness/recording.py` | Records every tool call into the run trace |
| `harness/artifacts.py` | Writes `input.json`, `config.json` (with secrets redacted), `result.json` and `status.json` to `runs/<timestamp>__<task>__<id>/` *before* scoring |
| `llm.py` | Model backends for the loop: `anthropic:<model>`, `openrouter:<model>`, `openai:<model>` (any OpenAI-compatible endpoint, used by the central evaluator) or `ollama:<model>`. 429, 5xx and the evaluator proxy's upstream 401 are retried once |
| `central_runner.py` | Central evaluator entry point named in `agentswitch-harness.toml`: seven read-only tasks per instance, written to `results.json` (see the README's "Central evaluator" section) |
| `preflight.py` | Read-only probe of what the seat can actually do: the tool catalogue it really exposes, whether each entity is readable, and drift against `catalogue_baseline.json` |

On this platform the tool catalogue *is* the access boundary. Preflight therefore prints a `CHANGED:` line whenever the seat's tool set moves.

## Running it

```bash
# offline, no credentials: canonical task against the stub
PYTHONPATH=src python3 -m pipeline_agent.runner --mode dry-run

# live; needs AGENTSWITCH_MCP_URL and AGENTSWITCH_MCP_TOKEN in .env
PYTHONPATH=src python3 -m pipeline_agent.runner --task preflight --mode live
PYTHONPATH=src python3 -m pipeline_agent.runner --task canonical --mode live
PYTHONPATH=src python3 -m pipeline_agent.runner --task canonical --mode live \
    --exec-mode create-next-actions --limit 1

# model-driven loop
MODEL_NAME=anthropic:<model> PYTHONPATH=src python3 -m pipeline_agent.runner \
    --task loop --mode live --request "<task prompt>"
```

The repo's `.env` currently points at **Keystone** (`class.agentswitch…`). To run against Suryodaya, set `AGENTSWITCH_MCP_URL=https://agentswitch.theschoolofai.in` and use a token from `/api/auth/login` as `AGENTSWITCH_MCP_TOKEN`.

The runner's tasks are `canonical`, `preflight`, `loop` and `log-contact`. `log-contact` records a contact that actually happened, with `--contact-date`, `--contact-type` and `--outcome`.

## Repository layout

| Path | Contents |
|---|---|
| `src/pipeline_agent/` | The agent, harness, MCP clients, preflight and CLI described above |
| `tasks/` | `mandatory_refusal_task.py`: a human-authored scored task that probes the unavailable `Invoice` entity and verifies the refusal and that nothing changed, through direct REST snapshots. `run_refusal_task.sh` wraps it; see below. Also `task_schema.json` and the task-matrix `README.md` |
| `tests/` | Offline suite: 21 modules, **462 passing** (2026-10-23). It covers the workflow, guards, preflight, transport, artifacts, the loop's token and time budgets and model retries, and Keystone-specific contracts. `test_pipeline_enhancements.py` and `test_remaining.py` (written by the team) are part of the scored matrix; the other modules are regression tests and are not scored. See `tests/README.md`, `TEST_CASES.md` and `EVIDENCE.md` |
| `gapreport/` | `crm_gapreport.md`: what the platform has, what competitors (Clarify, Monaco, Reevo) have that it lacks, and what an agent can close today. `deals_snapshot_schema.json`: the proposed `DealSnapshot` history table. `crm_gap_closure.pptx`: a 12-slide deck covering what exists, what is proposed, and the impact on schema and platform |
| `bugs/` | `mybugs_all_2026-09-30.json`: the full `/api/bug-report/mine` export, refreshed 2026-10-19: 94 bugs (Suryodaya 50, Keystone 44), all `new`; two earlier reports the platform no longer lists are kept under `no_longer_listed`. `bughunt_filed_2026-10-13/16/17/22/23.json`: the bug-hunt filings' bodies and ids (10, 1, 1, 1, 1), with their fixtures, test cases (`bughunt_test_cases.md`) and the H4 probe script. `run3_bugfixes.md` and `run3_test_cases.md`: the IT-01 Run 3 fixes |
| `docs/` | `architecture.md`, `agent_contract.md` (input/output contract), `open_items.md` (measured status and open issues), `capstone_capability_enhancement_for_pipelineagent.md` (enhancement plan), IT-01 run records (`integration_testing_*`), complex-jobs records, central run records (`central_run_test_result.md`, `central_runs_4_to_6.md` covering Runs 4–7), plus a copy of the gap report |
| `agentswitch-harness.toml` | Central evaluator config: install, run command, results file, instances (keystone, suryodaya), 30-minute timeout |
| `runs/` | Run artifacts (gitignored) |

### Refusal task runner

`tasks/run_refusal_task.sh` refuses to start unless:

- `AGENTSWITCH_BASE_URL` and `AGENTSWITCH_TOKEN` are set, and the token passes `/api/auth/me` (exit code 4 otherwise);
- the platform agent's own LLM provider answers a small test message. If the provider fails, the script warns, then asks whether to continue; non-interactive runs abort with exit code 5 unless `AGENTSWITCH_ACK_LLM_DOWN=1` is set.

After those checks it runs the task's `run` then `verify` steps, and exits with `verify`'s exit code.

## Current status

### Latest (2026-10-23)

- **Central evaluator: Run 7 passed 14/14** on `136733a`, the final submission (keystone 7/7, suryodaya 7/7; 174,121 tokens of the 300k per-run cap; both loops ended `done`). Run 7 added the evaluator's time budget (`AGENTSWITCH_SECONDS_LEFT` / `AGENTSWITCH_DEADLINE`) and checks new Activity dates against the company's day instead of UTC, after the platform fixed `4681e718` (N489). Run 6 also passed 14/14 (`da9b7e8`). Runs 4 and 5 failed: the evaluator's model returned 401s, and the Suryodaya loop hit the old 110k budget. The fixes were the wrap-up call, a 140k budget and the proxy-401 retry. Records: `docs/central_run_test_result.md` (Run 3) and `docs/central_runs_4_to_6.md` (Runs 4–7).
- **IT-01: Run 3 passed** on 2026-10-12 (`docs/integration_testing_pipeline_run3.md`); its fixes are on `main`.
- **Canonical task on the shared book** (central Run 7, 2026-10-23, read-only): Keystone 25 rotting deals and 8 uncalled customers; Suryodaya 54 and 21. Part of the drop on Suryodaya since 2026-10-03 is that open deals whose only party is a company employee are now excluded (bug-hunt fix 31589ab3, 20 deals); the book has also aged since then.

### Canonical task on both tenants (2026-10-03)

Measured live on 2026-10-03, read-only (`propose` mode; no records were written).

| | Suryodaya (India) | Keystone (US) |
|---|---|---|
| Run artifact | `runs/20261003T064849Z__canonical__live__bf9c490b` | `runs/20261003T064925Z__canonical__live__f011ad40` |
| Run | 13 reads, 28 s, completed | 13 reads, 26 s, completed |
| Rot threshold (`deal_rot_days`) | 30 days | 30 days |
| **Rotting deals** | **67** | **25** |
| Rotting deals that already have an open next action | 1 | 19 |
| Rotting deals whose open action is overdue | 50 | 4 |
| **Customers with open deals not called in 30 days** | **2** (both never called) | **6** (1 never called) |
| Customers never called but in the book under 30 days | 35 | 0 |
| Deals that look fresh but have no real contact | 0 | 0 |
| Supplier-side deals excluded | 16 | 2 (+1 test-fixture deal) |
| Platform rot boundary (measured) | exactly 15 days | between 5 and 16 days (book too small to pin down) |
| Data issues flagged | 198 | 43 |
| Late confirmed orders for customers with open deals | 270 | 5 |
| Leads needing action | 23 | 3 |
| Weighted open pipeline (stage defaults) | ₹5.50 Cr of ₹13.32 Cr | $836,930 of $1,522,956 |
| Items escalated to a person | 298 | 5 |

On Suryodaya, most of the data issues are deals with no contact (66) or no owner (64).

### Seat access

- **Preflight passes on both tenants.** `G1=sales_reachable`; CRMPreferences, Deal and Activity are all readable.
- **The tool catalogue has changed.** On 2026-10-23 the shared credential and the central evaluator's copies both serve **234** entity-scoped tools (250 on 2026-10-19, 261 on 2026-10-03, 263 in the evaluator for Runs 1–3), against a recorded baseline of 242, so preflight prints `CHANGED:`. The new tools haven't been reviewed yet. Once they are understood, update `RECORDED_TOOL_COUNT` (and `catalogue_baseline.json`).

### Refusal task

- **A1–A4 pass on both tenants (2026-10-07).** The platform agent's LLM provider, which returned `401 Unauthorized` until at least 2026-10-03, works again. `tasks/run_refusal_task.sh` still checks the provider first and stops if it is down.

### Tests and bugs

- **Tests:** offline suite, 462 passed (2026-10-23); 418 also passed on a clean Python 3.11 install on 2026-10-19.
- **Bugs:** 96 filed (Suryodaya 50, Keystone 46): 94 in the 2026-10-19 export, all `new`, plus `bdec2b73` (2026-10-22) and `c9c9078a` (2026-10-23). `4681e718` was fixed by the platform (N489) and verified live on 2026-10-23. Two earlier reports are no longer listed by the platform: `279bea18` (superseded by `9268a84a`) and `cd26e205` (stage records since fixed or reseeded).

### Still open

See `docs/open_items.md`.

- Write mode (`create-next-actions`) has not been run at scale.
- The `openai` and `openrouter` backends are exercised live (central runs, local runs); the loop's 140k budget is sized for the evaluator's model, which needs about twice as many calls as Sonnet on Suryodaya.
- The scored task matrix: the refusal task (live), `tests/test_pipeline_enhancements.py` (offline: rotting deal, existing and missing next action, bad stage data) and `tests/test_remaining.py` (offline: no recent contact, concurrent change; added 2026-10-23). Live verifiers for the other cases remain, to be written by a human (plan T5 in `docs/remainingtests.md`); AI-written tests score zero under the rubric.

### Not on `main` yet

Branch `phase2-gap-fillup` holds the Phase 2 work: drift guard, snapshots, rot score, leads, forecast, digest, consent. A stash holds work in progress on top of it. Neither is described above.
