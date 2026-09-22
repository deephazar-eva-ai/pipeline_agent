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
backend has made a real API call; and the scored task matrix is deliberately
left to a human team member. Full status: `docs/open_items.md`.

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
