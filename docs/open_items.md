# Open items

Status is honest about what has actually been checked against the live seat,
not what has merely been coded for. Everything dated 2026-09-21 or 2026-09-22
below was measured against the real `team07` credential on
`https://agentswitch.theschoolofai.in`.

## Gate G1 - RESOLVED 2026-09-21

**The `sales` exclusion was not real.** The seat 07 credential reads `Deal`
and `Activity` without complaint, and `--task preflight --mode live` reports
`G1=sales_reachable` with all three probes green. The whole "charter requires
a domain its policy excludes" blocker - which shaped this repo's design, its
refusal path, and most of its earlier documentation - came from a single
2026-09-18 observation made through a different door, and was wrong for this
credential.

Two things followed from that, both now fixed:

- **Access on this platform is not domain-shaped.** A seat is scoped by which
  tools appear in its `tools/list`. Refusals say *"This tool is not available
  to your seat"* and never name a domain, which is why `EXCLUDED_DOMAINS` is
  now empty and `PermissionDeniedError` phrases itself in terms of the tool
  catalogue.
- **The refusal detector missed the platform's actual refusal.** The
  "not available to your seat" message matched none of the markers in
  `_is_permission_denied`, so a genuine policy refusal arrived as a generic
  `MCPToolError`: the workflow would never have built a `RefusalResult`, and
  the loop's no-retry guard - which keys on `PermissionDeniedError` - would
  never have engaged. Fixed and verified against `Invoice`, `SalarySlip` and
  `Contract` (all refused) versus `Deal` (readable).

## The surface was wrong too

The credential serves **entity-scoped tools** (`Deal.list`,
`Activity.create`, `Deal.qualify`, ...), not the 13 generic tools in the
captured `agent_tools.json` - 237 of them on 2026-09-21, 238 on 2026-09-22
(see "Found 2026-09-22" below). `MCP_TOOL_SURFACE` now defaults to
`entity_scoped`. Three concrete consequences, all measured:

- **Filters are top-level arguments.** The entity-scoped schemas declare every
  filter and writable field as a top-level property with
  `additionalProperties: false`, so the generic surface's `filters` / `data`
  envelopes are rejected with a bare *"Invalid tool arguments"*. `to_wire`
  now unwraps them.
- **There is no aggregate tool at all.** `report(Activity, max,
  group_by=deal_id)` - the documented plan for "who has not been contacted" -
  does not exist on this surface. Aggregation is now client-side over one
  full `Activity` scan.
- **`Activity.create` requires `subject`, `type` and `due_date`**, and rejects
  unknown keys. The previous payload sent `stage` and `notes`, which are not
  Activity fields, and omitted all three required ones - every write would
  have failed.

## Data findings worth filing as platform bugs

Neither is an agent bug; both change what a correct answer looks like.

1. **Three sources of truth for the rot threshold, and the one that is
   actually used is not exposed at all.** `GET /api/deal-rot-config` returns
   `default_rot_days: 14`; `CRMPreferences.deal_rot_days` returns `30`; and
   the boundary at which `_rot_level` actually flips `fresh` -> `attention`
   is neither. Measured 2026-09-22 across the 83 agent-untouched deals: the
   oldest `fresh` deal is 4 days, the youngest `attention` deal is 9 days, so
   the real boundary lies in **[5, 9]** and no deal sits in the gap to pin it
   down. 14 is consistent with a half-of-`default_rot_days` rule, but that is
   a guess and this repo does not act on it - `calibrate_rot_boundary` brackets
   the boundary from live data on every run and takes the conservative lower
   bound, reporting the bracket in the answer's summary.
2. **`Activity.deal_id` is never populated.** 0 of 175 activities carry one;
   all 175 carry `party_id`. Any deal-scoped contact reporting is therefore
   silently empty rather than wrong-looking - this agent reported *every* deal
   as never-contacted until the party fallback was added. 35 parties are
   shared between deals and activities.

`_rot_level`'s real vocabulary is `fresh` / `attention` / `none` (not
`aging` / `stale`, which this repo filtered on and which never occur), and
`none` is exactly the 49 closed deals.

## Writing a next action resets the signal that detected the rot - FIXED 2026-09-22

