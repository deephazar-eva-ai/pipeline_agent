# Architecture (Phase 0 exit artifact)

```
runner.py          CLI entry point (--task canonical | preflight)
  -> agent/workflow.run_canonical_task   deterministic Track A/B logic
  -> preflight.run_preflight             read-only capability + access probe
       -> mcp/client.MCPClient           abstract tool-call interface
            -> mcp/stub_client.StubMCPClient    (dry-run: in-memory fixture)
            -> mcp/real_client.RealMCPClient    (live: JSON-RPC POST /api/mcp)
  -> harness/recording.RecordingMCPClient        records every call into TaskRun.steps
  -> harness/artifacts.run_artifact               persists the run BEFORE scoring
```

## Preflight: measuring the seat instead of assuming it

`--task preflight` answers three questions this repo previously could only
assert: which tool catalogue the credential actually exposes (measured via
`tools/list`, not read from `MCP_TOOL_SURFACE`), whether the aggregate
`report` tool the canonical task needs is in it, and whether the `sales`
domain is genuinely blocked right now.

It is read-only by construction - every probe is `list(entity, limit=1)` -
and it records each expected-vs-actual outcome, so a *change* in the seat's
policy shows up as a `CHANGED:` line rather than being silently absorbed. Gate
G1 therefore becomes a dated, re-runnable measurement instead of a remembered
result from a chat transcript. Run it first against any new credential.

Note that a preflight proving the seat is blocked still sets
`claimed_success: false` in its artifact: the probe worked, but the seat
cannot do its job, and those must not look the same to a scorer.

A second, independent path exists for free-form/model-driven tasks (mainly the
refusal task, and any future task that needs judgement rather than fixed
logic):

```
harness/loop.run_loop(task, client, llm, model)     <- runner --task loop
  -> llm.build_llm(MODEL_NAME)   anthropic:<model> | ollama:<model>
```

This is "the loop" in the harness sense - the model picks a tool call each
step via a small JSON action protocol, exactly the shape the grading rubric
means by "your own loop." It is deliberately NOT used for the canonical
task's safety-critical parts (rot detection, contact classification, the
create-Activity idempotency guard) - those are plain Python in
`agent/workflow.py`, per capstone_plan.md Phase 2's own instruction: "do not
leave required safety checks to free-form prompting."

`--task loop` passes `--request` straight through, so the prompt for any
scored task stays human-authored (`tasks/README.md`). The loop records its own
steps, so the runner hands it the raw client rather than the recorder - wrapping
it would log every call twice.

`llm.py` keeps the repo's stdlib-only stance: the Anthropic SDK is imported
lazily inside its own backend, so `pip install` is needed only if you select
it, and the other two tasks never import the module at all.

### The no-retry guard is the harness's job, not the prompt's

`SYSTEM` tells the model not to retry a refused call, but `MAX_REPEAT_DENIALS`
enforces it: a second denied call to the same `(tool, entity)` force-stops the
run with `ended="refused"`. A model that ignores the instruction and then
claims success cannot produce a `claimed_success: true` artifact - the guard
trips before its `done` action is ever read.

## How the canonical answer is actually computed

Three reads, then pure Python:

1. `CRMPreferences.list` - the live `deal_rot_days` threshold.
2. `Deal.list` (one page, 133 records) - `_rot_level` and `_rot_days` are
   computed server-side and returned per record, but are **not** filterable
   (absent from the tool's `inputSchema`), so selection is client-side.
3. `Activity.list` (one full scan) - indexed by deal *and* by party.

That third index is load-bearing rather than an optimisation. No live Activity
carries a `deal_id`; all 175 carry a `party_id`. Indexing only by deal reports
every deal as never-contacted, which is a statement about an unused foreign
key, not about whether anyone called the customer. The linkage that proved
each verdict travels into the evidence as `basis: deal | party | none`, because
"no activity links to this deal" and "this customer has never been contacted"
are different claims.

Scanning once also removed an N+1: the per-deal version issued one
`Activity.list` per candidate, which was 81 sequential round trips and a
two-minute run. The re-read immediately before a write stays per-deal - that
one is the concurrency guard, not a lookup.

## Why two MCP client implementations

The captured OpenAPI contract specifies one JSON-RPC 2.0 POST per request at
`/api/mcp`, bearer authentication, no SSE stream, and the standard MCP
initialize/initialized handshake. `RealMCPClient` now implements that
contract with the stdlib-only `JsonRpcTransport`. A live credentialed smoke
test remains outstanding; the code is an implementation of the snapshot, not
proof that a particular seat credential is configured correctly.

- `RealMCPClient` supports both known catalogue shapes. The default `generic`
  surface uses the 13 seat tools (`list`, `report`, and so on), with the entity
  in arguments. `entity_scoped` translates generic calls to names such as
  `Deal.list`; unsupported generic operations fail loudly at the boundary.
- `StubMCPClient` is a small, explicitly-labeled in-memory fixture (one
  `CRMPreferences` record, and a `PermissionDeniedError` on `Deal`/`Activity`
  matching the already-verified real refusal) that lets every other piece of
  the harness - the loop, the recorder, the artifact writer, the workflow's
  refusal branch - be exercised today. It is not a substitute for a live
  verifier; see `tests/README.md`.

## Exit condition (checked 2026-09-19)

`PYTHONPATH=src python3 -m pipeline_agent.runner --mode dry-run` runs the
canonical request against `StubMCPClient` and produces a run folder under
`runs/` containing:

- `input.json` - the task/request as given
- `config.json` - redacted settings (no secrets, even when none are set)
- `result.json` - the full `TaskRun`: tool-call trace (`steps`), final
  answer, timestamps, `ended` reason
- `status.json` - flips `running` -> `completed` only after `result.json`
  is fully written

The trace in that run shows exactly two things: a successful `list(CRMPreferences)`
(the harmless read) and a clean `refused` on `list(Deal)` (the sales-domain
exclusion) - the same two outcomes independently verified against the live
platform earlier in this project. Nothing here claims that a live MCP
credential has been proven; that step is still blocked on Gate G1 and a
credentialed smoke test (`docs/open_items.md`).

## Dependency stance

This repo has no runtime dependencies (stdlib only) and no import of the
sibling `pipeline/S18Code` or `pipeline/glc_v5` projects. The *pattern* is
adapted from S18Code's `Step`/`TaskRun`/loop split - the code is not.
