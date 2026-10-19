# Pipeline Agent

Capstone deliverable for AgentSwitch Seat 07 — Pipeline (CRM), Suryodaya Precision Works.

Standalone repository — self-contained, no dependency on sibling projects in this
workspace (`pipeline/S18Code`, `pipeline/glc_v5`). Everything needed to build, run,
and grade this seat's agent and harness lives here.

Canonical task this agent answers:

> "Which deals are rotting, who has not been contacted, and what is the next action
> on each?"

See project scope and gap analysis (kept outside this repo, in the capstone docs tree)
for the full context this agent is built against.

## Harness

The harness runs the agent against the live AgentSwitch platform over MCP. It records
every tool call and writes the run to disk before anything is scored. The scored
verifiers then check outcomes against the platform's own data, not against what the
agent says it did.

### What it is made of

| Piece | Where | What it does |
|---|---|---|
| MCP client | `src/pipeline_agent/mcp/` | Live JSON-RPC 2.0 client for `POST /api/mcp` (standard library only), and an in-memory stub for offline runs |
| Preflight | `src/pipeline_agent/preflight.py` | Read-only probe of what the seat can actually do: the tool catalogue it exposes, whether each entity is readable, and drift against `catalogue_baseline.json` |
| Deterministic workflow | `src/pipeline_agent/agent/workflow.py` | The canonical task: rot, contact status and next action, computed in plain Python |
| Model-driven loop | `src/pipeline_agent/harness/loop.py` | "Our own loop": the model picks each tool call. A second denied call to the same tool/entity force-stops the run, so a model cannot retry past a refusal. An optional token budget gives the model one wrap-up call to answer before the run is stopped |
| Central evaluator entry point | `src/pipeline_agent/central_runner.py` + `agentswitch-harness.toml` | What the course's central evaluator runs: env contract, preflight, canonical task, refusal guard, model probe and the agent loop, all read-only, written to `results.json` (see [Central evaluator](#central-evaluator)) |
| Recorder | `src/pipeline_agent/harness/recording.py` | Captures every tool call into the run trace |
| Run artifacts | `src/pipeline_agent/harness/artifacts.py` | Writes `runs/<timestamp>__<task>__<id>/` with `input.json`, `config.json` (secrets redacted), `result.json` and `status.json`. `status.json` flips to `completed` only after `result.json` is fully written |
| Scored tasks | `tasks/` | Human-authored tasks and verifiers that check the platform directly |

### Running it

```bash
# 1. What can this credential do? Read-only; run it first.
PYTHONPATH=src python3 -m pipeline_agent.runner --task preflight --mode live

# 2. The canonical task (read-only propose mode by default).
PYTHONPATH=src python3 -m pipeline_agent.runner --task canonical --mode live

# 3. The scored refusal task: checks env and platform, then runs run + verify.
export AGENTSWITCH_BASE_URL=https://agentswitch.theschoolofai.in
export AGENTSWITCH_TOKEN=<token from POST /api/auth/login>
tasks/run_refusal_task.sh
```

`--mode live` reads `AGENTSWITCH_MCP_URL` and `AGENTSWITCH_MCP_TOKEN` from `.env`. In
the scored task, `verify` recomputes the verdict from the run's artifacts and does not
use the process exit code. Its exit codes are 0 pass, 1 fail and 3 blocked.
`run_refusal_task.sh` adds two more: 4 when the environment is not set up, and 5 when
the platform's LLM provider is down.

### Scored task matrix

The cases come from `tasks/README.md`. Tasks and verifiers are written by hand by the
team; the rubric scores AI-written tests at zero. The team's offline module
`tests/test_pipeline_enhancements.py` (added 2026-10-07) is part of the scored
matrix. It runs against in-memory clients, so it checks the agent's logic, not a
postcondition on the live platform.

| Case | Verifier asserts | Status |
|---|---|---|
| Permission refusal (`Invoice`, outside the catalogue) | Precise refusal for the right target; no state change (REST snapshots before and after) | **Implemented** (`tasks/mandatory_refusal_task.py`). **A1–A4 pass on both tenants (2026-10-07)**: the platform's LLM provider, which returned `401` until at least 2026-10-03, works again. Offline: `test_split_request_*` (data-scope refusals keep the in-scope part; action refusals refuse all) |
| Rotting deal | Correct inclusion/exclusion and reported evidence | **Offline**: `test_view_filters_rotting_deals_by_contact_rule` (rot by real contact kept apart from the platform flag). No live verifier yet |
| No recent contact | Correct contact classification, with date and channel evidence | Not started |
| Existing next action | No duplicate activity; result links the existing one | **Offline**: `TestOpenActivityDuplicate` (open action on the deal, or on the customer with no deal, blocks a create; a completed contact is allowed) and `test_loop_blocks_duplicate_create_before_calling_client`. No live verifier yet |
| Missing next action | Exactly one correct activity exists after the run | **Offline**: `test_loop_write_stamps_provenance_and_blocks_second_create` (exactly one create, provenance stamped, a rerun creates nothing). No live verifier yet |
| Concurrent change | Agent re-reads; no stale or duplicate write | Not started |
| Ambiguous or bad stage data | No unsafe invented action; flagged for review | **Offline**: `test_data_issues_include_derived_checks` (a stage the pipeline does not define is reported as a data issue). No live verifier yet |

