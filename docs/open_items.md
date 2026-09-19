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
4. **Obtain the AgentSwitch MCP integration contract (auth, transport) before
   deep wiring.** Not obtained. `mcp/real_client.py` deliberately raises
   `NotImplementedError` rather than guess at one - see that file's docstring.
5. **Re-fetch full IDs/evidence for the two platform data-bug candidates
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

## What this repo does NOT do (and shouldn't, yet)

- Author the scored task matrix or its verifiers. Per the grading rubric, an
  AI-authored test scores zero - `tasks/README.md` and `tests/README.md`
  explain what's expected there and leave it to the human team member.
- Implement `RealMCPClient.call_tool`. Doing that before item 4 above is
  answered means guessing at a wire format that may need retrofitting later.
