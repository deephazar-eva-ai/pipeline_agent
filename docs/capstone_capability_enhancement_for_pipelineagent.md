# Pipeline Agent: capability enhancement plan

**Seat:** 07, Pipeline (CRM). Credential `team07`, tenant Suryodaya Precision Works.
**Seat goals:** `pipeline.rotting_deals` ("Which deals are rotting?") and `pipeline.uncalled_30_days` ("Who has not been called in 30 days?").
**Canonical question:** *"Which deals are rotting, who has not been contacted, and what is the next action on each?"*
**Measured:** 2026-09-25, live, read-only, against `https://agentswitch.theschoolofai.in`.
**Builds on:** [`crm_gapreport.md`](crm_gapreport.md) (2026-09-17), [`agent_contract.md`](agent_contract.md), [`architecture.md`](architecture.md), [`open_items.md`](open_items.md).

---

## 0. The argument in one paragraph

The best AI-native pipeline tools compete on **reach**: more data sources, call recording, enrichment, sequencing, forecasting. This seat cannot win on reach. It can read CRM and Quotation/SalesOrder data, and it gets a 403 on email, calendar, channels, invoices, tickets and contracts (§1). What it can win on is **trust**: every verdict carries its evidence, the agent's own writes never hide a problem, it refuses when the data cannot support an answer, and a harness checks each claim against the database instead of the agent's prose. The live book shows why this matters. On 2026-09-25 the platform's own rot signal says **no deal is rotting** (88 of 88 open deals are `fresh`). Meanwhile 9 open deals are past their expected close date, 69 have no close date at all, and 123 of 138 have no owner. An agent that repeats the platform's signal answers "nothing to do". This plan is for an agent that answers correctly anyway, and shows its evidence.

---

## 1. The seat boundary as measured today, not as the gap report assumed

The gap report was written against a static snapshot with 13 generic tools. Several of its load-bearing assumptions are now false. Everything below was re-measured on 2026-09-25.

### 1.1 What the seat can do

**Catalogue: 242 MCP tools, entity-scoped** (`Deal.list`, not `list(entity=Deal)`). The catalogue grew 237 → 238 → 242 between 2026-09-21 and 2026-09-24 without notice (filed as bugs 4 and B2).

| Entity | Operations available to this seat | Relevance |
|---|---|---|
| `Deal` | list, get, create, update, full stage state machine (`qualify`, `send_proposal`, `negotiate`, `mark_won.*`, `mark_lost.*`), `make.Quotation` | core |
| `Activity` | list, get, create, update. **No delete.** | core: contact history and next actions |
| `Lead` | list, get, create, update, `contact`, `qualify`, `convert`, `disqualify.*`, `make.Deal` | goal 2: "who has not been called" |
| `Party`, `PartyRelationship` | list, get, create, update | contact linkage |
| `Quotation` | list, get, transitions (`send`, `accept.*`, `decline.*`, `convert_to_order.*`, `approval.submit`) | quote-aware next actions |
| `SalesOrder` | list, get, create, update, `confirm`, `approval.submit` | won-deal verification |
| `Note` | list, get, create, update (has `deal_id`) | rationale without touching Activity |
| `Pipeline`, `Goal`, `CRMPreferences` | read (`CRMPreferences` also writable) | configuration |
| `AgentTask` | create, update, `run_now`, `pause`, `resume`, `complete`, `fail` (cron / interval) | standing habit |
| `AgentMemory`, `AgentTodo`, `AgentEscalation` | create, list, update | memory, work queue, human hand-off |
| `endpoint.crm.*` | call-note sources/drafts/approve, account plans, campaign audiences | call-note drafting |
| `tools.search`, `tools.describe` | catalogue introspection | drift detection |

**REST fallback, same login:**
- `GET /api/{entity}/aggregate`: `group_by` with **count and sum only, no max**.
- `GET /api/forecast?pipeline=&period=`: a weighted-value forecast. **REST only; not in the MCP catalogue.**
- `GET /api/deal-rot-config`: now returns `default_rot_days: 30`. It returned **14** on 2026-09-21, so it changed silently and now agrees with `CRMPreferences.deal_rot_days = 30`.

