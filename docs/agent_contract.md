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
  rotting deal, and a one-line `summary`. Sorted by rot severity. Also
  `uncalled`: one `UncalledParty` per customer with an open deal and no
  completed call in `deal_rot_days` (goal `pipeline.uncalled_30_days`) -
  `party_name`, `last_called` (None = never), `days_since`, `open_deal_ids`,
  `open_deal_value`. Keyed on the party, not the deal: a call logged against
  one deal of a customer still counts for its other deals. Only `type=call`
  counts, only done, only dated on or before today. Never-called first.
  Also `hidden_silence`: the customers in `uncalled` that still have open deals
  **missing from the rotting list**, which is where the two goals disagree. Each
  `HiddenSilence` lists its `masked_deals`. For each one, `fresh_because`
  (`non_call_activity` or `recently_opened`), `fresh_basis_date` and
  `fresh_basis_detail` say what made the deal look fresh, and `last_contact` /
  `days_since_contact` give its real age by contact alone. Largest hidden value
  first. When this list is non-empty, the `summary` states it too.
  History: on 2026-09-28 it first found 5 Keystone customers (13 deals, $963,392)
  masked by a bulk record edit. That finding is why `updated_at` stopped counting
  toward rot; with that rule, Keystone's hidden silence is empty.
  Also `excluded_deals`: open deals left out of **both** answers, each with a
  reason. Today these are supplier-side deals, whose party has the `supplier`
  role and neither `customer` nor `prospect` (seat-owner decision, 2026-09-28). If
  `Party` is unreadable, nothing is excluded and the summary says so.
  "Called" means `type == "call"` only; meetings and emails are contact but not
  calls (seat-owner decision, 2026-09-28).
  A never-called customer is in `uncalled` only if the relationship is at
  least `deal_rot_days` old. The relationship age is measured from the
  earliest of its first completed contact and its open deals' `created_at`.
  Younger never-called customers go to `never_called_new` (`days_since` = the
  relationship age), and the summary counts them. On Suryodaya, whose book is
  16 days old, that is 35 customers.
  One contact definition throughout: `contact_status` / `contact_evidence`
  use exactly the contact date rot uses (done, dated on or before today, not
  agent-authored, by deal or by party on rows with no `deal_id`). Before
  2026-09-28, 18 of 23 Keystone rows reported two different dates.
  Robustness: a done activity whose `due_date` is not a date is skipped and
  counted in the summary; any failure to read `Party` (not only a permission
  refusal) disables the supplier exclusion and says why; open deals with no
  party are named in the summary, because they cannot appear in `uncalled`.
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
| `rot_evidence` | the platform's own `_rot_level`/`_rot_days`, **and** `independent_rot_days` - days since the last genuine contact (`last_contact`) or deal update, judged against `rot_boundary_days` (= `deal_rot_days`) - plus, on deals this agent wrote to, `platform_mirror_days` against `platform_boundary_days`, and which signals flagged the deal. When the platform calls a deal fresh only because the agent wrote to it, the row also carries `agent_write_suppressed_platform_signal: true` and a plain-language `note` saying so |
| `contact_status` | `never_contacted` / `not_contacted_since_threshold` / `recently_contacted` / `insufficient_evidence` |
| `contact_evidence` | the date(s)/channel that classification was based on |
| `action_status` | `existing` / `overdue` / `created` / `recommended` / `needs_human_review` / `unavailable`. `overdue` = an open Activity exists but its `due_date` has passed: nobody is actioning it, so it is neither a duplicate to create nor evidence the deal is handled |
| `next_action` | the action itself (existing, created, or merely recommended) |
| `reasons` | plain-language justification for `action_status`, including every idempotency check performed |

## Advisory lists (added 2026-09-29, `agent/pipeline_checks.py`)

These don't change which deals are rotting or who is uncalled. Every source
they need is optional. Pipeline, Party, PartyRelationship, Lead, Quotation,
SalesOrder, `endpoint.crm.account_plans` and `endpoint.people_directory` are
read if the seat can read them; otherwise the check is skipped and
`context_notes` says why.

| Field | Contents |
|---|---|
| `data_issues` | One row per (deal, `code`). Codes: `contact_of_other_customer`, `contact_is_organisation`, `contact_not_found`, `stage_not_in_pipeline`, `unknown_pipeline`, `duplicate_from_lead`, `disqualified_lead`, `slipped_close_date`, `unvalued`, `no_contact`, `no_owner`, `unknown_owner`, `expired_quote`, `overdue_plan_milestone`, `close_lost_candidate` (proposed only), `won_but_prospect` (reported, never changed) |
| `late_orders` | Confirmed SalesOrders past `delivery_date` and not delivered, for customers with open deals. Computed from SalesOrder, never from `make.orders.late` (K3) |
| `leads_needing_action` | Qualified leads with no deal, leads whose `next_action` date has passed, and leads still `new` after `deal_rot_days`. Supplier-only parties and tagged test leads are excluded |
| `worklist_by_owner` | Rotting deals per owner, ranked by value × days rotting, top 5 each |
| `escalations_needed` | Who has to decide what. Written out instead of calling `escalations.raise`, because Keystone has no assignable person (G9) |
| `weighted_pipeline` | Weighted open value from stage-default probabilities, next to the value implied by the `probability` field (Bug 11) |
| `context_notes` | Timezone used and why, sources skipped, mismatched activity links |

Behaviour changes to the core answer:

