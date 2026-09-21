# Open items

Carried over from `capstone_plan.md` §3 (Gate G1) and §5. None of these are
resolved by writing code - they need either a decision from the platform
owner, a live smoke test against real credentials, or a human's own test
authorship. Status here is honest about what's actually been checked, not
what's merely been coded for.

## Gate G1 - charter/access mismatch (blocking full Track A)

**Status: unresolved.** Seat 07's charter requires `Deal`/`Activity`; its
policy excludes the `sales` domain those entities live in. This repo's code
already handles the situation correctly (`agent/workflow.run_canonical_task`
returns a `RefusalResult`, never a fabricated answer), but "handles it
correctly" and "resolved" are different things. The actual question - is the
`sales` exclusion intentional, and should it change - still needs an answer
from whoever owns this seat's policy config. Track A (the full three-part
answer) cannot be implemented beyond what's here until that answer arrives
and a live smoke test confirms `Deal.list`/`Activity.list` actually work.

**How to re-check it (new):** `--task preflight` now measures this rather
than trusting the 2026-09-18 transcript. It probes `CRMPreferences`, `Deal`
and `Activity` read-only and reports a `gate_g1` verdict of `sales_blocked`,
`sales_reachable`, or `inconclusive`, flagging any drift from the recorded
state as a `CHANGED:` line. Run it against a live credential the moment one
exists; if it comes back `sales_reachable`, G1 is resolved and item 2 below
becomes the next blocker.

## Checklist (capstone_plan.md §5)

1. **Resolve the `sales` access/charter mismatch (G1).** Unresolved - see above.
2. **Live-smoke-test `report(Activity, max(due_date), group_by=deal_id, done=true)`.**
   Not done. `agent/workflow.load_last_contacted_map` is written to this
   documented shape but has never executed against a live server - it's
   currently unreachable in dry-run because the `Deal` refusal short-circuits
   first, and there's no live credential in this environment to test it with.
3. **Reconcile whether `core` is genuinely accessible.** Not needed yet -
   nothing in this codebase depends on the `core` domain. Revisit only if a
   future design needs it.
4. **Credentialed MCP smoke test.** The captured OpenAPI contract is now
   implemented: JSON-RPC 2.0 POST to `/api/mcp`, bearer auth, no SSE, standard
   initialize/initialized handshake. This remains **unverified against a live
   seat credential**. `--task preflight --mode live` is now the one command
   that performs this check: it runs the handshake, enumerates the catalogue,
   and does the read-only `CRMPreferences` probe, all in one persisted run.
   The catalogue shape no longer has to be configured correctly in advance -
   preflight detects it and says so when `MCP_TOOL_SURFACE` disagrees.
   `generic` is required for the canonical task's aggregate `Activity.report`;
   `entity_scoped` cannot express it and intentionally fails closed.
5. **Run `--task loop` against a real model.** The loop is now reachable
   (`llm.py`, backends `anthropic:<model>` and `ollama:<model>`), and its
   control flow is checked against a scripted stand-in model: a clean refusal,
   a retry that correctly trips `MAX_REPEAT_DENIALS`, an allowed read, and an
   unparseable reply. **Neither backend has issued a single real API call** -
   no key and no local daemon in the environment where it was written. Run
   each once before relying on it; the failure modes to expect are transport
   (wrong host/key) rather than logic.

6. **Re-fetch full IDs/evidence for the two platform data-bug candidates
   before filing** (malformed Pipeline stage records; the Goal
   target/current_value anomaly). Not done in this repo - that work belongs
   with the bug-filing effort tracked in `EAG_V3_capstone/bugs/mybugs.json`,
   not here.

## What IS checked, as of 2026-09-19

- The harness runs end-to-end against `StubMCPClient`: a harmless read
  (`CRMPreferences`) and a clean refusal (`Deal`) both land correctly in a
  persisted, `status=completed` run artifact under `runs/`. See
  `docs/architecture.md`'s exit-condition section.
- No write is attempted anywhere on the refusal path - confirmed by reading
  the produced `result.json`, not by trusting the code's own claim.
- `--task preflight --mode dry-run` produces a four-step trace (`tools/list`,
  `list(CRMPreferences)` ok, `list(Deal)` refused, `list(Activity)` refused)
  with `created_record_ids: []` and `claimed_success: false`. Checked
  2026-09-21 by reading the artifact, not the code.
- The same command against an unreachable host returns `gate_g1:
  inconclusive`, not `sales_blocked` - a network fault must never be reported
  as a policy refusal. Checked 2026-09-21.
- `run_loop`'s refusal handling, under a scripted stand-in model: it stops a
  retry of a denied call via `MAX_REPEAT_DENIALS`, and the offending model's
  later `claimed_success: true` never reaches the artifact because the guard
  trips first. Checked 2026-09-21.

## What this repo does NOT do (and shouldn't, yet)

- Author the scored task matrix or its verifiers. Per the grading rubric, an
  AI-authored test scores zero - `tasks/README.md` and `tests/README.md`
  explain what's expected there and leave it to the human team member.
- Treat the current transport implementation as live-proven before the
  read-only credentialed smoke test in item 4 succeeds.