### 1.2 What the seat cannot do

| Wanted for | Entity or route | Result |
|---|---|---|
| email recency | `EmailMessage` | 403, app `email` not enabled |
| WhatsApp/SMS nudges, consent ledger | `ChannelConversation`, `ChannelConsent`, `ChannelSendDecision` | 403, `channels` |
| meeting recency | `CalendarEvent` | 403, `scheduling` |
| account financial health | `Invoice`, `Payment` | 403, `accounting` |
| support context | `Ticket` | 403, `support` |
| renewal context | `Contract` | 403, `contracts` |
| governed autonomy | `ApprovalRequest` | 403, `approvals`; `AgentRunbook` is readable but holds 0 rows |
| rep context | `Employee` | 403, `payroll` |
| lead capture | `WebForm` | 403, role cannot read |
| stage-entry dates | no stage-history entity, no `stage_entered_at` field | absent |
| server-side rot filter | `_rot_level` / `_rot_days` not in `Deal.list` filters | absent |
| "latest activity per party" | aggregate has no `max` | absent |
| deleting its own mistakes | `Activity.delete` | not in catalogue |

### 1.3 Gap-report claims that no longer hold

| `crm_gapreport.md` said | Measured 2026-09-25 | Consequence |
|---|---|---|
| 13 generic tools, `report(aggregate=max, group_by=deal_id)` | entity-scoped surface; no `report`; REST aggregate has count/sum only | "last contact per deal" is a client-side scan, not one call |
| `_rot_level` ∈ `fresh`/`aging`/`stale`/`none` | `fresh`/`attention`/`none`; today only `fresh` and `none` | filtering on `stale` returns nothing |
| Threshold = `CRMPreferences.deal_rot_days` | three sources disagreed (14 / 30 / measured 5–9); now 30 / 30 and nothing is flagged | the platform signal can't be the only signal |
| §3: cross-domain reasoning over Invoice, Ticket, Contract | all 403 for this seat | that edge belongs to the EA seat, reachable only by escalation |
| §3: consent-ledgered omnichannel outreach | `channels` app 403 | not available to this seat |
| §3: `AgentRunbook` → `ApprovalRequest` governance | runbooks readable and empty; approvals 403 | governance must be built in the harness |
| `Activity.deal_id` for deal-scoped contact | 29 of 176 carry `deal_id`; all 176 carry `party_id` | party linkage stays load-bearing |

**What still holds:** §1's competitor gaps (no enrichment, sequences, call recording, sentiment), §2a's `AgentTask`/`AgentMemory`/`AgentEscalation` pattern, and the five-factor rot score in §5.

---

## 2. What best-in-class looks like, and where this seat stands

Competitors from the gap report (Clarify, Monaco, Reevo), plus the capability classes the wider revenue-intelligence category advertises. These are the vendors' marketing claims, not verified through trials.

| Capability class | Category leaders advertise | This seat today | Reachable? |
|---|---|---|---|
| Deal-risk / rot detection | ML risk scores, "deal warnings" | two-signal rot, calibrated boundary, self-write exclusion | **yes, and can exceed** (§3.1) |
| "Who is going cold" | engagement scoring over email + calls + meetings | Activity scan via party linkage | **partly**: Activity only; email/calendar are 403 |
| Next best action | AI-suggested steps, auto-drafted follow-ups | stage templates, idempotent `Activity.create` | **yes, can exceed** (§3.3) |
| Pipeline Q&A with citations ("Ask Reevo") | chat over the CRM | loop harness, deterministic core | **yes** (§3.7) |
| Forecasting | weighted rollups, AI forecast, accuracy tracking | none | **partly**: `/api/forecast` REST and own computation (§3.5) |
| Data hygiene / auto-updates | agents keep fields current | none | **yes, can exceed** (§3.4) |
| Standing monitoring | scheduled digests, alerts | on-demand runs | **yes** via `AgentTask` (§3.6) |
| Call recording, transcripts, sentiment | first-class | no entity exists | **no**: platform work |
| Enrichment, intent, TAM | built-in data | no entity exists | **no**: platform work |
| Sequences, dialer, inbox warm-up | first-class | no entity; `email`/`channels` 403 | **no** |

