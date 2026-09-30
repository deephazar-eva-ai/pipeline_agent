# CRM Gap Fill-up Plan

**Seat:** 07, Pipeline (CRM). **Instances:** Suryodaya (India, GST) and Keystone (US, sales & use tax).
**Date:** 2026-09-30.
**Input:** [`crm_gapreport.md`](crm_gapreport.md), which lists the gaps against Clarify, Monaco and Reevo.
**Question the seat answers:** *"Which deals are rotting, who has not been contacted, and what is the next action on each?"*

This plan takes every gap in the gap report and puts it in one of four places:

1. **Done.** `pipeline_agent` already handles it.
2. **Build now.** `pipeline_agent` can close it with the seat's current tools. No change is needed anywhere else.
3. **Outside `pipeline_agent`.** It needs one of: a platform (AgentSwitch backend) change, access or data held by another seat, a decision by the seat owner, or an external vendor.
4. **Not fillable.** It stays a gap even after items 2 and 3 are done.

**Where the numbers come from.** Counts are taken from the measurements in
[`capstone_capability_enhancement_for_pipelineagent.md`](../docs/capstone_capability_enhancement_for_pipelineagent.md)
(2026-09-25), [`../docs/open_items.md`](../docs/open_items.md) and the Keystone bug hunts
(2026-09-28/29). None were re-measured for this plan. The platform has changed at least five
times in eight days, so check any number before depending on it.

---

## 1. Where the gap report is out of date

The gap report was written on 2026-09-17 against a static snapshot. Since then, live
measurements have disproved several of its assumptions. The plan below uses the measured
facts.

| Gap report said | Measured since | Effect on this plan |
|---|---|---|
| 13 generic tools, including `report(aggregate=max, group_by=…)` | The seat is served 242 **entity-scoped** tools. There is no `report` tool. REST `/aggregate` supports count and sum only | "Last contact per deal" is a client-side scan. This is already built |
| `_rot_level` values are `fresh`/`aging`/`stale` | The values are `fresh`/`attention`/`none`. On 2026-09-25 the platform flagged 0 of 88 deals | The platform's signal can only be a floor. The agent computes its own |
| One rot threshold (`CRMPreferences.deal_rot_days`) | Three sources disagreed (14, 30, and 5–9 days measured). Now 30/30 | The boundary is calibrated on every run. This is already built |
| §3: the agent can reason over Invoice, Ticket and Contract | All three return 403 for this seat | Moved to "other seat" (§4.2) |
| §3: consent-ledgered WhatsApp/SMS outreach | The `channels` app returns 403 | Moved to "access grant" (§4.2) |
| §3: `AgentRunbook` → `ApprovalRequest` governance | Runbooks are readable but hold 0 rows. `approvals` returns 403 | The consent gate lives in the harness. The platform route is a request (§4.1) |
| `Activity.deal_id` links contact to a deal | 29 of 176 activities carry it. All carry `party_id` | The party fallback carries the load. This is already built |

---

## 2. Every gap and where it lands

