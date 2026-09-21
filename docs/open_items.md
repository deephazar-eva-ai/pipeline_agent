# Open items

Status is honest about what has actually been checked against the live seat,
not what has merely been coded for. Everything dated 2026-09-21 below was
measured against the real `team07` credential on
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

The credential serves **237 entity-scoped tools** (`Deal.list`,
`Activity.create`, `Deal.qualify`, ...), not the 13 generic tools in the
captured `agent_tools.json`. `MCP_TOOL_SURFACE` now defaults to
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

1. **Two sources of truth for the rot threshold disagree.**
   `GET /api/deal-rot-config` returns `default_rot_days: 14`;
   `CRMPreferences.deal_rot_days` returns `30`. Nothing reconciles them, and
   the computed `_rot_level` appears to follow neither directly (81 deals sit
   at `attention` with `_rot_days: 8`).
2. **`Activity.deal_id` is never populated.** 0 of 175 activities carry one;
   all 175 carry `party_id`. Any deal-scoped contact reporting is therefore
   silently empty rather than wrong-looking - this agent reported *every* deal
   as never-contacted until the party fallback was added. 35 parties are
   shared between deals and activities.

`_rot_level`'s real vocabulary is `fresh` / `attention` / `none` (not
`aging` / `stale`, which this repo filtered on and which never occur), and
`none` is exactly the 49 closed deals.

## Writing a next action resets the signal that detected the rot

Measured on the 2026-09-21 smoke test: creating one `Activity` against deal
`7de1dd77` moved it from `_rot_level: attention, _rot_days: 8` to
`fresh, 0`, and the candidate count dropped 81 -> 80. So `_rot_days` tracks
days since the last linked activity, not days in stage - which also explains
why 81 deals sat at exactly 8 days (none had any linked activity, and the
book was seeded 8 days ago).

**This is a feedback loop, and it is the most dangerous thing found so far.**
The agent's own bookkeeping makes a deal look healthy. Logging a task is not
the same as contacting a customer, so a scheduled run of this agent would
quietly launder every rotting deal into `fresh` without a human ever speaking
to anyone. Two consequences:

- Re-running the agent is idempotent for a reason that has nothing to do with
  the idempotency guard: the deal simply stops being a candidate. The guard
  was therefore *not* exercised on live data by the re-run - the deal fell out
  of the candidate set first, which the run reported honestly as
  "requested deal(s) not among the rotting candidates".
- Before write mode is used at scale, rot needs a definition that does not
  reset on the agent's own writes - e.g. ignore activities whose description
  carries the `created_by=pipeline_agent` provenance marker, or key rot off
  `done=true` activities only (a completed touchpoint), not open ones.

## Still open

1. **Write mode has run exactly once, on one deal, with consent.** Deal
   `7de1dd77` got Activity `1ca85e7b` on 2026-09-21. Verified by reading the
   platform back, not the artifact: the book went 175 -> 176 activities,
   exactly one is linked to that deal, and exactly one row in the whole book
   carries the `created_by=pipeline_agent` provenance marker. The remaining
   18 recommended deals are untouched, pending the rot-feedback-loop fix
   above. Note `Activity._permissions.delete` is `false` for this seat, so
   agent-created rows cannot be removed by the agent - cleanup needs someone
   with delete rights.
2. **`NEXT_ACTION_DUE_DAYS = 3`, `NEXT_ACTION_TYPE = "task"`, and
   `STAGE_ACTION_TEMPLATES` are unvalidated templates.** Nobody has confirmed
   them against how Suryodaya actually works. They are named constants so the
   guesses are visible rather than buried in a payload.
3. **The party-level fallback is deliberately conservative.** If a party has
   any open activity, the deal is reported as already actioned - which may
   suppress a genuinely needed next action when one customer has several
   deals. The basis (`deal` vs `party`) is in the evidence so a human can
   overrule it; whether that tradeoff is right is a product decision.
4. **Run `--task loop` against a real model.** The loop's control flow is
   checked against a scripted stand-in (clean refusal, retry tripping
   `MAX_REPEAT_DENIALS`, allowed read, unparseable reply), but neither the
   `anthropic` nor the `ollama` backend has made a single real API call.
5. **The scored task matrix and its verifiers remain unwritten, on purpose.**
   Per the rubric an AI-authored test scores zero - see `tasks/README.md` and
   `tests/README.md`.

## What IS checked, as of 2026-09-21

- `--task preflight --mode live`: 237 tools, surface `entity_scoped`,
  `G1=sales_reachable`, canonical task READY, no probe drift.
- `--task canonical --mode live --exec-mode propose`: 81 rotting deals from
  133, in 3 tool calls and ~7s, `created_record_ids: []`. Contact split
  74 never-contacted / 6 past threshold / 1 recent; action split 62 already
  open / 19 recommended.
- Credentials never reach disk: `config.json` in every live run artifact
  shows `"mcp_token": "***redacted***"`.
- The dry-run exercises all four decision paths offline (existing-by-party,
  recommended, needs-human-review, and correct exclusion of fresh and closed
  deals), and the stub refuses out-of-catalogue entities with the same
  message the live platform produces.
- A preflight against an unreachable host returns `gate_g1: inconclusive`,
  never `sales_blocked` - a network fault is not a policy refusal.