**Where we choose to beat them.** Nobody in the category publishes how trustworthy their deal-risk verdicts are. A black-box score cannot say "I don't know", cannot show which record it read, and in practice its own automation moves it. We saw this platform's rot signal reset when an agent logged a task (bug 3, since changed). The differentiator is **verifiable correctness under a shared, moving book**, measured by our own harness.

---

## 3. Enhancements this seat can build now (no platform change)

Each item names the tools it uses, the live evidence behind it, and how the harness can check it against the database.

### 3.1 Rot that survives the platform's signal: five factors, degraded honestly

**Problem.** The platform flags 0 of 88 open deals. The five-factor score in gap report §5 is the right model, but one of its inputs does not exist.

**Build.** Compute `rot_score = 0.35·S_stage + 0.25·S_activity + 0.20·S_slippage + 0.15·S_regression + 0.05·S_value` in `agent/workflow.py` from `Deal.list` plus the existing Activity index. Handle each input honestly:

| Input | Source | Status |
|---|---|---|
| `S_activity` | latest `done` Activity, by deal, then party; excluding rows carrying the agent's provenance marker | available |
| `S_slippage` | `today − expected_close_date` | available for 19 of 88 open deals; **69 have no close date** |
| `S_value` | `Deal.value` against p90 of open deals | available; 39 open deals have value ≤ 0 |
| `S_stage` | no stage-entry date exists anywhere | **unavailable**; use `updated_at` as a proxy, labelled as a proxy |
| `S_regression` | needs stage history | **unavailable until §3.8 builds one** |

**Beats best-in-class by:**
- Every deal row lists which factors were computed, which were proxied and which were missing.
- A score built from fewer than three real factors is reported as `insufficient_evidence`, never as a number.
- The `rot_comment` names the dominant factor **by weighted contribution** (gap report §5.2).
- The platform verdict stays alongside, so a disagreement is visible, never averaged away.

**Harness check.** Recompute each factor from a raw `Deal.get` and `Activity.list` read at verification time. Assert that the stated band follows from those numbers, and that every deal the platform flags is also in our candidate set.

### 3.2 "Who has not been called": both goals, not one

**Problem.** Goal 2 is `pipeline.uncalled_30_days`, and it applies to leads as well as deals. The current agent only covers deals.

**Build.**
- Extend contact classification to `Lead` (143 rows: 59 `new`, 8 `contacted`) through `Lead.party_id` and the same Activity party index.
- Report "called" separately from "touched": `type = call` is 20 of 176 activities, against 100 meetings and 14 emails.
- Make "30 days" configurable and state it on every run.
- Say plainly that email and calendar history is **invisible to this seat** (403). The honest verdict is "no call recorded in CRM in 30 days", never "not contacted".

**Beats best-in-class by** naming the limits of its own evidence in the answer. Engagement-scoring tools present one number and never say which channels they could not see.

**Harness check.** For each party reported uncalled, verify that no `done=1, type=call` Activity dated within 30 days exists for that party.

### 3.3 Next actions that are specific, safe and not self-deceiving

**Build on the contract that exists** (idempotency, re-read before write, provenance marker). Add:
- **Quote-aware actions.** `Quotation` is readable: 103 rows, 40 past `valid_till` but still `draft`, `sent` or `viewed` (filed as bug 12). Examples:
  - "Deal in `proposal`, the party's quote Q-… lapsed 12 days ago" → re-quote through `Deal.make.Quotation`, proposed and never auto-executed.
  - "Quote `accepted`, no SalesOrder" → chase the order.
