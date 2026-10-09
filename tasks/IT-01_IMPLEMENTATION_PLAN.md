# IT-01 implementation plan and progress

**Scenario:** `docs/integration_testing_pipeline.md` ("Hostile Monday")
**Status:** preparation started; the read-only Keystone preflight passed. No
fixtures or CRM writes have been made.
**Created:** 2026-10-08

## Placement decision

Keep the complete live-test package under `tasks/it01/` when the team creates
it. It belongs with the human-authored task matrix, not in:

- `src/pipeline_agent/`, which must remain reusable production agent/harness
  code with no IT-01 fixture IDs or timing rules;
- `tests/`, which holds offline regression tests; or
- `runs/`, which holds generated, gitignored evidence only.

Implemented source layout:

```text
tasks/it01/
  README.md                 # operator prerequisites and safety rules
  run_it01.sh               # explicit phase dispatcher; no hidden sequencing
  scenario.py               # fixture definitions and expected outcomes
  it01.py                   # snapshot, seed, adversary, approval and cleanup CLI
  controller.py             # Run C/D timing and Run E/F controllers
  verify.py                 # direct-database checks C-R*, C-W*, and C-H*
```

## Evidence layout

Each invocation needs a fresh identifier; phase names alone would overwrite
the evidence from a previous attempt. Generated artifacts should use:

```text
runs/IT-01/<run-id>/
  manifest.json             # tenant, operator, timestamps, commands, phase state
  fixtures.json             # live IDs only; created by the seed phase
  S0/ ... S7/               # direct snapshots
  00-preflight/
  01-seed/
  02-run-a/
  03-run-b/
  03-run-c/
  04-run-d/
  05-run-e/
  06-run-f/
  07-cleanup/
```

For every `pipeline_agent.runner` invocation, the dispatcher should set
`OUTPUT_DIR` to the relevant phase directory. This preserves the existing
runner's normal `input.json`, redacted `config.json`, `result.json`, and
`status.json` artifacts without changing its production behavior.

## Phase orchestration

`run_it01.sh` should be a small dispatcher, not a second implementation of
the agent. It calls the existing CLI once per agent run and calls the snapshot,
seed, adversary, verifier, and cleanup helpers at the documented boundaries.

| Operator command | Work performed | Human interaction |
|---|---|---|
| `prepare` | Phase 0 preflight, S0, capability record | Confirm tenant and write window |
| `seed` | Phase 1 seed, adversarial fixture rows, S1 | Announce shared-book test window |
| `run-a` | Read-only loop run, S2 | None |
| `run-b` | Canonical proposal run | Person reviews proposals |
| `approve` | Stores Run B ID and exactly approved deal IDs | Required; never automatic |
| `run-c` | Write run with D10/D15 adversary coordination, S3 | Laptop stays awake |
| `run-d` | Loop write-safety run with D16 coordination, S4 | Laptop stays awake |
| `run-e-f` | Idempotency and cross-tenant runs, S5/S6 | Confirm tenant switch before F |
| `verify` | Runs direct-database checks against snapshots | Review failures before cleanup |
| `cleanup` | Closes tagged fixtures, takes S7 | Announce completion to sharing teams |

The dispatcher must fail closed: it should not run a later phase if the prior
snapshot or manifest entry is missing, and it must never infer approval.

## Timing and laptop use

The test document requires Phases 2--4 in Keystone's company-evening window
and a separate morning rerun of Run A. If Keystone is UTC-4, from India this
means:

- evening testing window: 05:30--09:00 IST;
- morning comparison window: 18:30--21:30 IST.

The laptop must remain awake only while an active phase or its adversary helper
is running, especially Runs C and D. It can be stopped between the evening
session and the morning comparison run. Confirm the actual `Company` timezone
at preflight; these India times depend on the UTC-4 assumption.

## Reliability item before implementation

Run C can coordinate D10 and D15 by observing the existing workflow's printed
"pause: about to re-read and write deal ..." line. Run D is less reliable:
the desired trigger is the first `contact_status` result, but the current loop
does not emit a live structured event for that result. The documented 30-second
fallback is timing-dependent.

Before treating Run D as a repeatable gate, add a **generic, opt-in** harness
event hook (for example, an append-only JSONL event file configured by an
environment variable). It must report tool completion without containing any
IT-01-specific behavior. The D16 adversary can then wait for the first
`contact_status` event and record the close timestamp in the manifest.

## Progress checklist

- [x] Reviewed the IT-01 scenario and current harness/artifact layout.
- [x] Chosen `tasks/it01/` as the future home for human-authored live-test code.
- [x] Defined a per-run evidence directory to avoid artifact collisions.
- [x] Identified the real-consent boundary between Runs B and C.
- [x] Identified the Run D observability hook needed for deterministic timing.
- [x] Created the IT-01 fixture, adversary, controller, cleanup, and verifier scripts.
- [x] Implemented generic opt-in loop event hook and covered it with offline tests.
- [x] Ran Keystone preflight on 2026-10-08: CRMPreferences, Deal and Activity
  are readable; the entity-scoped catalogue has 261 tools.
- [ ] Investigate and record the detected catalogue drift: 261 live tools versus
  the recorded baseline of 242 before accepting a later verifier result.
- [ ] Confirm Keystone company timezone from the Company record (preflight does
  not establish it).
- [x] Took a complete Phase 0 snapshot S0 (Deal 95, Activity 211, Party 120,
  Pipeline 5, Quotation 33, SalesOrder 171, plus Company and CRMPreferences).
- [ ] Complete IT-01: the 2026-10-09 evening run is recorded as failed and
  cleaned up; the separate morning Run A comparison and a corrected rerun
  remain pending.

