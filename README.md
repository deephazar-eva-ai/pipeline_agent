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

## Status

Implementing in phases per `capstone_plan.md` (kept outside this repo). As of
2026-09-21: Phases 0, 1, 2, 4 have working code; Phase 3's mechanism is live
(the sales-domain refusal is the actual, current behavior, not a placeholder);
Phase 5 is deliberately left to a human team member. Gate G1 is still
unresolved, but is now *measured* by `--task preflight` rather than asserted.
The model-driven loop is reachable via `--task loop` with a pluggable backend
(`anthropic:` or `ollama:`). Still outstanding: no live credential has ever
exercised the JSON-RPC transport, and neither model backend has been run
against a real model. Full status: `docs/open_items.md`.

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
tasks/          human-authored task matrix goes here (schema only, so far)
tests/          human-authored verifiers go here (empty on purpose - see tests/README.md)
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
surface=generic, tools=13, G1=sales_blocked, canonical task BLOCKED
  CRMPreferences   crm      ok: readable
  Deal             sales    refused: entity 'Deal' is in domain 'sales', ...
  Activity         sales    refused: entity 'Activity' is in domain 'sales', ...
```

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
Set `MCP_TOOL_SURFACE=generic` for the seat's 13 generic tools (the default),
or `entity_scoped` for a standard MCP catalogue exposing names such as
`Deal.list`. The latter does not expose the aggregate `report` tool required
by the canonical task, so it fails closed at that point rather than producing
an incomplete answer.
