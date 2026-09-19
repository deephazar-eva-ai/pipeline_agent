# Task matrix - human-authored, not scaffolded by AI

Per the capstone grading rubric: **a test written by Claude or Codex scores
zero.** Everything in this directory that becomes a *scored* task or verifier
must be written by a human team member, by hand. This README and
`task_schema.json` are the only things here so far - documentation of the
expected shape, not tasks themselves - so that authorship boundary is never
ambiguous later.

## What the harness gives you to build on

- `pipeline_agent.agent.workflow.run_canonical_task(client, request, run_id=...)`
  - the function every task ultimately exercises. Returns either a
  `CanonicalAnswer` or a `RefusalResult` (see `agent/contract.py`).
- `pipeline_agent.mcp.stub_client.StubMCPClient` - useful for a fast,
  offline sanity check of your own verifier logic, but it is a plumbing
  fixture, not a stand-in for the live platform. A scored verifier must run
  against real AgentSwitch data and read the database directly, not just
  the run artifact's claims (that's the whole point of a direct-database
  verifier per the rubric).
- `pipeline_agent.harness.artifacts` - every run leaves a folder under
  `runs/<timestamp>__<task_id>__<id>/` with `input.json`, `config.json`,
  `result.json`, `status.json`. A verifier can and should read `result.json`
  for the tool-call trace, but must confirm postconditions (e.g. "exactly one
  Activity now exists for this deal") by querying AgentSwitch itself, not by
  trusting the trace.

## The matrix this repo still needs (from capstone_plan.md Phase 5)

| Case | Required observation | Direct verifier assertion |
|---|---|---|
| Rotting deal | Computed rot fields and live threshold | Correct inclusion/exclusion and reported evidence |
| No recent contact | Activity/channel history | Correct contact-status classification and date/channel evidence |
| Existing next action | Open activity already exists | No duplicate activity; result links existing action |
| Missing next action | Eligible deal has none | Exactly one appropriate activity exists after run; fields/linkage correct |
| Concurrent change | Record/action changes before write | Agent re-reads and does not make a stale or duplicate write |
| Permission refusal | `sales` domain unavailable | No state change; precise refusal |
| Ambiguous/bad stage data | Malformed Pipeline stage mapping | No unsafe invented action; flag/review route taken |

`task_schema.json` documents the expected shape of a task file for whichever
runner ends up loading this matrix. It is a schema, not a task - filling in
real task files and their verifiers is deliberately left undone here.
