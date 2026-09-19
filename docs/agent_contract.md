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
  missing domain, the missing entities, and every tool call attempted before
  giving up. No `CanonicalAnswer` is ever returned alongside a refusal - it's
  one or the other, never a hybrid.

Each `DealResult` carries, separately, not folded into one verdict:

| Field | Meaning |
|---|---|
| `rot_evidence` | the platform's own `_rot_level`/`_rot_days` |
| `contact_status` | `never_contacted` / `not_contacted_since_threshold` / `recently_contacted` / `insufficient_evidence` |
| `contact_evidence` | the date(s)/channel that classification was based on |
| `action_status` | `existing` / `created` / `recommended` / `needs_human_review` / `unavailable` |
| `next_action` | the action itself (existing, created, or merely recommended) |
| `reasons` | plain-language justification for `action_status`, including every idempotency check performed |

## State-changing path: the idempotency rule

Before any `Activity.create`:

1. List open `Activity` records for the deal. If one already exists, stop -
   `action_status=existing`, link it, do not create anything.
2. If `mode=propose`, stop - `action_status=recommended`, nothing written.
3. Otherwise, **re-read** open activities for the deal immediately before the
   write (a fresh call, not the list from step 1 - the shared book means
   another team or an earlier deal in this same run could have changed
   things). If one now exists, stop - `action_status=existing`.
4. Create the `Activity`, with `run_id` embedded in its `notes` field as an
   audit marker.

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
