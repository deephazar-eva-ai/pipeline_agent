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
| Model-driven loop | `src/pipeline_agent/harness/loop.py` | "Our own loop": the model picks each tool call. A second denied call to the same tool/entity force-stops the run, so a model cannot retry past a refusal |
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
`tests/test_pipeline_enhancements.py` (25 tests, added 2026-10-07) is part of the scored
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

`tests/` holds 297 offline tests in 14 modules (`.venv/bin/pytest tests -q`). They cover
the workflow, guards, preflight, transport, artifacts and Keystone-specific contracts.
One module, `test_pipeline_enhancements.py`, is part of the scored matrix (above). The
other 13 are regression tests that protect the code and are not scored. None of them can
prove a postcondition on the live platform.

## Status

Latest measured status (2026-10-03, both tenants):
[`docs/pipeline_agent_summary.md`](docs/pipeline_agent_summary.md). The notes below
are the 2026-09-22 milestones.

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
  llm.py                        model backends for the loop (anthropic | ollama)
  runner.py                    CLI entry point (Phase 4)
tasks/          scored, human-authored tasks + verifiers (refusal task so far) and run_refusal_task.sh
tests/          offline suite, 297 tests; test_pipeline_enhancements.py is part of the scored matrix (see tests/README.md)
docs/           architecture.md, agent_contract.md, open_items.md
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
and a credential) and `ollama:<model>` (needs a local `ollama serve`). The
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