Measured on the 2026-09-21 smoke test: creating one `Activity` against deal
`7de1dd77` moved it from `_rot_level: attention, _rot_days: 8` to `fresh, 0`,
and the candidate count dropped 81 -> 80.

**This is a feedback loop, and it was the most dangerous thing in this repo.**
The agent's own bookkeeping made a deal look healthy. Logging a task is not
the same as contacting a customer, so a scheduled run would have quietly
laundered every rotting deal into `fresh` without a human speaking to anyone.

### The platform's formula, derived rather than assumed

A second day of data made the rule measurable instead of merely suspected. On
2026-09-22, for all 84 open deals, with **zero mismatches**:

```
_rot_days == (now - max(deal.updated_at,
                        latest Activity.created_at linked to that deal)).days
```

`Activity.due_date` is ruled out - it mismatches on exactly one deal, the one
the agent wrote to. Closed deals are pinned at `_rot_days: 0` /
`_rot_level: none` regardless of true age (theirs range 4..9 days), which is
why closed state is read off the stage and never inferred from the level.

The natural experiment confirmed the laundering over a full day: deal
`7de1dd77` read `attention`/8 on 2026-09-21, the agent logged one task, and on
2026-09-22 it read `fresh`/0 while all 80 untouched peers aged 8 -> 9.

### What now defends against it

`workflow.independent_rot_days` recomputes that exact formula from inputs the
agent has not touched - Activity rows carrying the `created_by=pipeline_agent`
provenance marker are excluded. On an untouched deal it reproduces the
platform's number exactly; on a written deal it reports what the platform
*would* have said. A deal is a candidate if **either** signal flags it, so the
agent's own writes can no longer remove a deal from its own report.

Verified live on 2026-09-22: the canonical run reports **81** candidates again,
not 80. Deal `7de1dd77` is back, sorted first at 9 independent days, carrying
`agent_write_suppressed_platform_signal: true` and this row-level note:

> the platform reads this deal as 'fresh' only because this agent logged an
> Activity against it; with the agent's own rows excluded it has been
> untouched for 9 days and nobody has contacted the customer

The run summary carries a matching `WARNING:` line with the count, because an
aggregate at the bottom of a 81-row report is not where a human looks.

Two related honesty fixes went in with it:

- An open Activity **the agent itself wrote** no longer reports as a bland
  "an open Activity already exists". It is still not duplicated - that part was
  right - but the reason now says it is the agent's own unactioned note and
  that a human needs to act on it. "Somebody is on this" and "the agent left
  itself a note" are identical in the data and opposite in meaning.
- Candidates sort by the **recomputed** rot days, not the platform's. Sorting
  by the platform's number sends every already-written deal to the bottom at 0
  days, so a `--limit` run would never reach the deals the agent had been
  hiding.

### What is still true, and still a platform bug worth filing

The underlying platform behaviour is unchanged: `_rot_days` still treats an
agent-logged task as contact. This repo defends itself, it does not fix the
platform. Anyone else's agent on this book has the same problem and no
defence.

One consequence noted on 2026-09-21 still stands: the re-run on 2026-09-21 did
not exercise the idempotency guard, because the deal fell out of the candidate
set before the guard could run. With this fix the deal stays in the set, so the
guard is now reachable on live data - but it has still not been *exercised*,
because the deal now short-circuits at "an open Activity already exists"
instead. Exercising the re-read-before-write branch needs a deal with no open
activity at all, written twice.

## Found 2026-09-22 while probing, both fixed

1. **The refusal message named a domain the platform never mentioned.**
   `get_schema` is not in this seat's catalogue, so `get_schema(Activity)`
   refuses with *"This tool is not available to your seat"*. Because
   `ENTITY_DOMAIN` maps `Activity` to `sales`, `PermissionDeniedError`
   rendered that as *"entity 'Activity' is in domain 'sales', outside this
   seat's policy scope"* - a claim contradicted by the preflight run one
   minute earlier, which read `Activity` fine. That is the Gate G1 error
   again, generated fresh by this repo's own code. The wording is now decided
   by what the server actually said (`PermissionDeniedError.cites_domain`),
   `RefusalResult.missing_domain` is empty when no domain was named, and both
   were re-verified live against `get_schema(Activity)` and `list(Invoice)`.