- **Human-readable subjects.** Resolve `party_id` to the party name. 29 deals are titled `Deal from <uuid>` (bug B4), and the agent's one live Activity inherited that title.
- **Rationale in a `Note`, not in the Activity.** `Note.create` takes `deal_id`. Put the "why" there with the run id, and keep the Activity to the action itself. Verify first that Notes do not move `_rot_days`; the platform's rot formula has already changed once (see `open_items.md`).
- **Owner routing.** 123 of 138 deals have no `owner`. Emit a `needs_owner` action rather than assigning one, because this seat has no `Employee` access to choose from.

**Beats best-in-class by:** writes that cannot duplicate (re-read guard), cannot launder the rot signal (independent recomputation), and are traceable to one run.

**Harness check (write tasks, opt-in).** After a `create-next-actions` run:
- read `Activity.list(deal_id=…)` back and assert exactly one open agent-authored row per eligible deal,
- assert every one carries the run id,
- assert the deal is still reported by the next run's independent signal.

### 3.4 Pipeline hygiene auditor: the agent that fixes what makes the answer wrong

**Problem.** Most of the "rot" in this book is missing data, not stalled deals. Measured today:

| Defect | Count |
|---|---|
| open deals with no `expected_close_date` | 69 of 88 |
| open deals past `expected_close_date` | 9 |
| deals with no `owner` | 123 of 138 |
| `closed_won` with probability < 100 | 19 of 21 |
| `closed_lost` with probability > 0 | 9 |
| `closed_lost` with no `lost_reason` | 28 of 31 |
| deals whose `pipeline` is `'default'` (not a real pipeline) or null | 8 + 26 |
| deals titled `Deal from <uuid>` | 29 |

**Build.** A `--task hygiene` mode that lists each defect with the record and the proposed fix. It is propose-only by default. Where a fix is mechanical and reversible (for example `probability = 100` on `closed_won`), it can apply the fix through `Deal.update`, but only in a separate opt-in mode, one deal at a time, with a re-read first.

**Beats best-in-class by** separating "this deal is rotting" from "this deal's data is too incomplete to judge". Black-box risk scores blend the two.

**Harness check.** Each reported defect is re-derived from a raw read. After an opt-in fix, the field reads back changed and nothing else on the record moved.

### 3.5 An honest forecast, reconciled against the platform's

**Problem.** `GET /api/forecast` returns weighted buckets, and on inspection:
- A deal with `probability: 0.0` is weighted at **50%** of value (451,902.60 → 225,951.30), so "unset" and "zero" are treated the same. Worth filing, with bug 11.
- A 2025-09 bucket still contains an open deal a year past its close date.
- Won deals sit at probability 0–25 (bug 11), so the rollup understates won revenue.

**Build.** Compute our own weighted pipeline with stage-consistent probabilities (won = 100, lost = 0, open = recorded value, or "unset" when zero). Place it side by side with `/api/forecast` and list every deal where the two disagree and why. Report past-dated buckets as slippage, not as forecast.

**Beats best-in-class by** making forecast disagreements explicit and attributable to named records. It does not claim predictive accuracy; forecast-accuracy tracking needs history (§3.8).

**Harness check.** Recompute bucket totals from `Deal.list`, and assert that each discrepancy we report actually reproduces.

### 3.6 From a query to a standing habit

**Build.**
- **`AgentTask`** (create, cron) for the Pipeline persona `86ce8737…`: daily at 09:00 IST, propose mode. Its output is a digest, not writes.
- **`AgentMemory`** per `party_id`, category `relationship`, with an expiry. Records "flagged on 2026-09-25, owner not set", so tomorrow's digest says "still unowned, day 2" instead of repeating itself.
- **`AgentTodo`** for items that need a human decision the agent must not make: re-quote, mark lost, assign owner.
- **`AgentEscalation`** (needs a `session_id`) for high-value deals past close by more than N days, so the SLA clock runs on the platform, not in a chat log.

**Caution.** 52% of this tenant's scheduled-job runs execute under personas with filler prompts (bug 9). Before relying on scheduled runs, verify with `AgentTask.get` / `AgentSession.get` that ours runs under persona `86ce8737…`.

