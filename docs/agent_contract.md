# Agent contract (Phase 1 exit artifact)

Prose companion to `src/pipeline_agent/agent/contract.py` - keep both in sync.

## Input

- `text` - the canonical request, verbatim or a close paraphrase:
  > "Which deals are rotting, who has not been contacted, and what is the
  > next action on each?"
- Optional narrowing: `owner`, `team`, `since` (all `None` by default - no
  narrowing applied).
- `mode`: `propose` (default - read-only, never writes) or
  `create-next-actions` (may create exactly one `Activity` per eligible deal
  that doesn't already have an open one).

## Output

Exactly one of:

- **`CanonicalAnswer`** - `deal_rot_days` (the threshold actually used, and
  whether it came from a live read), `analyzed_at`, one `DealResult` per
  rotting deal, and a one-line `summary`. Sorted by rot severity.
- **`RefusalResult`** - returned instead of a partial/best-effort answer the
  moment a required entity (`Deal` or `Activity`) is inaccessible. Names the
  missing entities and every tool call attempted before giving up.
  `missing_domain` is populated **only when the platform itself named a
  domain**; AgentSwitch scopes access by tool catalogue and normally names
  none, in which case the field is empty and the message says a tool was not
  in the seat's `tools/list`. A refusal never invents a reason it was not
  given. No `CanonicalAnswer` is ever returned alongside a refusal - it's
  one or the other, never a hybrid.

Each `DealResult` carries, separately, not folded into one verdict:

| Field | Meaning |
|---|---|
| `rot_evidence` | the platform's own `_rot_level`/`_rot_days`, **and** `independent_rot_days` - the same number recomputed with this agent's own Activity rows excluded - plus the `rot_boundary_days` in force and which of the two signals flagged the deal. When the platform calls a deal fresh only because the agent wrote to it, the row also carries `agent_write_suppressed_platform_signal: true` and a plain-language `note` saying so |
| `contact_status` | `never_contacted` / `not_contacted_since_threshold` / `recently_contacted` / `insufficient_evidence` |
| `contact_evidence` | the date(s)/channel that classification was based on |
| `action_status` | `existing` / `created` / `recommended` / `needs_human_review` / `unavailable` |
| `next_action` | the action itself (existing, created, or merely recommended) |
| `reasons` | plain-language justification for `action_status`, including every idempotency check performed |

## Rot: two signals, never one

`rot_evidence` reports the platform's `_rot_level`/`_rot_days` *and* an
independent recomputation, and a deal is a candidate if either flags it.

This is not redundancy. The platform computes `_rot_days` as days since the
most recent of `deal.updated_at` and any linked `Activity.created_at` - so an
Activity **this agent creates** resets it. Trusting the platform's signal
alone means the agent's own bookkeeping deletes deals from the agent's own
report while the customer stays uncontacted. The recomputation excludes rows
carrying `PROVENANCE_MARKER` and therefore does not move when the agent
writes.

The boundary between `fresh` and flagged is not exposed by any API, and the
two documented thresholds (`deal-rot-config: 14`, `CRMPreferences: 30`) are
both wrong for it. It is measured per run by bracketing the oldest `fresh`
deal against the youngest flagged one, over deals the agent has never written
to; the conservative lower bound is used, and the summary states the bracket.

## State-changing path: the idempotency rule

Before any `Activity.create`:

1. List open `Activity` records for the deal. If one already exists, stop -
   `action_status=existing`, link it, do not create anything. If that existing
   activity is one **this agent wrote**, it is still not duplicated, but
   `reasons` says plainly that it is the agent's own unactioned note and not
   evidence that anyone contacted the customer.
2. If `mode=propose`, stop - `action_status=recommended`, nothing written.
3. Otherwise, **re-read** open activities for the deal immediately before the
   write (a fresh call, not the list from step 1 - the shared book means
   another team or an earlier deal in this same run could have changed
   things). If one now exists, stop - `action_status=existing`.
4. Create the `Activity`, with `PROVENANCE_MARKER` and the `run_id` embedded
   in its `description` field as an audit marker. `description`, not `notes`:
   `notes` is not an Activity field and `Activity.create` rejects unknown
   keys. That marker is what later runs use to recognise their own writes, so
   it is load-bearing for the rot recomputation above, not just for audit.

This sequence is unconditional Python (`agent/workflow.determine_next_action`),
not something asked of the model - see `docs/architecture.md` for why.

## Error policy

A `PermissionDeniedError` on `Deal` or `Activity` is not routed around with
Pipeline/Goal data as a substitute (that substitution was already identified,
in the earlier gap analysis, as producing nothing that answers the real
question). It becomes a `RefusalResult` instead, naming the exact missing
domain. The harness never retries after such a refusal (`harness/loop.py`
enforces this with a hard stop after `MAX_REPEAT_DENIALS`; the deterministic
`workflow.py` path never had a retry loop to begin with).

## Safety policy

No message, call, email, or other external send exists anywhere in this
codebase. The only write this agent can make is `Activity.create`, and only
under `mode=create-next-actions`, and only after the idempotency check above.

## Audit policy

Every tool call - success, refusal, or error - is appended to `TaskRun.steps`
via `harness/recording.RecordingMCPClient` and persisted by
`harness/artifacts.run_artifact` before the process exits. See
`docs/architecture.md`'s exit-condition section for what that looks like on
disk today.