| # | Gap (gap report section) | Done | Build now | Outside `pipeline_agent` | What is left afterwards |
|---|---|---|---|---|---|
| G1 | Which deals are rotting (§2a) | ✅ Rot is recomputed independently from genuine calls, calibrated, and protected against the agent's own writes resetting it | 5-factor score (B3) | Stage-history ledger (P1) | — |
| G2 | Who has not been contacted (§2a) | ✅ Calls only, by party, with hidden silence detected | Leads in the same answer (B4) | Email and calendar recency (A1) | Until A1 is granted: "no call logged in CRM", never "not contacted" |
| G3 | Next action on each deal (§2a) | ✅ Stage- and quote-aware. Owner worklist. Re-reads before writing. Idempotent | Rationale kept in a `Note` (B6) | Seat owner validates the action templates (D2) | Write mode has run on only 1 deal (D1) |
| G4 | A standing habit, not a one-off query (§2a, §3) | ❌ | `AgentTask` daily digest (B7) | — | — |
| G5 | Escalate the deals that matter (§2a) | ◐ The output carries an `escalations_needed` block | Use `AgentEscalation` on Suryodaya (B7) | Keystone has no assignee to escalate to (P9) | Keystone stays output-only until P9 is fixed |
| G6 | Remember what was already raised (§2a, §3) | ❌ | `AgentMemory` for each party (B7) | — | — |
| G7 | Governed autonomy (§2a, §3) | ◐ Consent is per run, writes are opt-in, and there is a provenance marker | Consent ledger in the run artifacts (B9) | `approvals` access; runbooks (P7) | — |
| G8 | Server-side rot filter (§2b) | Worked around with one full-book scan | — | Platform (P2), already filed | Full-book scans until P2 lands |
| G9 | Structured "next action" field (§2b) | ✅ Uses Activity as the next action | — | Seat-owner decision (D3) | — |
| G10 | Rot-score history, `DealSnapshot` (§2b, §5.3) | ❌ | Snapshots in the run artifacts, and a diff (B2) | Platform `DealSnapshot` table (P1) | History starts on the day of the first snapshot |
| G11 | 5-factor rot score and `rot_comment` (§5.1–5.2) | ❌ | With honest degradation (B3) | Stage-entry dates (P1) | `S_stage` is a proxy until P1 lands |
| G12 | Forecasting (§1) | ◐ `weighted_pipeline` uses stage-default probabilities | Compare with `/api/forecast` (B5) | Put forecast in MCP; fix the probability bugs (P4, P5) | Forecast accuracy needs history (B2 or P1) |
| G13 | Rep coaching and performance (§1) | ◐ `worklist_by_owner` | Owner rollups (B5) | Owner as a reference, not free text (P8). Rep data (A5) | Talk ratio and objection handling need P10 |
| G14 | "Ask about my pipeline" with citations (§1) | ◐ The loop harness and request guard exist | Citation floor and refusal cases (B8) | A real model run (D5) | — |
| G15 | Lead scoring engine (§1, §2b) | ❌ | Score leads in the output only, with the rules stated (B4) | Scoring job and a place to store the score (P6) | No persisted score until P6 |
| G16 | Cross-domain reasoning (§3) | ❌ (403) | Write the escalation requests (B7) | Answers from AR, Helpdesk and Contract seats (A2–A4) | Depends on those seats answering |
| G17 | Omnichannel outreach with consent (§3) | ❌ (403) | — | `channels` access (A6) | — |
| G18 | Call recording and transcripts (§1) | ❌ | Draft call notes from text the user dictates (B10) | A recording/transcription vendor and platform entity (P10) | — |
| G19 | Sentiment and talk-track analysis (§1) | ❌ | Tag the outcome text of logged calls (B10), as advisory only | Needs P10 | Nothing reliable without transcripts |
| G20 | Sequences and cadences (§1) | ❌ | — | Sequence state machine (P11) plus email access (A1) | — |
| G21 | Dialer, inbox warm-up, deliverability (§1) | ❌ | — | External vendor (X1) | Out of scope for this seat |
| G22 | Enrichment, TAM, intent signals (§1) | ❌ | — | Data provider plus platform entities (X2) | Out of scope for this seat |

Legend: ✅ done · ◐ partly done · ❌ not done.

---

## 3. What `pipeline_agent` can build now

None of these items needs a platform change, a new permission or another seat. Items B1–B6
and B8 are read-only and safe to run on the shared book at any time. Items B7, B9 and B10
write agent-owned records and must run only with consent.

**Seat-owner rules that bind every item** (commit `3423e41`, 2026-09-28):
- Editing a record is not contact.
- Only `type == "call"` counts as a call.
- Supplier-only deals are left out.
- The agent does not tidy shared data. Hygiene findings are **proposed, never applied**.

### B1. Wider drift guard (small effort, closes nothing new but protects G1 and G8)
- **Where:** [`preflight.py`](../src/pipeline_agent/preflight.py).
- **What:**
  - Compare the catalogue by **tool name**, not only by count (`RECORDED_TOOL_COUNT = 242`).
  - Stop the run and name the tool if the workflow's own tools are missing (item 4.4 in the Keystone enhancement list, not yet done).
  - Compare `GET /api/deal-rot-config`, `CRMPreferences.deal_rot_days` and the calibrated boundary. Print `CHANGED:` if they move.
  - Re-fit the independent rot formula against `_rot_days` on deals the agent has not touched, and print `CHANGED:` if mismatches appear.
- **Check:** Remove a tool from the stub. The run must stop and name that tool.