**Beats best-in-class by** remembering what it said yesterday and escalating on a measured SLA, all as inspectable platform records.

### 3.7 Cited pipeline Q&A with a refusal floor

**Build.** Keep `harness/loop.py` for free-form questions ("Which Sahyadri deals are stuck?"), with three rules the harness enforces rather than the prompt:
- Every number in the answer must be backed by a tool call recorded in `TaskRun.steps`. Answers citing no step are marked unsupported.
- Questions needing out-of-seat data ("Are they late paying us?" needs `Invoice`, 403) **refuse** and name the seat that owns the data. The current `MAX_REPEAT_DENIALS` hard stop already exists.
- Questions the data cannot answer at all ("How long has this deal been in negotiation?" needs stage history, which does not exist) refuse instead of estimating.

This is also where the **mandatory refusal task** lives. Two natural candidates, for the team to write:
- "What is the payment history of the accounts behind rotting deals?" (403: `accounting`)
- "How many days has each deal spent in its current stage?" (no stage-entry data exists)

**Beats best-in-class by** saying no. Chat-over-CRM products are built to always answer.

### 3.8 History without a backend: snapshots in the run artifacts

**Problem.** The gap report's `DealSnapshot` table (§5.3) needs new backend. Without history there is no `S_regression`, no "is this getting worse", and no forecast accuracy.

**Build.** Every run already writes `result.json` to disk before scoring. Add `snapshot.json`: one row per open deal with `stage`, `value`, `probability`, `expected_close_date`, `updated_at`, `rot_score`, plus a `captured_at`, following `deals_snapshot_schema.json`. Then:
- **Stage regression:** a deal's stage now earlier in `Pipeline.stages` than in a previous snapshot.
- **Time in stage:** first snapshot where the current stage appeared. This is a lower bound, stated as such.
- **Close-date pushes:** `expected_close_date` moved later across snapshots.
- **"What changed since yesterday":** diff of the last two snapshots. This is the most useful line in a daily digest.

**Beats best-in-class by** turning a backend gap into orchestration. History starts the day the first snapshot runs, and the doc says so.

**Harness check.** The snapshot is written before `status.json` flips to `completed`. Each row matches a raw `Deal.get` taken in the same run.

### 3.9 Guarding the ground it stands on

The platform moved underneath this agent five times in eight days:
- the catalogue grew 237 → 238 → 242,
- the rot formula changed,
- `deal-rot-config` went from 14 to 30,
- unknown filters went from silently ignored to a 400 error,
- `Activity.deal_id` went from never populated to 29 rows.

Every run should start with the existing preflight, extended to:
- compare **tool names**, not only the count (the names are now persisted);
- compare `deal-rot-config`, `CRMPreferences.deal_rot_days` and the calibrated boundary, and emit `CHANGED:` lines;
- re-fit the independent rot formula against the platform's `_rot_days` on untouched deals, and emit a `CHANGED:` line if the mismatch count leaves zero.

**Beats best-in-class by** noticing when its own inputs change, rather than drifting silently.

---

## 4. Needs another seat: escalate, don't reach

The brief makes cross-app escalation part of the exercise. The agent should produce the request, not work around the 403.

| Question it would improve | Owner seat | Escalation content |
|---|---|---|
| Last email or meeting with the party | Inbox (10), Calendar (19) | party ids plus a date window; ask for the latest sent/received date |
| Is a cold deal's account also overdue on payment? | AR (2) via EA (28) | party ids of rotting deals |
| Open support tickets on the account | Helpdesk (15) | party ids |
| Contract renewal date | Contract (17) | party ids |

Each is an `AgentEscalation` or a hand-off through the EA seat, recorded with the question, the party ids and the reason. The run report then shows "email recency: requested from seat 10 at 09:02, not yet answered" instead of silently omitting it.

---

## 5. Needs platform work: request it, with evidence

Each item removes a known weakness above. Items already filed are marked with their report id.