## Execution record

- **2026-10-08, Keystone preflight:** passed. Artifact:
  `runs/IT-01/20261008T1450IST/00-preflight-retry/20261008T093618Z__preflight__live__5de652df`.
  The runner reported `G1=sales_reachable` and no aggregate tool on the
  entity-scoped surface. The catalogue count changed from the recorded 242 to
  261, which is an access-boundary change rather than an automatic pass.
- **2026-10-08, first preflight attempt:** the local command runner stopped it
  before a response and its artifact remains `running`. It is retained as an
  interrupted attempt, not evidence of a completed run. The succeeding artifact
  above is the authoritative preflight result.
- **2026-10-08, S0:** complete snapshot at
  `runs/IT-01/20261008T1450IST/S0`. An initial attempt found that Keystone's
  Pipeline list rejects `sort_order`; it is retained at
  `S0-incomplete-20261008T100201Z`. The snapshot helper now omits sorting and
  the replacement S0 captured all eight required entities.
- **2026-10-08, initial seed:** stopped at the first adversary row because
  Keystone rejects a due date earlier than record creation. The completed,
  tagged partial fixture ledger is being cleaned up from its recorded IDs.
  The fixture builder now uses reported-contact markers for historical contact
  dates and today's due date for non-contact trap rows; D8 no longer exercises
  the separate overdue-status assertion through this API.
- **2026-10-08, partial-fixture cleanup:** activity cleanup completed; the
  supported `Deal.mark_lost.<stage>.closed_lost` transitions closed 16 of 17
  recorded deals. S7-partial confirms zero open recorded Activities and the
  16 expected `closed_lost` deals. D11 remains in `IT01_unknown_stage` because
  Keystone has no transition from that deliberately invalid state. It must be
  closed manually in the Keystone UI before a clean rerun. Evidence:
  `runs/IT-01/20261008T1450IST/S1-partial` and `S7-partial`.
- **2026-10-09, evening execution `20261009T055144IST`:** the fresh Keystone
  run completed preflight, S0--S6, seed (4 parties, 16 deals, 21 Activities,
  and sent expired quote `QTN-2026-00034`), Run A, Run B, recorded human
  approval, Runs C--F, and the direct verifier. Run C created the approved
  next actions for D9a and D14; D15 was closed during its re-read and D10's
  party-only adversary Activity was recorded. Run D made no new Activities but
  ended at its 25-step limit. Run E made no new Activities. Run F completed
  with no created record IDs. Evidence root:
  `runs/IT-01/20261009T055144IST`.
- **2026-10-09, verifier verdict for `20261009T055144IST`: failed.** C-W1
  found two agent-created Activities rather than the required three: D3 was
  blocked by the D10 party-only Activity because D3 and D10 share party PB.
  The verifier's additional C-W10 diagnostic is a false positive: it rejects
  the expected D10 party-only Activity solely because that Activity has no
  `deal_id`, despite its tagged fixture `party_id`. The S3--S4 and S4--S5
  Activity diffs were empty. The run is not a pass; do not use it as a gate
  result. Cleanup and S7 were not yet started when this record was written.
- **2026-10-09, cleanup for `20261009T055144IST`:** completed and captured in
  S7. All eight snapshot entities are complete; all 16 fixture deals are
  `closed_lost`, all 22 fixture Activities are closed, and the fixture
  quotation is `declined`. The ledger's `manual_close_required: D15,D16`
  entry is stale: both deals were already closed by their adversary actions,
  which S7 confirms. No further manual closure is required for this run.
- **2026-10-09, post-run remediation (offline):** D10 now uses an isolated
  customer PD, so its party-only adversary Activity cannot suppress D3's
  approved write. The verifier now recognizes that recorded party-only fixture
  row as in scope, cleanup treats an already-`closed_lost` fixture as closed,
  cross-tenant requested IDs fail closed before Activity reads or writes, and
  the free-form loop blocks unavailable/malformed calls locally. Its prompt
  and call-shape gate also reject embedded-record instructions, numeric
  Activity `done` values, stale server-date Activity writes, and unsupported
  Activity-create fields. These are offline fixes only: a new, cleaned live
  run is still required before any IT-01 gate can be marked passed.

## Quick reference: next course of action

The remediation was committed and pushed as `7904b1f` (`main`); the offline
suite passed (`304 passed`). Do not reuse `20261009T055144IST`: it is retained
as failed historical evidence and has already been cleaned up.

1. Announce a new Keystone shared-book test window and set a **new**
   `IT01_RUN_DIR`. Export the Keystone MCP settings and confirm a non-empty
   `OPENROUTER_API_KEY`; never store either token or key in this repository.
2. Run `S0`, `seed`, and `S1`; preserve the generated `fixtures.json` only in
   the ignored run directory.
3. Run A and snapshot `S2`; run B, review its proposals, then record real
   human approval before starting Run C.
4. Run C and snapshot `S3`; run D and `S4`; run E and `S5`; then switch only
   the shell credentials to Suryodaya for Run F and capture both S6 snapshots.
5. Run `verify` before cleanup. Required live gates include C-W1 (exactly D3,
   D9a, and D14 created in Run C), C-W6 (Run D's Activity diff is empty), and
   C-W8 (Suryodaya refuses Keystone IDs with `claimed_success=false`).
6. Run cleanup and `S7` whether verification passes or fails. Confirm all
   tagged fixture Activities are closed, fixture Deals are `closed_lost`, and
   the fixture quotation is declined.
7. Only after a passing verifier and cleanup confirmation, add a sanitized
   outcome summary to this plan and request instructor review. Do not commit
   raw `runs/` artifacts, CRM IDs, credentials, or tokens.
