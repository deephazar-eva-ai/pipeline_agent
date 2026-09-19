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
2026-09-19: Phases 0, 1, 2, 4 have working code; Phase 3's mechanism is live
(the sales-domain refusal is the actual, current behavior, not a placeholder);
Phase 5 is deliberately left to a human team member. Full status, including
what's genuinely unresolved (Gate G1, the MCP transport contract) rather than
just unbuilt: `docs/open_items.md`.

## Layout

```
src/pipeline_agent/
  config.py            env-loaded settings, redacted before anything logs them
  mcp/
    client.py           abstract MCPClient - the 13-tool interface, PermissionDeniedError
    real_client.py       live transport - unimplemented on purpose, see docs/open_items.md
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
  runner.py                    CLI entry point (Phase 4)
tasks/          human-authored task matrix goes here (schema only, so far)
tests/          human-authored verifiers go here (empty on purpose - see tests/README.md)
docs/           architecture.md, agent_contract.md, open_items.md
runs/           run artifacts land here, gitignored except .gitkeep
```

## Running it

```
PYTHONPATH=src python3 -m pipeline_agent.runner --mode dry-run
```

Runs the canonical request against `StubMCPClient` - no credentials needed.
Produces a run folder under `runs/`. `--mode live` requires
`AGENTSWITCH_MCP_URL`/`AGENTSWITCH_MCP_TOKEN` (copy `.env.example` to `.env`)
and currently fails on its first call - see `docs/open_items.md` item 4.