| Request | Removes | Status |
|---|---|---|
| stage-entry timestamp or stage-history ledger on `Deal` | `S_stage` proxy, `S_regression` gap (§3.1) | new request |
| `_rot_level` / `_rot_days` as server-side filters | full-book scans | filed (bughunt A.8) |
| `max` in `/aggregate`, or a `last_activity_at` on Party/Deal | the client-side "latest contact" scan | new request |
| `/api/forecast` in the MCP catalogue; treat probability 0 as 0 | the REST-only forecast; 50% weighting of zero | new; file with bug 11 `2ff27d2b` |
| `Activity.completed_at` | contact recency inferred from `due_date` | new request |
| `Activity.delete` for agent-authored rows | the agent cannot clean up its own writes | noted in `open_items.md` |
| probability set by `mark_won` / `mark_lost` | won deals at 0% | filed `2ff27d2b` |
| a changelog or `listChanged` for catalogue changes | silent boundary moves | filed (bug 4, B2) |
| read-only `EmailMessage` / `CalendarEvent` recency for the CRM seat | "not called" versus "not contacted" | new request |

---

## 6. How we will show we exceed best-in-class

These are properties, not tests. The team writes the verifiers by hand; the rubric scores AI-written tests at zero.

| Property | Target | Why a competitor can't easily claim it |
|---|---|---|
| **Recall vs. platform** | every deal the platform flags is in our set | we keep the platform signal as a floor |
| **No self-laundering** | 0 deals leave our report because of our own writes | independent recomputation excludes provenance-marked rows |
| **Evidence coverage** | 100% of rows cite the records behind the verdict | black-box scores do not expose inputs |
| **Honest degradation** | 0 numeric scores built from fewer than 3 real factors | "insufficient evidence" is a first-class output |
| **Refusal correctness** | out-of-seat and unanswerable questions refuse; 0 fabricated numbers | chat products are tuned to answer |
| **Write safety** | 0 duplicate Activities across concurrent runs | re-read guard |
| **Drift awareness** | every platform change in §3.9 raises a `CHANGED:` line on the next run | most agents assume a static API |
| **Efficiency** | full book in a fixed number of calls, under 10 s | measured today: 3 calls, ~9 s for the canonical task |

---

## 7. Order of work

| # | Work | Section | Writes to book? | Effort |
|---|---|---|---|---|
| 1 | Preflight drift checks on tool names, rot config and formula | 3.9 | no | S |
| 2 | Snapshot to disk every run, plus a diff | 3.8 | no | S |
| 3 | Five-factor rot with honest degradation | 3.1 | no | M |
| 4 | Lead coverage and call-versus-touch for goal 2 | 3.2 | no | M |
| 5 | Hygiene auditor, propose-only | 3.4 | no | M |
| 6 | Quote-aware, human-readable next actions | 3.3 | no in propose mode | M |
| 7 | Forecast reconciliation | 3.5 | no | S |
| 8 | Cited Q&A and refusal cases in the loop | 3.7 | no | M |
| 9 | `AgentTask` digest with `AgentMemory` / `AgentTodo` / `AgentEscalation` | 3.6 | yes, agent-owned records | M |
| 10 | Escalation requests to other seats | 4 | yes, `AgentEscalation` | S |
| 11 | Opt-in writes: next actions and mechanical hygiene fixes | 3.3, 3.4 | yes, deal records; one at a time, with consent | M |

Items 1–8 are read-only and safe to run against the shared book at any time. Items 9–11 write to a book other teams share. Run them only with explicit consent, and remember the agent cannot delete what it creates.

---

## 8. Caveats

- Competitor capabilities come from vendor marketing (see gap report §6), not from trials.
- The seat boundary and every count here were measured once, on 2026-09-25. This platform has changed five times in eight days (§3.9), so re-measure before relying on any number.
- The `/api/forecast` weighting of probability 0 as 50% is inferred from two buckets. It needs a deliberate check before being filed as a bug.
- Nothing in this plan has been implemented yet. §6's figures are targets, except the efficiency baseline, which was measured on 2026-09-22.