- **Test data.** Open deals tagged `[teamNN-test…]`/`[teamNN-probe…]` (in the
  title or the party name) go to `excluded_deals` with reason `test fixture`.
  Closed tagged deals leave scope silently. Set `PIPELINE_INCLUDE_TEST_DATA=1`
  to keep them.
- **Mis-linked activities.** An activity whose `deal_id` belongs to a different
  party than its own `party_id` (PB3/P2) counts as contact for neither. It is
  counted in the summary instead.
- **Reported contacts.** A done activity carrying
  `logged_by=pipeline_agent actual_date=YYYY-MM-DD` is dated by `actual_date`.
  `--task log-contact` writes such rows, because the platform refuses a past
  `due_date` (PB1).
- **Clock.** "Today" is the company's day: `Company.timezone`, then
  `PIPELINE_TIMEZONE`, then UTC. Keystone's Company record has no timezone
  field, so set `PIPELINE_TIMEZONE=America/New_York`.
- **Next action.** A stage outside the deal's pipeline → `needs_human_review`.
  The type follows the stage (qualification/negotiation → call, proposal →
  email). The due date is 3 US business days out. An expired quote or an
  overdue account-plan milestone becomes the action. The description names the
  contact only if they represent the deal's customer. `Deal from <uuid>` titles
  show the party name. Several overdue open tasks on one deal are reported
  together.
- **Before a write**, the deal itself is re-read. If it has closed or changed
  stage since the list read, nothing is written (`recommended`, with the reason).

## Requests refused before any tool call (`agent/request_guard.py`)

For any request other than the canonical one, `runner` checks the text first.
A match returns a `RefusalResult` with zero tool calls. Rules:
- `approve_quote`: G2
- `set_line_amount`: G1
- `revenue_attainment`: K4
- `invoice_payment`: Invoice is outside the catalogue
- `delete_record`: no delete tool; the book is shared
- `other_users_task`: not this seat's record
- `bulk_close`: terminal and irreversible

## Rot: three signals, never one

A deal is a candidate if any of these flags it:

1. the platform's `_rot_level` (anything but `fresh`/`none` - the flagged word
   is `attention` on Suryodaya and `warning` on Keystone);
2. days since the last **genuine contact** - the latest done, non-agent
   Activity's `due_date` on or before today, by deal or by party on rows with
   no `deal_id` - reaching `deal_rot_days`. A deal with no contact at all ages
   from `deal.created_at`. `deal.updated_at` never counts: a record edit is
   not customer contact (seat-owner decision, 2026-09-28). `created_at` is
   not a floor under a contact date either, because on Keystone it is a
   seeding timestamp. Evidence field `age_basis` says `contact` or
   `deal_opened`;
3. on a deal this agent has written to, the platform's formula with the
   agent's rows excluded, reaching the calibrated platform boundary (the
   laundering defence below).

Signal 2 exists because the platform ages from `Activity.created_at`, the
**insert** time. On Keystone (2026-09-28) all 177 activities share one
`created_at`, so every deal with any activity read 11 days old and the agent,
mirroring that formula, found 1 rotting deal where 8 were.

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
It judges signal 3 only: measured on the platform's insert-time age, it says
nothing about contact age, and on Keystone a 12-day line from it would flag
every open deal.

## State-changing path: the idempotency rule

Before any `Activity.create`:

1. List open `Activity` records for the deal. If one already exists, stop -
   `action_status=existing`, link it, do not create anything. A party-level
   match counts only open activities that name **no** deal. An open task on a
   sibling deal belongs to that deal, and must not stand in for this deal's
   next action. If that existing
   activity is one **this agent wrote**, it is still not duplicated, but
   `reasons` says plainly that it is the agent's own unactioned note and not
   evidence that anyone contacted the customer. If the existing activity is
   **past its `due_date`**, `action_status=overdue` instead: still no
   duplicate, but it is reported as unactioned, not as handled.
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

## Which entities a refusal actually depends on

Decided 2026-09-24, after a refusal on `CRMPreferences` was found escaping
`run_canonical_task` and killing the run with a traceback - returning neither
of the two results this contract allows.

**Only `Deal` and `Activity` are required.** A refusal on either is a
`RefusalResult`, as above. `CRMPreferences` is **not** required: if it refuses,
the run falls back to `DEFAULT_ROT_DAYS` and says so in `unavailable_note`.

The reasoning, so this is not re-litigated:

- Refusing would discard a complete, correct rotting-deals answer over a
  preferences lookup. That is a worse failure than a weaker contact column.
- `deal_rot_days` no longer selects rotting deals at all - `calibrate_rot_boundary`
  measures that boundary from live data. The threshold now feeds only contact
  classification, so losing it degrades one column rather than invalidating the
  answer.
- The fallback already existed for the "no record returned" case; the refusal
  path simply never routed into it.

## A completed activity dated in the future

Decided 2026-09-24. `Activity` has no `completed_at`, so contact recency is
derived from `done` plus `due_date` - a scheduling field, not a record of when
anyone spoke to anyone. One live row is `done=1` with a `due_date` ahead of
today (filed against the platform as B8, report `529f67e0`).

Such a row classifies as **`insufficient_evidence`**, not `recently_contacted`.
What is actually known is "this was completed, when is unknown", which is what
that status means - and it is the same treatment an unparseable date already
receives. The previous behaviour computed `days_since: -3` and drew a positive
conclusion from it, dropping the deal out of the report on an impossible number.

This also lands on the safe side of the rule used throughout: over-reporting a
deal that turns out to be fine is recoverable, hiding a rotting one is not.

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
