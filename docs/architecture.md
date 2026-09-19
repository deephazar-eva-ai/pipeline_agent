# Architecture (Phase 0 exit artifact)

```
runner.py          CLI entry point
  -> agent/workflow.run_canonical_task   deterministic Track A/B logic
       -> mcp/client.MCPClient           abstract tool-call interface
            -> mcp/stub_client.StubMCPClient    (dry-run: in-memory fixture)
            -> mcp/real_client.RealMCPClient    (live: unimplemented, see below)
  -> harness/recording.RecordingMCPClient        records every call into TaskRun.steps
  -> harness/artifacts.run_artifact               persists the run BEFORE scoring
```

A second, independent path exists for free-form/model-driven tasks (mainly the
refusal task, and any future task that needs judgement rather than fixed
logic):

```
harness/loop.run_loop(task, client, llm, model)
```

This is "the loop" in the harness sense - the model picks a tool call each
step via a small JSON action protocol, exactly the shape the grading rubric
means by "your own loop." It is deliberately NOT used for the canonical
task's safety-critical parts (rot detection, contact classification, the
create-Activity idempotency guard) - those are plain Python in
`agent/workflow.py`, per capstone_plan.md Phase 2's own instruction: "do not
leave required safety checks to free-form prompting."

## Why two MCP client implementations

AgentSwitch's MCP transport/auth contract has not been confirmed against this
repo (see `docs/open_items.md`, item 4 - this was flagged as an open item
before any of this code was written, not discovered after). Rather than guess
at a wire format:

- `RealMCPClient.call_tool` raises `NotImplementedError` with a clear pointer
  to what's missing, so nothing pretends to talk to the live platform until
  the contract is actually known.
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
platform earlier in this project. Nothing here claims the live MCP integration
itself has been proven; that step is still blocked on Gate G1 and the
transport contract (`docs/open_items.md`, items 1 and 4).

## Dependency stance

This repo has no runtime dependencies (stdlib only) and no import of the
sibling `pipeline/S18Code` or `pipeline/glc_v5` projects. The *pattern* is
adapted from S18Code's `Step`/`TaskRun`/loop split - the code is not.