### B2. Snapshots on disk and a daily diff (small effort, closes G10 for now)
- **Where:** [`harness/artifacts.py`](../src/pipeline_agent/harness/artifacts.py) and a new `agent/snapshot.py`.
- **What:**
  - Each run writes `snapshot.json`: one row per open deal, using the fields in [`deals_snapshot_schema.json`](deals_snapshot_schema.json) plus `captured_at`.
  - A diff against the previous snapshot gives:
    - **stage regression** (the deal's current stage comes earlier in `Pipeline.stages` than it did before),
    - **close-date pushes**,
    - **time in stage**, reported as a lower bound,
    - **what changed since yesterday**.
- **Limit:** there is no history before the first snapshot. The report must say so.
- **Check:** Each snapshot row matches a `Deal.get` taken in the same run. The snapshot is written before `status.json` is set to completed.

### B3. Five-factor rot score with honest degradation (medium effort, closes G11 and strengthens G1)
- **Where:** [`agent/workflow.py`](../src/pipeline_agent/agent/workflow.py) and [`agent/contract.py`](../src/pipeline_agent/agent/contract.py) (new `rot_score`, `rot_band`, `rot_comment` and `factors` fields on `DealResult`).
- **Formula:** from gap report §5.1. The inputs are taken as follows:

  | Factor | Source now |
  |---|---|
  | `S_activity` | The existing genuine-call age (done calls, not written by the agent) |
  | `S_slippage` | `today − expected_close_date`. On 2026-09-25, 69 of 88 open deals had no close date, so this factor is **missing** for them |
  | `S_value` | Deal value against the 90th percentile of open deals. Deals with value 0 or less are marked unvalued |
  | `S_stage` | A **proxy**: the first snapshot showing the current stage (B2), otherwise `created_at`. Always labelled as a proxy |
  | `S_regression` | From the B2 diff. **Missing** until two snapshots exist |

- **Rules:**
  - A score built from fewer than three real factors is reported as `insufficient_evidence`, not as a number.
  - `rot_comment` names the factor with the largest **weighted** contribution.
  - The platform's verdict and the existing independent verdict stay next to the score. The score adds evidence. It never removes a deal the existing checks flag.
- **Check:** Each factor is recomputed from raw reads. The stated band must follow from those numbers.

### B4. Leads in "who has not been called", and a lead score in the output only (medium effort, closes the G2 lead gap and part of G15)
- **Where:** extend `find_leads_needing_action` in [`agent/pipeline_checks.py`](../src/pipeline_agent/agent/pipeline_checks.py).
- **What:**
  - Classify contact for `Lead` rows through `Lead.party_id` and the same party index as deals.
  - Report "called" and "touched" separately.
  - Rank leads with a transparent rule: status, days since the last call, and whether a qualified lead has no deal yet. Every row shows its inputs.
  - Nothing is written. `CRMPreferences.lead_scoring_enabled` stays untouched.
- **Check:** Each lead reported as uncalled has no done call within the threshold.

### B5. Forecast comparison and owner rollups (small effort, closes more of G12 and G13)
- **What:**
  - Put `weighted_pipeline` next to REST `GET /api/forecast?pipeline=&period=` and list every deal where they disagree, with the reason. Known reasons: probability 0 is weighted at 50%; won deals carry 0–25% probability (Bug 11).
  - Report past-dated forecast buckets as slippage.
  - For each owner, add won value, open value, rotting value and genuine calls in the last 30 days.
  - Unowned or unrecognised owners get their own bucket. On 2026-09-25, 123 of 138 deals had no owner.
- **Check:** Recompute the bucket totals from `Deal.list`. Each reported disagreement must reproduce.

### B6. Rationale in a `Note`, not in the Activity (small effort, improves G3)
- **What:**
  - In write mode, the Activity holds only the action.
  - The reason (rot factors, run id, evidence) goes into `Note.create(deal_id=…)`.
- **First, verify** that a new `Note` does not move `_rot_days`. The platform's rot formula has changed once already.

### B7. Standing habit: task, memory, escalation (medium effort, closes G4, G6 and G16, and G5 on Suryodaya)
- **What:**
  - An `AgentTask` on a cron schedule (daily at 09:00, in each company's own timezone) runs the canonical task in propose mode and produces a digest.
  - `AgentMemory` for each party (category `relationship`, with an expiry) records "flagged on this date because of this". The next digest then says "still unowned, day 3" instead of repeating the same finding.
  - `AgentTodo` for decisions only a human can make: re-quote, mark lost, assign an owner.
  - `AgentEscalation` (needs a `session_id`) for high-value deals past their close date. It works on Suryodaya. On Keystone it stays an output block because no assignee exists (P9).
  - Cross-seat questions go out as escalations naming the seat, the party ids and the question (A1–A4). The report then says "email recency: requested from seat 10, not yet answered" instead of leaving the line out.
- **Risk:** 52% of scheduled runs on this tenant ran under personas with filler prompts (bug 9). After the first run, read `AgentTask.get` and `AgentSession.get` and confirm the task ran under this seat's persona.
- **Needs consent (D1).** These are writes, and the agent cannot delete what it creates.

### B8. Cited pipeline Q&A with a refusal floor (medium effort, closes G14 on the agent side)
- **Where:** [`harness/loop.py`](../src/pipeline_agent/harness/loop.py) and [`agent/request_guard.py`](../src/pipeline_agent/agent/request_guard.py).
- **What:**
  - Every number in an answer must point to a step recorded in `TaskRun.steps`. A number with no step behind it is marked unsupported.
  - The agent refuses questions that need data outside the seat (Invoice, Contract, Ticket) and names the seat that owns that data.
  - It also refuses questions no data can answer, such as "days in the current stage" before B2 has history.
- **Note:** The mandatory refusal task and its test cases must be written by a human (D4).

### B9. Consent ledger in the harness (small effort, part of G7)
- **What:**
  - Every write-mode run records who consented, the cap, the deal ids and each record created.
  - This file sits next to `result.json`.
  - It stands in for `ApprovalRequest` until P7.

### B10. Call notes from dictation (medium effort, partly closes G18 and G19 without a platform change)
- **What:**
  - Extend `--task log-contact` so the user's own words about a call go into the `endpoint.crm` call-note draft tools (sources, drafts, then approve).
  - The draft carries citations back to the logged Activity.
  - An advisory sentiment or objection tag may be set from the outcome text, labelled as model-inferred.
- **Limit:** This is not call recording. It only structures what a human reports.

### Build order

| Order | Item | Writes to the book? | Effort |
|---|---|---|---|
| 1 | B1 drift guard | no | S |
| 2 | B2 snapshots and diff | no (disk only) | S |
| 3 | B3 five-factor score | no | M |
| 4 | B4 leads and lead score | no | M |
| 5 | B5 forecast and owner rollups | no | S |
| 6 | B8 citations and refusals | no | M |
| 7 | B9 consent ledger | no (disk only) | S |
| 8 | B6 rationale `Note` | yes, with consent | S |
| 9 | B7 `AgentTask`, memory, escalation | yes, agent-owned records | M |
| 10 | B10 call-note drafts | yes, drafts | M |

---

## 4. What must happen outside `pipeline_agent`

### 4.1 Platform (AgentSwitch backend) changes

"Filed" means a bug report already exists: see
[`../bugs/mybugs_all_2026-09-30.json`](../bugs/mybugs_all_2026-09-30.json) (69 reports) and
`EAG_V3_capstone/docnotshared/`. "New" means a feature request that still has to be written.

| # | Change | Gaps it fills | Agent-side workaround until then | Status |
|---|---|---|---|---|
| P1 | A stage-entry timestamp on `Deal`, or a stage-history ledger. The `DealSnapshot` table from gap report §5.3 would serve | G10, G11 (`S_stage`, `S_regression`), G12 accuracy | B2 snapshots | new |
| P2 | `_rot_level` and `_rot_days` as server-side filters on `Deal.list` | G8 | Full-book scan | filed (bughunt A.8) |
| P3 | `max` in `/aggregate`, or `last_contacted_at` / `last_call_at` on Party and Deal | G2 at scale | Client-side scan of Activity | new |
| P4 | `/api/forecast` in the MCP catalogue, with probability 0 counted as 0 | G12 | REST call (B5) | new; send with Bug 11 |
| P5 | `mark_won` / `mark_lost` set probability to 100 / 0 and store a close date | G12, "when did we win?" | Stage-default probabilities; `updated_at` labelled approximate | filed (Bug 11, P1) |
| P6 | A lead-scoring job and a score field, so `lead_scoring_enabled` actually does something | G15 | Output-only score (B4) | new |
| P7 | `approvals` access for this seat, or runbooks agents can register | G7 | Consent ledger (B9) | new |
| P8 | `owner` / `assigned_to` as a reference to a user, not free text | G13 | "Unrecognised owner" bucket | filed (P4) |
| P9 | Keystone escalations need an assignable person | G5 on Keystone | Output block | filed (G9) |
| P10 | Call recording and transcript entities feeding `CallNoteDraft` | G18, G19 | Dictated notes (B10) | new; needs a vendor (X1) |
| P11 | A sequence/cadence entity and state machine | G20 | none | new |
| P12 | `Activity.completed_at`, and backdating a logged contact | G2 accuracy | Real date written into `outcome` (already built, PB1) | new / filed (PB1) |
| P13 | `Activity.delete` for rows the agent wrote | G3 at scale (cleanup) | Idempotency guard and provenance marker | new |
| P14 | A changelog or `listChanged` notice when the catalogue changes | Keeps G1–G3 stable | Drift guard (B1) | filed (bug 4, B2) |
| P15 | Fix `_rot_days` so the agent's own writes do not reset it on Keystone (K1) | G1 trust | Independent recomputation (built) | filed (K1) |

**Priority for the platform team:** P1, P3 and P5 remove the biggest estimates in the agent's
answer. P13 is what makes write mode safe to run at scale.

### 4.2 Access, or answers from other seats

The brief treats cross-app escalation as part of the exercise. The agent writes the request
(B7). It does not try to get around the 403.

| # | Needed | Owner | Gap | Until then |
|---|---|---|---|---|
| A1 | Last email and meeting date per party (read-only `EmailMessage` / `CalendarEvent` recency) | Inbox seat (10), Calendar seat (19), or a platform grant | G2, G20 | "No call in CRM" wording; escalation request |
| A2 | Overdue payments on accounts with rotting deals | AR seat (2), through EA (28) | G16 | Escalation request |
| A3 | Open support tickets per party | Helpdesk seat (15) | G16 | Escalation request |
| A4 | Contract renewal dates | Contract seat (17) | G16 | Escalation request |
| A5 | Rep directory (`Employee`) | Payroll / HR, or a platform grant | G13 | Owner kept as free text |
| A6 | `channels` access (`ChannelConversation`, `ChannelConsent`, `ChannelSendDecision`) | Platform grant | G17 | None. The agent proposes, and a human sends |

### 4.3 Decisions and work for the team or seat owner

| # | Decision | Why the agent cannot make it |
|---|---|---|
| D1 | Consent to run write mode beyond one deal: the cap, which deals, and a clean-up plan | The book is shared and the agent cannot delete its own rows (P13) |
| D2 | Confirm `STAGE_ACTION_TEMPLATES`, `NEXT_ACTION_DUE_DAYS = 3` and the type of action for each stage | These are guesses about how each company sells |
| D3 | Accept "Activity is the next action" as the pattern (G9), or ask for P1-style fields | Product choice |
| D4 | Write the scored task matrix, the refusal task and the verifiers by hand | The rubric scores AI-written tests at zero |
| D5 | Provide a model credential and run `--task loop` against a real model | No real API call has been made yet |
| D6 | Rule on the party-level fallback: one open activity on a party currently counts as "actioned" for all of that party's deals | This can hide a deal when one customer has several |

### 4.4 External vendors (outside this project's scope)

| # | Need | Gaps |
|---|---|---|
| X1 | Dialer, call recording and transcription vendor. Inbox warm-up and deliverability | G18, G19, G21 |
| X2 | Company and people enrichment data, TAM lists, intent signals | G22 |

---

## 5. What is still missing after this plan

- **If only section 3 is done:** G1–G7, G10, G11 and G14 are closed or closed for now. G12,
  G13 and G15 are partly closed. G2 is limited to calls, because email and meetings cannot
  be seen. History only goes back to the first snapshot.
- **If section 4 is also done:** everything except G21 and G22 is closed. Those two depend
  on external data and belong to the platform's product roadmap, not to this seat.
- **What does not change:** the seat competes on **trustworthy answers**, not on reach.
  Every verdict carries its evidence. The agent's own writes never hide a problem. It
  refuses when the data cannot support an answer. The competitors win on data sources
  (G17–G22), and this plan does not claim to match them there.