### Offline regression suite

`tests/` holds 418 offline tests in 19 modules (`.venv/bin/pytest tests -q`; 418 passed
on 2026-10-19). They cover the workflow, guards, preflight, transport, artifacts, the
loop's token budget and Keystone-specific contracts. One module,
`test_pipeline_enhancements.py`, is part of the scored matrix (above). The other 18 are
regression tests that protect the code and are not scored. None of them can prove a
postcondition on the live platform. See `tests/README.md`.

## Central evaluator

The course's central evaluator checks out a submitted commit, runs `install` and `run`
from `agentswitch-harness.toml`, and scores only `results.json`:

```toml
install = "pip install ."
run     = "python -m pipeline_agent.central_runner"
results = "results.json"
instances = ["keystone","suryodaya"]
timeout_minutes = 30
```

`central_runner.py` follows the evaluator's harness rules. It reads only the variables
the evaluator supplies (`AGENTSWITCH_BASE_URL`, `AGENTSWITCH_TOKEN`,
`AGENTSWITCH_INSTANCE`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`). It never
reads `.env` and never hard-codes a platform URL. The package has no third-party
dependencies and runs on Python 3.11. It records seven tasks per instance:
`env-contract`, `mcp-preflight`, `canonical-read-only`, `refusal-guard`,
`model-reachable`, `agent-loop-read-only` and `result-contract`. Every task is
read-only and fails if anything was written. `results.json` is rewritten after each
task, so a timeout or crash still leaves the finished rows.

The agent loop has a hard budget of 140,000 tokens per instance (`LOOP_TOKEN_BUDGET`),
which keeps both instances under the evaluator's 300k per-run cap. When the next call
would leave no room for another, the model is told to answer from what it has read.
That wrap-up call is exempt from the stop, and only one is allowed. The model backend
retries a 429, a 5xx, or the evaluator proxy's upstream 401 (`"The model provider
answered 401."`) once after 10 seconds. A plain 401 for our own key fails straight
away.

To try it locally without the evaluator: `python -m pipeline_agent.central_runner --dry-run`
(stub MCP client, model tasks skipped). The submission branch is `central-eval`.

| Run | Date | Commit | Result |
|---|---|---|---|
| 1, 2 | 2026-10-09, 2026-10-12 | `3394506` | Keystone only, 7/7 each |
| 3 | 2026-10-15 | `277391e` | 14/14 (both instances), 244,757 tokens |
| 4 | 2026-10-18 | `3f91478` | Failed: the evaluator's model returned a 401 on the probe |
| 5 | 2026-10-19 | `6351813` | Failed: the Suryodaya loop hit the 110k budget; Keystone got the provider 401 mid-loop |
| 6 | 2026-10-19 | `da9b7e8` | **14/14**, 158,532 tokens |

Records: `docs/central_run_test_result.md` (Run 3) and `docs/central_runs_4_to_6.md`.
A central pass shows the harness installs and runs in the evaluator. It is not IT-01
evidence.

## Status

Latest status (2026-10-19):
[`docs/pipeline_agent_summary.md`](docs/pipeline_agent_summary.md). In short: central
Run 6 passed 14/14 on `da9b7e8`; IT-01 Run 3 passed on 2026-10-12
(`docs/integration_testing_pipeline_run3.md`); on the shared book the canonical task
reports 25 rotting deals and 8 uncalled customers on Keystone, and 54 and 20 on
Suryodaya (2026-10-19, read-only). The notes below are the 2026-09-22 milestones.

As of 2026-09-22 the agent answers the canonical question against the **live**
Suryodaya book: 81 rotting deals out of 133, with contact status and a next
action for each, in 3 tool calls and about 9 seconds.

**The rot-feedback loop is fixed.** The platform computes a deal's rot from
the time since its last linked Activity - including Activities *this agent
creates* - so writing a next action made a rotting deal read as `fresh` and
drop out of the agent's own report. The agent now recomputes rot with its own
rows excluded, reports both numbers side by side, and warns loudly on any deal
that only looks healthy because the agent wrote to it. See
`docs/open_items.md` for the derivation and the live before/after.

**Gate G1 is resolved.** The `sales`-domain blocker that shaped this repo's
earlier design was not real - the seat reads `Deal` and `Activity` fine.
Access here is scoped by the seat's tool catalogue, not by domain, and the
credential serves 238 entity-scoped tools rather than the 13 generic ones in
the captured snapshot (237 on 2026-09-21 - the catalogue moved, which is why
preflight now baselines it). `docs/open_items.md` has the full list of what that
invalidated and what it changed.

Still outstanding: write mode (`--exec-mode create-next-actions`) has run
exactly once, on one deal, with consent - not at scale; the measured rot
boundary is a range (`[5, 9]` days) rather than a number; neither model
backend has made a real API call; and the scored task matrix, written by the
team, still lacks the no-recent-contact and concurrent-change cases and any live
verifier beyond the refusal task. Full status: `docs/open_items.md`.

## Layout

```
src/pipeline_agent/
  config.py            env-loaded settings, redacted before anything logs them
  mcp/
    client.py           abstract MCPClient - the 13-tool interface, PermissionDeniedError
    real_client.py       JSON-RPC live transport and MCP tool-surface adapter
    stub_client.py        in-memory fixture for the dry-run smoke test
  harness/
    base.py               Step / TaskRun
    loop.py                 the model-driven tool-call loop ("your own loop")
    recording.py             wraps an MCPClient so its calls land in TaskRun.steps
    artifacts.py              persists a run to disk before/while it's scored
  agent/
    contract.py             the input/output contract (Phase 1)
    workflow.py              the deterministic canonical-task logic (Phase 2)
    refusal.py                builds the Track B refusal result (Phase 3)
  preflight.py                 read-only probe: tool catalogue + entity access
  llm.py                        model backends for the loop (anthropic | openrouter | openai | ollama)
  runner.py                    CLI entry point (Phase 4)
  central_runner.py            central evaluator entry point (agentswitch-harness.toml)
tasks/          scored, human-authored tasks + verifiers (refusal task so far) and run_refusal_task.sh
tests/          offline suite, 418 tests in 19 modules; test_pipeline_enhancements.py is part of the scored matrix (see tests/README.md)
docs/           architecture.md, agent_contract.md, open_items.md, pipeline_agent_summary.md, IT-01 and central run records
bugs/           filed platform bug records and bug-hunt fixtures
agentswitch-harness.toml   central evaluator config
runs/           run artifacts land here, gitignored except .gitkeep
```

## Running it

```
PYTHONPATH=src python3 -m pipeline_agent.runner --mode dry-run
PYTHONPATH=src python3 -m pipeline_agent.runner --task preflight --mode live
```

`--task preflight` is the first thing to run against any new credential. It is
read-only (every probe is `list(entity, limit=1)`) and reports what the seat
can actually do: the tool catalogue the credential really exposes, whether the
aggregate `report` tool is in it, and whether the `sales` domain is blocked -
a re-runnable measurement of Gate G1 rather than a remembered result. It flags
drift from the recorded state explicitly:

```
surface=entity_scoped, tools=238, G1=sales_reachable, canonical task READY
  CRMPreferences   crm      ok: readable
  Deal             sales    ok: readable
  Activity         sales    ok: readable
```

It also compares the catalogue against a recorded baseline and prints a
`CHANGED:` line if the seat's tool set has moved - on this platform the tool
catalogue *is* the access boundary, so it is not allowed to change quietly.

The default `--task canonical` runs the canonical request against
`StubMCPClient` - no credentials needed.

`--task loop` runs the model-driven loop instead of the deterministic
workflow: the model chooses each tool call itself, which is what the refusal
task needs to exercise. It requires `MODEL_NAME` as `backend:model`:

```
MODEL_NAME=anthropic:claude-opus-5 \
  PYTHONPATH=src python3 -m pipeline_agent.runner --task loop --mode live \
  --request "<the human-authored task prompt>"
```

Backends are `anthropic:<model>` (needs `pip install 'pipeline-agent[anthropic]'`
and a credential), `openrouter:<model>` (needs `OPENROUTER_API_KEY`), `openai:<model>`
(any OpenAI-compatible endpoint: `OPENAI_BASE_URL` and `OPENAI_API_KEY`; this is what
the central evaluator uses) and `ollama:<model>` (needs a local `ollama serve`).
`--token-budget N` caps the loop's input and output tokens. The
request text is passed through verbatim, so scored task prompts stay
human-authored - see `tasks/README.md`.
Produces a run folder under `runs/`. `--mode live` requires
`AGENTSWITCH_MCP_URL`/`AGENTSWITCH_MCP_TOKEN` (copy `.env.example` to `.env`)
uses JSON-RPC POST at `/api/mcp`; a credentialed smoke test remains required.
`MCP_TOOL_SURFACE` defaults to `entity_scoped`, which is what the seat 07
credential actually serves (238 tools named `Deal.list`, `Activity.create`,
...). Set it to `generic` only for a credential exposing the 13 generic tools
from the captured `agent_tools.json`. The entity-scoped surface has no
aggregate `report` tool at all, so "who has not been contacted" is aggregated
client-side from a single `Activity.list` scan.