2. **The seat's tool catalogue grew 237 -> 238 overnight and nothing noticed.**
   The count was printed on every preflight and compared against nothing. On a
   platform where the tool catalogue *is* the access-control boundary, that
   boundary moved unobserved. `preflight` now holds a dated
   `RECORDED_TOOL_COUNT` baseline and raises a `CHANGED:` line on any
   deviation (scoped to the entity-scoped surface, so the 13-tool stub does
   not cry wolf), and every run artifact now persists the sorted `tool_names`
   so the *next* change can be attributed rather than merely counted. Which
   tool appeared on 2026-09-22 is unrecoverable - no earlier run kept the list.

## Still open

1. **Write mode has run exactly once, on one deal, with consent.** Deal
   `7de1dd77` got Activity `1ca85e7b` on 2026-09-21. Verified by reading the
   platform back, not the artifact: the book went 175 -> 176 activities,
   exactly one is linked to that deal, and exactly one row in the whole book
   carries the `created_by=pipeline_agent` provenance marker - all three still
   true on 2026-09-22. The remaining 18 recommended deals are untouched. The
   rot-feedback-loop blocker on scaling this up is now fixed, but that removes
   one objection, not the need for a human decision: nothing has validated the
   action templates (item 2), and `Activity._permissions.delete` is `false`
   for this seat, so agent-created rows cannot be removed by the agent -
   cleanup needs someone with delete rights.
2. **`NEXT_ACTION_DUE_DAYS = 3`, `NEXT_ACTION_TYPE = "task"`, and
   `STAGE_ACTION_TEMPLATES` are unvalidated templates.** Nobody has confirmed
   them against how Suryodaya actually works. They are named constants so the
   guesses are visible rather than buried in a payload.
3. **The measured rot boundary is a range, not a number.** Live calibration
   gives `[5, 9]` days and the agent takes the lower bound, which errs toward
   showing a human a deal that turned out to be fine rather than hiding a
   rotting one. No deal currently sits in the gap, so nothing in this book can
   narrow it; a few more days of data, or one answer from whoever owns
   `deal-rot-config`, would. Every run states which bound it used.
4. **The party-level fallback is deliberately conservative.** If a party has
   any open activity, the deal is reported as already actioned - which may
   suppress a genuinely needed next action when one customer has several
   deals. The basis (`deal` vs `party`) is in the evidence so a human can
   overrule it; whether that tradeoff is right is a product decision.
5. **Run `--task loop` against a real model.** The loop's control flow is
   checked against a scripted stand-in (clean refusal, retry tripping
   `MAX_REPEAT_DENIALS`, allowed read, unparseable reply), but neither the
   `anthropic` nor the `ollama` backend has made a single real API call.
6. **The scored task matrix and its verifiers remain unwritten, on purpose.**
   Per the rubric an AI-authored test scores zero - see `tasks/README.md` and
   `tests/README.md`.

## What IS checked, as of 2026-09-22

- `--task preflight --mode live`: 238 tools, surface `entity_scoped`,
  `G1=sales_reachable`, canonical task READY, no probe drift, catalogue size
  matches the recorded baseline.
- `--task canonical --mode live --exec-mode propose`: 81 rotting deals from
  133, in 3 tool calls and ~9s, `created_record_ids: []`. Contact split
  74 never-contacted / 6 past threshold / 1 recent; action split 63 already
  open / 18 recommended. The one row that moved from `recommended` to
  `existing` since 2026-09-21 is deal `7de1dd77`, whose only open action is
  the one this agent wrote - and the run says exactly that on that row.
- The rot recomputation reproduces the platform's `_rot_days` exactly on all
  84 open deals, and recovers the one deal the agent's own write had removed
  from its report.
- Credentials never reach disk: `config.json` in every live run artifact
  shows `"mcp_token": "***redacted***"`. No `.env` exists in this repo; live
  runs take `AGENTSWITCH_MCP_TOKEN` from the session environment.
- The dry-run exercises all five decision paths offline (existing-by-party,
  recommended, needs-human-review, the agent-laundered deal, and correct
  exclusion of fresh and closed deals), and the stub refuses out-of-catalogue
  entities with the same message the live platform produces.
- A preflight against an unreachable host returns `gate_g1: inconclusive`,
  never `sales_blocked` - a network fault is not a policy refusal.
