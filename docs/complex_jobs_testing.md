# Complex jobs: what the harness has been tested on

*As of 2026-10-05. Figures come from the run folders under `runs/` and from the live measurements in [`pipeline_agent_summary.md`](pipeline_agent_summary.md) (2026-10-03).*

> **Update 2026-10-05:** the model-driven loop has now run live with Claude Sonnet 4.6. It failed at first, worked after two harness fixes, and has a remaining domain gap. See [`complex_jobs_testing_live.md`](complex_jobs_testing_live.md). The "Not yet tested" rows below about model-driven runs describe the state before that.

## Short answer

**Can the harness handle complex jobs?** Yes, if you mean jobs that read a lot of data across many entities, where the safety logic has to be right. It has not yet been tested on jobs where **a model plans the steps itself**. The model-driven loop has not been run with a real model.

## Evidence base

There are 154 run folders under `runs/`. Each one holds `input.json`, `config.json` (secrets removed), `result.json` with the full tool-call trace, and `status.json`. A run is written to disk before anything scores it. `status.json` changes to `completed` only after `result.json` has been fully written.

| Task | Live runs | Dry runs |
|---|---|---|
| `canonical` | 63 | 37 |
| `preflight` | 21 | 9 |
| `digest` | 4 | 3 |
| `log-contact` | 4 | 3 |
| `schedule` | 0 | 3 |
| `loop` (model-driven) | 0 | 1 (failed, see below) |

`digest`, `schedule` and the consent flow live on branch `phase2-gap-fillup` and are not on `main` yet.

## Jobs we have run

### 1. Canonical task, live on two tenants

> "Which deals are rotting, who has not been contacted, and what is the next action on each?"

- **13 reads in about 27 s.** The sources are CRMPreferences, Deal, Activity, Pipeline, Party, Lead, Quotation, SalesOrder, `crm.account_plans` and `people_directory`.
- **It joins data the platform does not link.** No live Activity carries a `deal_id`, so contact status is traced through the party. Every verdict records which link it used (`deal`, `party` or `none`).
- **It holds up when the seat is narrow.** All the context reads are optional, so a seat with less access gets a narrower answer instead of a crash.

Results from the 2026-10-03 live runs, read-only:

| | Suryodaya (India) | Keystone (US) |
|---|---|---|
| Run artifact | `runs/20261003T064849Z__canonical__live__bf9c490b` | `runs/20261003T064925Z__canonical__live__f011ad40` |
| Rotting deals | 67 | 25 |
| Customers with open deals not called in 30 days | 2 | 6 |
| Data issues flagged | 198 | 43 |
| Late confirmed orders for customers with open deals | 270 | 5 |
| Items escalated to a person | 298 | 5 |

### 2. Rot feedback loop

The platform resets a deal's rot clock whenever any Activity is linked to it, including one this agent creates. A naive agent would therefore hide the rotting deal it was meant to flag as soon as it wrote that deal's next action. The harness works out rot with the agent's own rows excluded and reports both numbers side by side. It also warns about any deal that only looks healthy because of the agent's own write. The derivation is in [`open_items.md`](open_items.md).

### 3. Writes with guards

- **`create-next-actions`** writes one `Activity` per deal that has no next action. Just before each write it re-reads that deal's activities, which guards against concurrent changes and duplicates. `--limit` and `--deal-id` narrow the scope.
- **`log-contact`** records a contact that actually happened. The four live runs on 2026-09-29 ended as follows:

  | Run | Outcome |
  |---|---|
  | `20260929T064628Z__log-contact__live__bd508684` | Refused before any call |
  | `20260929T064628Z__log-contact__live__e9c17345` | Refused after 2 reads |
  | `20260929T064640Z__log-contact__live__e1269811` | Ended `done` with no record created |
  | `20260929T064654Z__log-contact__live__22a682f3` | Ended `done` with exactly 1 Activity created |

### 4. Multi-entity digest (branch `phase2-gap-fillup`)

There were four live runs on 2026-09-30, each making 18 tool calls and taking 33–82 s, all `completed`. One example is `runs/20260930T055833Z__digest__live__2ed33114`.

### 5. Refusals and permission boundaries

- **Request guard** (`agent/request_guard.py`). Before the agent touches any data, it refuses these requests:
  - approving a quote
  - setting a line amount
  - revenue attainment
  - invoices or payments
  - deleting records
  - other users' tasks
  - bulk-closing deals

  Each refusal names a safe alternative.
- **Scored refusal task** (`tasks/mandatory_refusal_task.py`). It tries to reach `Invoice`, which is outside the seat's tool catalogue. It then checks through REST snapshots taken before and after the run that nothing changed.
- **Loop stop** (`harness/loop.py`). A second denied call to the same tool or entity stops the run, so a model cannot keep retrying past a refusal and then claim success.

### 6. Detecting access changes

On this platform the tool catalogue is the access boundary. Preflight compares the live catalogue with `catalogue_baseline.json` and prints `CHANGED:` when it has moved. On 2026-10-03 the seat served 261 tools against a baseline of 242.

## Not yet tested

| Gap | Current state |
|---|---|
| Model-driven runs | The canonical task and the digest are deterministic Python, so no model chooses the tool calls. The only `--task loop` run is `runs/20260921T094353Z__loop__dry-run__d45ed9fc`, which failed because the Ollama daemon was not reachable. Neither the `anthropic` nor the `ollama` backend has made a real API call. |
| Writes at scale | `create-next-actions` has run live once, on a single deal, with consent. |
| Scored refusal task | Checks A2–A4 pass. A1 is blocked because the platform's own LLM provider returns `401` (`can_configure: false`). This was re-checked on 2026-10-03. **Update 2026-10-07:** A1–A4 pass on both tenants; the provider works again. |
| Scored task matrix | Six of seven cases have not been started: rotting deal, no recent contact, existing next action, missing next action, concurrent change, and ambiguous stage data. These must be written by a human, because AI-written tests score zero. **Update 2026-10-07:** the team's `tests/test_pipeline_enhancements.py` is part of the matrix and covers four of them offline (rotting deal, existing and missing next action, ambiguous stage data). No recent contact and concurrent change remain. |

## How to describe it

The fair claim is that the harness handles jobs that are complex in data volume, safety logic and evidence, but not yet jobs where a model does the planning.

The next step that would close the main gap is one live run of `--task loop` with a real model on a multi-step request:

```bash
MODEL_NAME=anthropic:<model> PYTHONPATH=src python3 -m pipeline_agent.runner \
    --task loop --mode live --request "<human-authored multi-step task prompt>"
```
